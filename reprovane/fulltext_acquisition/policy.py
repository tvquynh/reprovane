"""Explicit, immutable acquisition semantics. No credential resolution here."""
import json
import math
from dataclasses import dataclass
from ..core import ValidationError
from ..core.provenance import canonical_json
from ..corpus_release.model import digest


DEFAULT_POLICY = dict(
    schema='fulltext-policy/1', allowed_versions=['publishedVersion', 'acceptedVersion'],
    allow_unknown_version=False, allowed_schemes=['http', 'https'], require_oa_evidence=True,
    provider_sources_enabled=['FROZEN', 'OPENALEX', 'UNPAYWALL', 'OPENALEX_CONTENT'],
    candidate_rank_policy=['version', 'host', 'direct', 'oa', 'provider', 'candidate_id'],
    version_rank=['publishedVersion', 'acceptedVersion', 'submittedVersion', 'UNKNOWN'],
    host_rank=['publisher', 'repository', 'openalex_content', 'unknown'],
    max_pdf_bytes=25_000_000, min_pdf_bytes=100, max_metadata_bytes=5_000_000,
    request_timeout=30, max_redirects=4, retry_count=2, retry_backoff=1, max_retry_delay=10,
    global_download_budget=dict(items=100, http_requests=200, bytes=500_000_000,
                                artifacts=100, provider_lookups=100),
    per_item_candidate_limit=4, manual_ingest_enabled=False,
    export_policy='METADATA_ONLY_EXACT_FILES', allow_https_downgrade=False,
    content_type_policy='VALID_PDF_OVERRIDES_MIME', pdf_validation='PYPDF_STRICT_PAGES',
    signed_url_policy='RUNTIME_ONLY_FAIL_CLOSED_ON_RESUME', conflict_policy='REJECT_URL_VERSION_CONFLICT',
    primary_policy='FIRST_SUCCESS_IN_FROZEN_RANK', lookup_policy='SERIAL_ONCE_PER_RUN',
    privacy_policy='SANITIZE_DERIVATIVE_RETAIN_ORIGINAL_SHA')


@dataclass(frozen=True)
class FullTextPolicy:
    canonical: str

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls): return value
        if not isinstance(value, dict) or set(value) != set(DEFAULT_POLICY):
            raise ValidationError('Complete explicit full-text policy required')
        p = json.loads(canonical_json(value))
        for k in ('allowed_versions', 'allowed_schemes', 'provider_sources_enabled'):
            if not isinstance(p[k], list) or len(p[k]) != len(set(p[k])):
                raise ValidationError('Invalid policy set')
            p[k] = sorted(p[k])
        if not set(p['allowed_versions']) <= {'publishedVersion','acceptedVersion','submittedVersion'}:
            raise ValidationError('Invalid versions')
        if not p['allowed_schemes'] or not set(p['allowed_schemes']) <= {'http','https'}:
            raise ValidationError('Invalid schemes')
        if not set(p['provider_sources_enabled']) <= {'FROZEN','OPENALEX','UNPAYWALL','OPENALEX_CONTENT'}:
            raise ValidationError('Invalid providers')
        for k in ('allow_unknown_version','require_oa_evidence','allow_https_downgrade','manual_ingest_enabled'):
            if type(p[k]) is not bool: raise ValidationError('Expected boolean')
        if p['manual_ingest_enabled']: raise ValidationError('Manual ingest is not implemented in schema 1')
        for k in ('max_pdf_bytes','min_pdf_bytes','max_metadata_bytes','per_item_candidate_limit'):
            if type(p[k]) is not int or not 1 <= p[k] <= 1_000_000_000: raise ValidationError('Invalid finite limit')
        if p['min_pdf_bytes'] > p['max_pdf_bytes']: raise ValidationError('Inverted byte bounds')
        for k, hi in [('max_redirects',10),('retry_count',5)]:
            if type(p[k]) is not int or not 0 <= p[k] <= hi: raise ValidationError('Invalid HTTP bound')
        for k in ('request_timeout','retry_backoff','max_retry_delay'):
            if type(p[k]) not in (int,float) or not math.isfinite(p[k]) or not 0 < p[k] <= 300:
                raise ValidationError('Invalid HTTP time bound')
        if set(p['global_download_budget']) != set(DEFAULT_POLICY['global_download_budget']):
            raise ValidationError('Explicit global budget required')
        if any(type(v) is not int or not 1 <= v <= 1_000_000_000 for v in p['global_download_budget'].values()):
            raise ValidationError('Finite global budget required')
        for k in ('version_rank','host_rank'):
            if sorted(p[k]) != sorted(DEFAULT_POLICY[k]): raise ValidationError('Invalid rank permutation')
        if p['candidate_rank_policy'] != DEFAULT_POLICY['candidate_rank_policy']:
            raise ValidationError('Unsupported rank algorithm; change explicit version/host ranks instead')
        fixed = set(p)-{'allowed_versions','allow_unknown_version','allowed_schemes','require_oa_evidence',
            'provider_sources_enabled','version_rank','host_rank','max_pdf_bytes','min_pdf_bytes','max_metadata_bytes',
            'request_timeout','max_redirects','retry_count','retry_backoff','max_retry_delay','global_download_budget',
            'per_item_candidate_limit','allow_https_downgrade'}
        if any(p[k] != DEFAULT_POLICY[k] for k in fixed): raise ValidationError('Unsupported policy semantics')
        return cls(canonical_json(p))

    def as_dict(self): return json.loads(self.canonical)
    @property
    def sha256(self): return digest(self.as_dict())
