"""One bounded, streamed, public-IP-pinned transport for metadata and direct PDFs.

No requests session, ambient proxies, netrc, cookies or browser state is used.
TLS connects to the validated IP with original-host SNI/certificate validation.
"""
import os
import re
import time
import math
from urllib.parse import urlsplit,urljoin,urlencode
import urllib3
from ..core import ValidationError
from ..openalex_acquisition.credentials import _user_environment_key
from .safety import ResolutionError,public_address,sanitize_url


def runtime_credentials():
    key=os.environ.get('OPENALEX_API_KEY','').strip() or _user_environment_key()
    email=os.environ.get('UNPAYWALL_EMAIL','').strip()
    if not email and os.name=='nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as handle:
                email=winreg.QueryValueEx(handle,'UNPAYWALL_EMAIL')[0].strip()
        except OSError: pass
    return key,email


_HTTP_DATE=re.compile(r'[A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT')


KNOWN_MEDIA=frozenset(('application/pdf','application/x-pdf','application/octet-stream','binary/octet-stream','text/html',
    'text/plain','text/xml','application/xml','application/xhtml+xml','application/json'))


def query_secrets(url):
    """Values of the query parameters sanitize_url redacts (and e-mail-like values) of one request URL."""
    from urllib.parse import parse_qsl,unquote
    from .safety import SENSITIVE
    return {v for k,v in parse_qsl(urlsplit(url).query,keep_blank_values=False) if SENSITIVE.search(unquote(k)) or '@' in v}


def retained_headers(headers,secrets=()):
    """Persisted copies of response headers that a remote server cannot use to plant text in the ledger.

    The media type comes from a fixed vocabulary (otherwise OTHER); lengths and Retry-After keep their grammar and are
    REDACTED when a token equals, or the value contains, a sensitive value of any request hop or a credential.
    """
    secrets={s for s in secrets if s}
    def clean(value):
        tokens=set(re.split(r'[\s,:]+',value))
        return 'REDACTED' if tokens & secrets or any(len(s)>=4 and s in value for s in secrets) else value
    out={}
    if 'content-type' in headers:
        media=headers['content-type'].split(';',1)[0].strip().lower()
        out['content-type']=media if media in KNOWN_MEDIA else 'OTHER'
    if 'content-length' in headers:
        value=headers['content-length'].strip()
        out['content-length']=clean(value) if re.fullmatch(r'\d{1,20}',value) else 'UNPARSEABLE'
    if 'retry-after' in headers:
        value=headers['retry-after'].strip()
        out['retry-after']=clean(value) if re.fullmatch(r'\d{1,10}',value) or _HTTP_DATE.fullmatch(value) else 'UNPARSEABLE'
    return out


class FetchError(Exception):
    # terminal=True stops in-transport retries even for a retryable class (the caller defers it).
    def __init__(self,reason,retryable=False,terminal=False): self.reason=reason; self.retryable=retryable; self.terminal=terminal; super().__init__(reason)


REDIRECT_STATUSES=(301,302,303,307,308)


