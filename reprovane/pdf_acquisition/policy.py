"""Versioned, canonical acquisition policies for the two acquisition profiles.

DISCOVERY_DOWNLOAD governs discovery-oriented runs (pdf_* tables). REVIEW_FULLTEXT is the accepted
Stage11B policy (fulltext-policy/1) used unchanged for sealed-corpus review acquisition.
"""
import copy
import json
import math
from dataclasses import dataclass

from ..core import ValidationError
from ..core.provenance import canonical_json
from ..corpus_release.model import digest
from ..fulltext_acquisition.policy import DEFAULT_POLICY as REVIEW_DEFAULT_POLICY, FullTextPolicy

VERSIONS = ['publishedVersion', 'acceptedVersion', 'submittedVersion', 'UNKNOWN']
HOSTS = ['publisher', 'repository', 'openalex_content', 'unknown']
URL_TYPES = ['DIRECT_PDF', 'OPENALEX_CONTENT_PDF', 'OA_URL']
EVIDENCE_SOURCES = ['INPUT_ROW', 'OPENALEX_RECORD', 'FROZEN', 'OPENALEX', 'UNPAYWALL', 'OPENALEX_CONTENT']
LOOKUP_SOURCES = ('OPENALEX', 'UNPAYWALL')
MANUAL_MATCH_RULES = ['USER_MAPPING', 'EXACT_FILENAME_KEY']

DISCOVERY_DEFAULT_POLICY = dict(
    schema='pdf-discovery-policy/1',
    profile='DISCOVERY_DOWNLOAD',
    identity_policy='EXACT_IDENTIFIERS_NO_TITLE_MERGE',
    # Every allowed version is acquisition evidence only; none is promoted to a verified version.
    allowed_versions=list(VERSIONS),
    version_rank=list(VERSIONS),
    version_verification='PROVIDER_CLAIM_ONLY_UNVERIFIED',
    host_rank=list(HOSTS),
    url_type_rank=list(URL_TYPES),
    oa_evidence_policy='NOT_EXPLICITLY_CLOSED',
    evidence_sources_enabled=['FROZEN', 'INPUT_ROW', 'OPENALEX_RECORD'],
    allowed_schemes=['http', 'https'],
    allow_https_downgrade=False,
    max_pdf_bytes=100 * 1024 * 1024,
    min_pdf_bytes=100,
    max_metadata_bytes=5_000_000,
    max_robots_bytes=512 * 1024,
    request_timeout=60,
    max_redirects=5,
    retry_count=2,
    retry_backoff=1,
    max_retry_delay=30,
    per_host_delay_seconds=2.0,
    max_crawl_delay_seconds=60,
    robots_policy='RFC9309_RESPECT',
    host_failure_threshold=3,
    block_on_plain_403=True,
    max_cooldown_seconds=86400,
    per_item_candidate_limit=4,
    max_attempts_per_candidate=3,
    stop_after_first_success=True,
    global_budget=dict(items=100_000, http_requests=1_000_000, bytes=500 * 1024 ** 3,
                       artifacts=100_000, provider_lookups=200_000),
    pdf_validation='PYPDF_STRICT_PAGES',
    manual_match_rules=list(MANUAL_MATCH_RULES),
    privacy_policy='SANITIZED_URLS_NO_RUNTIME_SECRETS',
    concurrency='SEQUENTIAL',
)

_ENUMS = dict(schema={'pdf-discovery-policy/1'}, profile={'DISCOVERY_DOWNLOAD'},
              identity_policy={'EXACT_IDENTIFIERS_NO_TITLE_MERGE'},
              version_verification={'PROVIDER_CLAIM_ONLY_UNVERIFIED'},
              oa_evidence_policy={'NOT_EXPLICITLY_CLOSED', 'EXPLICIT_OPEN_REQUIRED'},
              robots_policy={'RFC9309_RESPECT'}, pdf_validation={'PYPDF_STRICT_PAGES'},
              privacy_policy={'SANITIZED_URLS_NO_RUNTIME_SECRETS'}, concurrency={'SEQUENTIAL'})


