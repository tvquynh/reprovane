"""HTTPS-only, anonymous, bounded requests transport; redirects fail closed."""
import hashlib
import io
import json
import math
import time
from urllib.parse import quote
import requests
from .models import (repository,ref,commit,utc_now,ResolvedGitHubRef,MirrorSyncError,
    GitHubFetchError,GitHubResolutionError)


class GitHubClient:
    def __init__(self,*,session=None,connect_timeout=10,read_timeout=60,max_file_bytes=256*1024*1024,
                 max_control_bytes=1024*1024,max_attempts=3,max_retry_sleep=10,sleeper=time.sleep):
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in [connect_timeout,read_timeout,max_retry_sleep]) or any(type(v) is not int or v<1 for v in [max_file_bytes,max_control_bytes,max_attempts]) or max_attempts>10:
            raise MirrorSyncError('Invalid transport limits')
        self.session=session if session is not None else requests.Session()
        self.session.trust_env=False  # No environment proxies or implicit netrc credentials.
        self.session.auth=None
        self.timeout=(connect_timeout,read_timeout); self.max_file_bytes=max_file_bytes
        self.max_control_bytes=max_control_bytes; self.max_attempts=max_attempts
        self.max_retry_sleep=max_retry_sleep; self.sleeper=sleeper

    def close(self): self.session.close()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()

    def _pause(self,attempt,header=None):
        try: delay=float(header) if header is not None else 2**(attempt-1)
        except (ValueError,TypeError): delay=2**(attempt-1)
        if not math.isfinite(delay) or delay<0: delay=2**(attempt-1)
        self.sleeper(min(delay,self.max_retry_sleep))

    def _download(self,url,endpoint,limit,destination=None):
        for attempt in range(1,self.max_attempts+1):
            response=None; retry=None
            try:
                response=self.session.get(url,timeout=self.timeout,stream=True,allow_redirects=False,
                    headers={'User-Agent':'reprovane-pinned-mirror/1','Accept-Encoding':'identity','Accept':'application/vnd.github+json' if endpoint=='ref' else 'application/octet-stream'})
                status=response.status_code
                if status in (429,500,502,503,504):
                    if attempt==self.max_attempts: raise GitHubFetchError(f'GitHub {endpoint} transient HTTP {status}; attempts={attempt}')
                    retry=response.headers.get('Retry-After')
                elif status!=200: raise GitHubFetchError(f'GitHub {endpoint} HTTP {status}; attempt={attempt}')
                else:
                    length=response.headers.get('Content-Length')
                    if length is not None:
                        try: length=int(length)
                        except (ValueError,TypeError): raise GitHubFetchError('Invalid GitHub content length') from None
                        if length<0 or length>limit: raise GitHubFetchError('GitHub response exceeds byte limit')
                    stream=io.BytesIO() if destination is None else open(destination,'wb')
                    try:
                        count=0; digest=hashlib.sha256()
                        for chunk in response.iter_content(chunk_size=64*1024):
                            if not chunk: continue
                            count+=len(chunk)
                            if count>limit: raise GitHubFetchError('GitHub response exceeds byte limit')
                            digest.update(chunk); stream.write(chunk)
                        if length is not None and not response.headers.get('Content-Encoding') and count!=length: raise requests.exceptions.ChunkedEncodingError()
                        if destination is None: return stream.getvalue()
                        stream.flush()
                        import os
                        os.fsync(stream.fileno())
                        return digest.hexdigest(),count
                    finally: stream.close()
            except requests.exceptions.SSLError:
                raise GitHubFetchError(f'GitHub {endpoint} TLS validation failed') from None
            except (requests.ConnectionError,requests.Timeout,requests.exceptions.ChunkedEncodingError):
                if attempt==self.max_attempts: raise GitHubFetchError(f'GitHub {endpoint} transport failed; attempts={attempt}') from None
            except requests.RequestException:
                raise GitHubFetchError(f'GitHub {endpoint} request failed') from None
            finally:
                if response is not None: response.close()
            self._pause(attempt,retry)

    def resolve_ref(self,repo,requested_ref):
        repo=repository(repo); requested_ref=ref(requested_ref)
        url='https://api.github.com/repos/'+repo+'/commits/'+quote(requested_ref,safe='')
        try:
            body=self._download(url,'ref',self.max_control_bytes)
            resolved=commit(json.loads(body)['sha'])
            if len(requested_ref)==40 and all(c in '0123456789abcdefABCDEF' for c in requested_ref) and resolved!=requested_ref.lower():
                raise GitHubResolutionError('Requested immutable commit differs from resolved commit')
        except (ValueError,TypeError,KeyError,UnicodeError,MirrorSyncError):
            raise GitHubResolutionError('GitHub ref resolution failed or returned an invalid commit') from None
        return ResolvedGitHubRef(repo,requested_ref,resolved,utc_now())

    def _raw_url(self,repo,sha,path):
        from .models import safe_path
        return 'https://raw.githubusercontent.com/'+repository(repo)+'/'+commit(sha)+'/'+quote(safe_path(path),safe='/')

    def fetch_bytes_at_commit(self,repo,sha,path,*,control=False):
        return self._download(self._raw_url(repo,sha,path),'file',self.max_control_bytes if control else self.max_file_bytes)

    def fetch_to_file(self,repo,sha,path,destination):
        return self._download(self._raw_url(repo,sha,path),'file',self.max_file_bytes,destination)

    def fetch_to_object_cache(self,cache,repo,sha,path,expected_sha256):
        with cache.temporary_object() as temporary:
            actual,size=self.fetch_to_file(repo,sha,path,temporary)
            cache.promote_object(temporary,expected_sha256)
            return actual,size
