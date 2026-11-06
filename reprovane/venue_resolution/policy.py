import hashlib
from dataclasses import dataclass
from ..core import ValidationError
from ..core.provenance import canonical_json
from ..resolution.policy import ObservationSelector, object_value

POLICY_NAME, POLICY_VERSION = 'conservative_venue_v1', '1'
RESOLVER_NAME, RESOLVER_VERSION = 'conservative_venue', '1'
METHODS = ['ISSN_EXACT','ISSNL_EXACT','TITLE_EXACT','TITLE_NORMALIZED','TITLE_STATUS_NORMALIZED']


def stable(value):
    if isinstance(value,dict):
        return {k:stable(v) for k,v in value.items() if not (k.endswith('_at_utc') or k.endswith('_retrieved_at'))}
    if isinstance(value,list): return [stable(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(canonical_json(stable(value)).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class VenuePolicy:
    max_candidate_block_size: int = 50

    def as_dict(self):
        if type(self.max_candidate_block_size) is not int or self.max_candidate_block_size<2:
            raise ValidationError('Block limit must be an integer >=2')
        return dict(method_precedence=METHODS,allow_provider_native_grouping=True,
            allow_unique_exact_title_auto_same=True,allow_unique_normalized_title_auto_same=True,
            allow_unique_status_title_auto_same=True,allow_fuzzy_auto_merge=False,
            max_candidate_block_size=self.max_candidate_block_size)

    @classmethod
    def from_value(cls,value=None):
        if value is None: return cls()
        value=object_value(value); policy=cls(value.get('max_candidate_block_size',50))
        if value != policy.as_dict(): raise ValidationError('Unsupported venue policy semantics')
        return policy