class SafeTransport:
    """Shared by Stage11B review acquisition and unified discovery acquisition.

    Optional hooks keep Stage11B behaviour byte-identical when unused:
    gate(url) runs before every connection (initial URL, each redirect hop and each retry) and may raise
    FetchError; inspect(status, headers, head, url) sees each response with a bounded body prefix before
    generic status handling and may raise FetchError.
    """
    def __init__(self,policy,*,credentials=None,resolver=None,opener=None,sleeper=time.sleep,user_agent='reprovane-fulltext/1',extra_headers=None):
        self.p=policy; self.key,self.email=credentials or ('',''); self.resolver=resolver
        self.opener=opener or self._open; self.sleep=sleeper; self.user_agent=user_agent
        self.extra_headers={k:v for k,v in (extra_headers or {}).items() if k.lower() not in ('authorization','cookie','host','accept-encoding')}

    @staticmethod
    def _open(url,address,headers,timeout):
        p=urlsplit(url); port=p.port or (443 if p.scheme=='https' else 80)
        if p.scheme=='https':
            pool=urllib3.HTTPSConnectionPool(address,port,server_hostname=p.hostname,
                    assert_hostname=p.hostname,cert_reqs='CERT_REQUIRED',maxsize=1)
        else: pool=urllib3.HTTPConnectionPool(address,port,maxsize=1)
        try:
            response=pool.urlopen('GET',p.path or '/' if not p.query else (p.path or '/')+'?'+p.query,
                    headers=dict(headers,Host=p.netloc),timeout=urllib3.Timeout(connect=timeout,read=timeout),
                    retries=False,redirect=False,preload_content=False,decode_content=False)
            response._fulltext_pool=pool
            return response
        except BaseException: pool.close(); raise

    def fetch(self,url,*,purpose,max_bytes,reserve,complete,provider=None,gate=None,inspect=None):
        origin=urlsplit(url).hostname; current=url; redirects=[]; retry=0; total_retry=0; hop_secrets={self.key,self.email}
        while True:
            hooked=gate is not None or inspect is not None
            try:
                normalized,address=public_address(current,self.p['allowed_schemes'],**({'resolver':self.resolver} if self.resolver else {}))
            except ResolutionError:
                # Refused either way; hook callers (unified acquisition) treat a failed lookup as temporary.
                raise FetchError('DNS_UNAVAILABLE' if hooked else 'UNSAFE_URL',hooked) from None
            except ValidationError: raise FetchError('UNSAFE_URL') from None
            if hooked: hop_secrets|=query_secrets(normalized)
            if gate is not None: gate(normalized)
            headers={**self.extra_headers,'User-Agent':self.user_agent,'Accept-Encoding':'identity'}
            host=urlsplit(normalized).hostname
            if host==origin and host in ('api.openalex.org','content.openalex.org') and self.key:
                headers['Authorization']='Bearer '+self.key
            retrieval=normalized
            if provider=='UNPAYWALL':
                if not self.email: raise FetchError('RUNTIME_EMAIL_REQUIRED')
                if host!=origin or host!='api.unpaywall.org': raise FetchError('AUTH_REDIRECT_REFUSED')
                retrieval += ('&' if urlsplit(retrieval).query else '?')+urlencode({'email':self.email})
            ticket,limit=reserve(sanitize_url(normalized),purpose,max_bytes)
            response=None; size=0; status=None; chunks=[]; failure=None; retryable=False; terminal=False; subset={}; started=time.monotonic(); stream=None
            try:
                response=self.opener(retrieval,address,headers,self.p['request_timeout']); status=response.status
                subset={k.lower():str(v) for k,v in response.headers.items() if k.lower() in ('content-type','content-length','retry-after')}
                if hooked: subset=retained_headers(subset,hop_secrets)
                if inspect is not None:
                    head=b''
                    if status not in REDIRECT_STATUSES:
                        stream=iter(response.stream(amt=min(65536,limit+1),decode_content=False))
                        while len(head)<65536 and len(head)<=limit:
                            chunk=next(stream,None)
                            if chunk is None: break
                            size+=len(chunk)  # every consumed byte is charged, also when the chunk is then refused
                            # The prefix read obeys the same elapsed-time bound as the body stream.
                            if time.monotonic()-started>self.p['request_timeout']: raise FetchError('REQUEST_TIMEOUT',True)
                            head+=chunk
                        chunks.append(head)
                    seen={k.lower():str(v) for k,v in response.headers.items()}
                    if 'retry-after' in seen:
                        # A directive that repeats a sensitive value must not be echoed into any record.
                        masked=retained_headers({'retry-after':seen['retry-after']},hop_secrets).get('retry-after')
                        if masked=='REDACTED': seen['retry-after']='REDACTED'
                    inspect(status,seen,head,normalized)
                if status in REDIRECT_STATUSES:
                    target=urljoin(normalized,response.headers.get('Location',''))
                    if not response.headers.get('Location') or len(redirects)>=self.p['max_redirects']: raise FetchError('REDIRECT_LIMIT')
                    if urlsplit(normalized).scheme=='https' and urlsplit(target).scheme=='http' and not self.p['allow_https_downgrade']:
                        raise FetchError('HTTPS_DOWNGRADE')
                    # Safety is also checked before every subsequent socket connection.
                    public_address(target,self.p['allowed_schemes'],**({'resolver':self.resolver} if self.resolver else {}))
                    redirects.append(sanitize_url(target)); current=target
                elif status==429 or 500<=status<=599: raise FetchError('RATE_LIMITED' if status==429 else 'SERVER_ERROR',True)
                elif status in (401,403): raise FetchError('AUTH_REQUIRED')
                elif status!=200: raise FetchError('HTTP_'+str(status))
                else:
                    if response.headers.get('Content-Encoding','identity').lower() not in ('','identity'):
                        raise FetchError('ENCODING_REFUSED')
                    length=response.headers.get('Content-Length')
                    if length is not None:
                        try: length=int(length)
                        except ValueError: raise FetchError('INVALID_LENGTH') from None
                        if length<0 or length>limit: raise FetchError('MAX_BYTES')
                    if size>limit: raise FetchError('MAX_BYTES')
                    for chunk in (stream if stream is not None else response.stream(amt=min(65536,limit+1),decode_content=False)):
                        if time.monotonic()-started>self.p['request_timeout']:
                            if hooked: size+=len(chunk)
                            raise FetchError('REQUEST_TIMEOUT',True)
                        size+=len(chunk)
                        if size>limit: raise FetchError('MAX_BYTES')
                        chunks.append(chunk)
                    if length is not None and length!=size: raise FetchError('TRUNCATED_BODY',True)
                    body=b''.join(chunks)
                    if purpose=='PDF' and not body.startswith(b'%PDF-'):
                        text=body[:8192].lower()
                        if any(s in text for s in (b'captcha',b'cloudflare',b'javascript challenge')): raise FetchError('ANTI_BOT_BLOCKED')
                        if any(s in text for s in (b'login',b'sign in',b'payment')): raise FetchError('AUTH_REQUIRED')
                    return body,dict(http_status=status,final_url=sanitize_url(normalized),redirects=redirects,
                        content_type=subset.get('content-type'),content_length=subset.get('content-length'),headers=subset,retry_count=total_retry,bytes=size)
            except FetchError as exc: failure=exc.reason; retryable=exc.retryable; terminal=exc.terminal
            except ResolutionError:
                failure='DNS_UNAVAILABLE' if hooked else 'UNSAFE_URL'; retryable=hooked
            except ValidationError: failure='UNSAFE_URL'
            except (OSError,urllib3.exceptions.HTTPError): failure='TRANSPORT_ERROR'; retryable=True
            finally:
                try: complete(ticket,dict(http_status=status,bytes=size,failure_class=failure,headers=subset,final_url=sanitize_url(normalized),redirects=list(redirects),retry_count=total_retry))
                finally:
                    if response is not None:
                        response.close()
                        if hasattr(response,'_fulltext_pool'): response._fulltext_pool.close()
            if failure:
                if not retryable or terminal or retry>=self.p['retry_count']: raise FetchError(failure,retryable,terminal)
                delay=min(self.p['retry_backoff']*2**retry,self.p['max_retry_delay'])
                try:
                    requested=float(subset.get('retry-after',delay))
                    if math.isfinite(requested) and requested>=0: delay=min(requested,self.p['max_retry_delay'])
                except ValueError: pass
                self.sleep(delay); retry+=1; total_retry+=1
