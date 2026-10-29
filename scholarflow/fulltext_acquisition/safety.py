"""Conservative URLs, persistence redaction, pinned public DNS, PDF integrity only."""
import hashlib
import io
import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit, unquote
from pypdf import PdfReader
from ..core import ValidationError
from ..openalex_http import PrivateValues

SENSITIVE = re.compile(r'(?i)(key|token|signature|credential|password|email|auth|secret|expires|x-amz|x-goog|policy|session|^sig$)')


def normalize_url(url):
    try:
        p=urlsplit(url)
        if p.scheme.lower() not in ('http','https') or not p.hostname or p.username or p.password:
            raise ValueError()
        if any(ord(c)<33 or c=='\\' for c in url): raise ValueError()
        host=p.hostname.lower().encode('idna').decode('ascii')
        port=p.port
        if port not in (None,80,443): raise ValueError()
        if ':' in host: host='['+host+']'
        netloc=host+((':'+str(port)) if port and port != (443 if p.scheme.lower()=='https' else 80) else '')
        return urlunsplit((p.scheme.lower(),netloc,p.path,p.query,''))
    except (ValueError,TypeError,UnicodeError): raise ValidationError('Unsafe or malformed HTTP URL') from None


def sanitize_url(url):
    value=normalize_url(url); p=urlsplit(value); parts=[]
    for part in p.query.split('&') if p.query else []:
        key,sep,val=part.partition('=')
        parts.append(key+sep+('REDACTED' if SENSITIVE.search(unquote(key)) or '@' in unquote(val) else val))
    return urlunsplit((p.scheme,p.netloc,p.path,'&'.join(parts),''))


def sanitize(value):
    if isinstance(value,dict):
        return {k: ('REDACTED' if SENSITIVE.search(k) and k.lower() not in ('license','license_id') else sanitize(v)) for k,v in value.items()}
    if isinstance(value,list): return [sanitize(v) for v in value]
    if isinstance(value,str):
        if value.startswith(('http://','https://')):
            try: return sanitize_url(value)
            except ValidationError: return 'UNSAFE_URL'
        if re.search(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}',value): return 'REDACTED'
    return value


def public_address(url, schemes, resolver=socket.getaddrinfo):
    value=normalize_url(url); p=urlsplit(value)
    if p.scheme not in schemes or p.hostname.rstrip('.').lower() in ('localhost','metadata.google.internal'):
        raise ValidationError('Disallowed network target')
    try:
        try: literal=ipaddress.ip_address(p.hostname)
        except ValueError: literal=None
        if literal is not None and (not literal.is_global or literal.is_multicast): raise ValueError()
        addresses=sorted({r[4][0] for r in resolver(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)})
        if not addresses or any(not ipaddress.ip_address(a).is_global or ipaddress.ip_address(a).is_multicast for a in addresses):
            raise ValueError()
    except (OSError,ValueError): raise ValidationError('Target must resolve exclusively to public addresses') from None
    return value,addresses[0]


def validate_pdf(body, policy):
    if not policy['min_pdf_bytes'] <= len(body) <= policy['max_pdf_bytes']:
        raise ValidationError('PDF_SIZE_INVALID')
    if not body.startswith(b'%PDF-') or not body.rstrip().endswith(b'%%EOF'):
        raise ValidationError('PDF_VALIDATION_FAILED')
    # This parser checks structure/pages only. It never extracts body text or runs OCR.
    try:
        reader=PdfReader(io.BytesIO(body),strict=True)
        if reader.is_encrypted or len(reader.pages)<1: raise ValueError()
        pages=len(reader.pages)
    except Exception: raise ValidationError('PDF_VALIDATION_FAILED') from None
    return dict(format='PDF',media_type='application/pdf',byte_size=len(body),pages=pages,
                validation='PYPDF_STRICT_PAGES',artifact_sha256=hashlib.sha256(body).hexdigest())


def guard(value, private=()):
    try: PrivateValues(*private).check(value)
    except Exception: raise ValidationError('Private runtime value; persistence refused') from None
