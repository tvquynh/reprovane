"""One bounded, streamed, public-IP-pinned transport for metadata and direct PDFs.

No requests session, ambient proxies, netrc, cookies or browser state is used.
TLS connects to the validated IP with original-host SNI/certificate validation.
"""
import os
import time
import math
from urllib.parse import urlsplit,urljoin,urlencode
import urllib3
from ..core import ValidationError
from ..openalex_acquisition.credentials import _user_environment_key
from .safety import public_address,sanitize_url


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


class FetchError(Exception):
    def __init__(self,reason,retryable=False): self.reason=reason; self.retryable=retryable; super().__init__(reason)


class SafeTransport:
    def __init__(self,policy,*,credentials=None,resolver=None,opener=None,sleeper=time.sleep):
        self.p=policy; self.key,self.email=credentials or ('',''); self.resolver=resolver
        self.opener=opener or self._open; self.sleep=sleeper

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

    def fetch(self,url,*,purpose,max_bytes,reserve,complete,provider=None):
        origin=urlsplit(url).hostname; current=url; redirects=[]; retry=0; total_retry=0
        while True:
            try:
                normalized,address=public_address(current,self.p['allowed_schemes'],**({'resolver':self.resolver} if self.resolver else {}))
            except ValidationError: raise FetchError('UNSAFE_URL') from None
            headers={'User-Agent':'scholarflow-fulltext/1','Accept-Encoding':'identity'}
            host=urlsplit(normalized).hostname
            if host==origin and host in ('api.openalex.org','content.openalex.org') and self.key:
                headers['Authorization']='Bearer '+self.key
            retrieval=normalized
            if provider=='UNPAYWALL':
                if not self.email: raise FetchError('RUNTIME_EMAIL_REQUIRED')
                if host!=origin or host!='api.unpaywall.org': raise FetchError('AUTH_REDIRECT_REFUSED')
                retrieval += ('&' if urlsplit(retrieval).query else '?')+urlencode({'email':self.email})
            ticket,limit=reserve(sanitize_url(normalized),purpose,max_bytes)
            response=None; size=0; status=None; chunks=[]; failure=None; retryable=False; subset={}; started=time.monotonic()
            try:
                response=self.opener(retrieval,address,headers,self.p['request_timeout']); status=response.status
                subset={k.lower():str(v) for k,v in response.headers.items() if k.lower() in ('content-type','content-length','retry-after')}
                if status in (301,302,303,307,308):
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
                    for chunk in response.stream(amt=min(65536,limit+1),decode_content=False):
                        if time.monotonic()-started>self.p['request_timeout']: raise FetchError('REQUEST_TIMEOUT',True)
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
            except FetchError as exc: failure=exc.reason; retryable=exc.retryable
            except ValidationError: failure='UNSAFE_URL'
            except (OSError,urllib3.exceptions.HTTPError): failure='TRANSPORT_ERROR'; retryable=True
            finally:
                try: complete(ticket,dict(http_status=status,bytes=size,failure_class=failure,headers=subset,final_url=sanitize_url(normalized),redirects=list(redirects),retry_count=total_retry))
                finally:
                    if response is not None:
                        response.close()
                        if hasattr(response,'_fulltext_pool'): response._fulltext_pool.close()
            if failure:
                if not retryable or retry>=self.p['retry_count']: raise FetchError(failure,retryable)
                delay=min(self.p['retry_backoff']*2**retry,self.p['max_retry_delay'])
                try:
                    requested=float(subset.get('retry-after',delay))
                    if math.isfinite(requested) and requested>=0: delay=min(requested,self.p['max_retry_delay'])
                except ValueError: pass
                self.sleep(delay); retry+=1; total_retry+=1