def _int(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValidationError('Invalid finite integer policy bound')


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValidationError('Invalid finite numeric policy bound')


@dataclass(frozen=True)
class DiscoveryPolicy:
    canonical: str

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict) or set(value) != set(DISCOVERY_DEFAULT_POLICY):
            raise ValidationError('Complete explicit discovery policy required')
        p = json.loads(canonical_json(value))
        for key, allowed in _ENUMS.items():
            if p[key] not in allowed:
                raise ValidationError('Unsupported policy semantics')
        for key, universe in (('allowed_versions', VERSIONS), ('evidence_sources_enabled', EVIDENCE_SOURCES),
                              ('allowed_schemes', ['http', 'https']), ('manual_match_rules', MANUAL_MATCH_RULES)):
            if not isinstance(p[key], list) or len(p[key]) != len(set(p[key])) or not set(p[key]) <= set(universe):
                raise ValidationError('Invalid policy set')
            p[key] = sorted(p[key])
        if not p['allowed_versions'] or not p['allowed_schemes'] or not p['evidence_sources_enabled']:
            raise ValidationError('Empty policy set')
        for key, universe in (('version_rank', VERSIONS), ('host_rank', HOSTS), ('url_type_rank', URL_TYPES)):
            if not isinstance(p[key], list) or sorted(p[key]) != sorted(universe):
                raise ValidationError('Invalid rank permutation')
        for key in ('allow_https_downgrade', 'block_on_plain_403', 'stop_after_first_success'):
            if type(p[key]) is not bool:
                raise ValidationError('Expected boolean')
        for key in ('max_pdf_bytes', 'min_pdf_bytes', 'max_metadata_bytes', 'max_robots_bytes'):
            _int(p[key], 1, 2_000_000_000)
        if p['min_pdf_bytes'] > p['max_pdf_bytes']:
            raise ValidationError('Inverted byte bounds')
        _int(p['max_redirects'], 0, 10)
        _int(p['retry_count'], 0, 5)
        _int(p['host_failure_threshold'], 1, 100)
        _int(p['per_item_candidate_limit'], 1, 100)
        _int(p['max_attempts_per_candidate'], 1, 20)
        _int(p['max_cooldown_seconds'], 1, 30 * 86400)
        for key in ('request_timeout', 'retry_backoff', 'max_retry_delay', 'max_crawl_delay_seconds'):
            _number(p[key], 0.001, 3600)
        # v4.1.0 refused a delay below one second per host; the same politeness floor applies here.
        _number(p['per_host_delay_seconds'], 1, 3600)
        if set(p['global_budget']) != set(DISCOVERY_DEFAULT_POLICY['global_budget']):
            raise ValidationError('Explicit global budget required')
        for value in p['global_budget'].values():
            _int(value, 1, 10 ** 15)
        return cls(canonical_json(p))

    def as_dict(self):
        return json.loads(self.canonical)

    @property
    def sha256(self):
        return digest(self.as_dict())


def default_policy(profile='discovery'):
    """Return an explicit editable copy of a built-in policy profile."""
    if profile in ('discovery', 'DISCOVERY_DOWNLOAD'):
        return copy.deepcopy(DISCOVERY_DEFAULT_POLICY)
    if profile in ('review', 'REVIEW_FULLTEXT'):
        return copy.deepcopy(REVIEW_DEFAULT_POLICY)
    raise ValidationError('Unknown policy profile')


def describe(profile='discovery'):
    value = default_policy(profile)
    if value.get('profile') == 'DISCOVERY_DOWNLOAD':
        frozen = DiscoveryPolicy.from_value(value)
        return dict(profile='DISCOVERY_DOWNLOAD', policy=frozen.as_dict(), policy_sha256=frozen.sha256)
    frozen = FullTextPolicy.from_value(value)
    return dict(profile='REVIEW_FULLTEXT', policy=frozen.as_dict(), policy_sha256=frozen.sha256,
                note='Stage11B fulltext-policy/1; used by reprovane pdf review and reprovane fulltext.')


def transport_policy(p):
    """Adapter for the shared SafeTransport, which reads only the bounded HTTP fields."""
    return dict(allowed_schemes=p['allowed_schemes'], request_timeout=p['request_timeout'],
                max_redirects=p['max_redirects'], retry_count=p['retry_count'], retry_backoff=p['retry_backoff'],
                max_retry_delay=p['max_retry_delay'], allow_https_downgrade=p['allow_https_downgrade'])


def candidate_policy(p):
    """Adapter for the shared Stage11B make_candidates eligibility rules."""
    return dict(provider_sources_enabled=p['evidence_sources_enabled'],
                allowed_versions=[v for v in p['allowed_versions'] if v != 'UNKNOWN'],
                allow_unknown_version='UNKNOWN' in p['allowed_versions'],
                require_oa_evidence=p['oa_evidence_policy'] == 'EXPLICIT_OPEN_REQUIRED',
                allowed_schemes=p['allowed_schemes'], version_rank=p['version_rank'], host_rank=p['host_rank'])
