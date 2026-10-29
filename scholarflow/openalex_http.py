"""One byte-preserving HTTP implementation shared by legacy and persisted acquisition."""
import hashlib,json,math,time
from dataclasses import dataclass
from urllib.parse import quote
import requests


class OpenAlexHTTPError(RuntimeError): pass
class OpenAlexPayloadError(RuntimeError): pass


class PrivateValues:
    def __init__(self,*values): self._values=tuple(v for v in values if isinstance(v,str) and v)
    def __repr__(self): return '<runtime-only private-value guard>'
    def check(self,value):
        if isinstance(value,bytes): text=value.decode('utf-8',errors='replace')
        elif isinstance(value,str): text=value
        else: text=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
        for private in self._values:
            if private in text or quote(private,safe='') in text:
                raise OpenAlexPayloadError('Private runtime value detected; persistence refused')


@dataclass(frozen=True)
class OpenAlexPage:
    body: bytes
    data: dict
    rate: dict
    status: int = 200


def request_page(session,url,params,*,private,headers,timeout,max_attempts,max_bytes,sleeper=None,max_sleep=10):
    if any(type(v) is not int or v<1 for v in [max_attempts,max_bytes]) or max_attempts>10: raise ValueError('Invalid HTTP bounds')
    sleep=sleeper if sleeper is not None else time.sleep
    for attempt in range(1,max_attempts+1):
        response=None; delay=min(2**(attempt-1),max_sleep)
        try:
            response=session.get(url,params=params,headers=headers,timeout=timeout,stream=True,allow_redirects=False)
            status=response.status_code
            if status==429 or status in (500,502,503,504):
                if attempt==max_attempts: raise OpenAlexHTTPError(f'OpenAlex transient HTTP {status}; attempts={attempt}')
                try:
                    requested=float(response.headers.get('Retry-After',delay))
                    if math.isfinite(requested) and requested>=0: delay=min(requested,max_sleep)
                except (ValueError,TypeError): pass
            elif status!=200: raise OpenAlexHTTPError(f'OpenAlex HTTP {status}; not retried')
            else:
                chunks=[]; size=0
                length=response.headers.get('Content-Length')
                if length is not None:
                    try: length=int(length)
                    except (ValueError,TypeError): raise OpenAlexPayloadError('Invalid response size header') from None
                    if length<0 or length>max_bytes: raise OpenAlexPayloadError('OpenAlex page exceeds byte limit')
                for chunk in response.iter_content(chunk_size=64*1024):
                    if not chunk: continue
                    size+=len(chunk)
                    if size>max_bytes: raise OpenAlexPayloadError('OpenAlex page exceeds byte limit')
                    chunks.append(chunk)
                if length is not None and not response.headers.get('Content-Encoding') and size!=length: raise requests.exceptions.ChunkedEncodingError()
                body=b''.join(chunks); private.check(body)
                try:
                    data=json.loads(body,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
                    if not isinstance(data,dict): raise ValueError()
                except (ValueError,UnicodeError): raise OpenAlexPayloadError('OpenAlex page is not a JSON object') from None
                private.check(data)
                rate={}
                for name in ['X-RateLimit-Limit','X-RateLimit-Remaining','X-RateLimit-Credits-Used','X-RateLimit-Reset']:
                    value=response.headers.get(name)
                    try:
                        number=float(value)
                        if math.isfinite(number) and number>=0: rate[name.lower()]=number
                    except (ValueError,TypeError): pass
                return OpenAlexPage(body,data,rate)
        except requests.exceptions.SSLError:
            raise OpenAlexHTTPError('OpenAlex TLS validation failed') from None
        except (requests.ConnectionError,requests.Timeout,requests.exceptions.ChunkedEncodingError):
            if attempt==max_attempts: raise requests.RequestException('OpenAlex transport failed after bounded attempts') from None
        except requests.RequestException:
            raise requests.RequestException('OpenAlex request failed') from None
        finally:
            if response is not None: response.close()
        sleep(delay)
