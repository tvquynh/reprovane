"""Explicit projection/export semantics; membership is never a plan choice."""
from dataclasses import dataclass
import json,re
from ..core import ValidationError
from ..core.provenance import canonical_json
from ..screening.plan import FIELDS,label
from ..discovery.store import digest


@dataclass(frozen=True)
class CorpusReleasePlan:
    canonical: str

    @classmethod
    def from_value(cls,value):
        if isinstance(value,cls): value=json.loads(value.canonical)
        keys={'release_label','release_schema_version','metadata_fields','identifier_policy','authorship_policy','venue_policy','provenance_policy','export_formats','ordering_policy','identity_policy','screening_source_policy','oa_policy','search_validation_refs','export_directory_policy'}
        if not isinstance(value,dict) or set(value)!=keys: raise ValidationError('Complete explicit corpus release plan required')
        p=json.loads(canonical_json(value)); label(p['release_label'])
        if p['release_schema_version']!='1' or p['identifier_policy']!='STAGE03_FROZEN_EXACT' or p['identity_policy']!='USE_STAGE03_FINAL_IDENTITY' or p['screening_source_policy']!='EXACT_SEALED_STAGE10': raise ValidationError('Accepted identity and screening policies required')
        if not isinstance(p['metadata_fields'],list) or not set(p['metadata_fields'])<=set(FIELDS): raise ValidationError('Unsupported metadata projection fields')
        p['metadata_fields']=sorted(set(p['metadata_fields']))
        if p['authorship_policy']!=('PER_PROVIDER_ORDERED' if 'authors' in p['metadata_fields'] else 'OMIT') or p['venue_policy']!=('FROZEN_PROVIDER_DESCRIPTION' if 'venue' in p['metadata_fields'] else 'OMIT'): raise ValidationError('Explicit field/policy agreement required')
        if p['provenance_policy'] not in ('REFERENCES_ONLY','REFERENCES_WITH_ACTOR_LABELS') or p['ordering_policy'] not in ('PUBLICATION_ENTITY_ID','CORPUS_ITEM_ID') or p['oa_policy'] not in ('FROZEN_RAW_WORK_LOCATIONS','OMIT') or p['export_directory_policy']!='EXACT_FILES_ONLY': raise ValidationError('Unsupported release/export policy')
        if not isinstance(p['export_formats'],list) or set(p['export_formats'])!={'JSONL','CSV'}: raise ValidationError('Lossless JSONL and convenience CSV required')
        p['export_formats']=['JSONL','CSV']
        if not isinstance(p['search_validation_refs'],list) or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',v) for v in p['search_validation_refs']): raise ValidationError('Explicit search audit references required')
        p['search_validation_refs']=sorted(set(p['search_validation_refs']))
        return cls(canonical_json(p))

    def as_dict(self): return json.loads(self.canonical)
    @property
    def sha256(self): return digest(self.as_dict())
