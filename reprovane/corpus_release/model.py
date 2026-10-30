"""Shared semantic item/manifests, privacy and descriptive set statistics."""
import hashlib,json,re
from collections import Counter
from ..core import ValidationError,ArtifactIntegrityError
from ..core.provenance import canonical_json


def sha(value): return hashlib.sha256(value).hexdigest()
def digest(value): return sha(canonical_json(value).encode('utf-8'))
def json_bytes(value): return (canonical_json(value)+'\n').encode('utf-8')


def private_check(value,forbidden_values=()):
    text=canonical_json(value)
    patterns=[r'(?i)(?:api[_-]?key|password|authorization|access[_-]?token|cookie|x-amz-signature|x-amz-credential)["\s]*[=:]',r'(?i)\bbearer(?:\s|%20)+\S+',r'-----BEGIN .*PRIVATE KEY',r'\b(?:ghp_|github_pat_)[A-Za-z0-9_]{25,}',r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{25,}',r'\bAKIA[0-9A-Z]{16}\b',r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',r'(?i)file://',r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]',r'(?i)/(?:Users|home|tmp|private|var|mnt)/',r'(?i)https?://[^/\s"\\]+@']
    if any(re.search(pattern,text) for pattern in patterns) or any(v and v in text for v in forbidden_values): raise ValidationError('Private or machine-local release content refused')


def item_id(release_id,entity): return 'cri_'+digest([release_id,entity])[:40]


def finish_item(body):
    value=dict(body)
    for field in ['identity','metadata','screening_provenance','discovery_provenance','oa_locations']: value[field+'_sha256']=digest(value[field])
    value['item_sha256']=digest(value); return value


def validate_item(item):
    fields={'corpus_item_id','release_id','publication_entity_id','screening_candidate_id','screening_resolution_id','resolution_run_id','ordinal','identity','metadata','screening_provenance','discovery_provenance','oa_locations'}
    body={k:item[k] for k in fields}
    if item!=finish_item(body) or item['corpus_item_id']!=item_id(item['release_id'],item['publication_entity_id']): raise ArtifactIntegrityError('Corpus item hash/identity mismatch')


def values(item,field):
    found=[]
    for record in item['metadata']['records']:
        for claim in record['fields'].get(field) or []:
            value=claim.get('title') if field=='venue' else claim.get('value')
            if value is not None and value not in found: found.append(value)
    return sorted(found)


def statistics(source,items):
    years=Counter(str(v) for item in items for v in values(item,'publication_year')); types=Counter(v for item in items for v in values(item,'work_type'))
    doi=sum(bool(i['identity']['normalized_dois']) for i in items); native=sum(bool(i['identity']['openalex_work_ids']) for i in items)
    return dict(screening_candidate_count=source['screening_candidate_count'],screen_include_count=source['screen_include_count'],screen_exclude_count=source['screen_exclude_count'],corpus_item_count=len(items),unresolved_discovery_evidence_count=source['unresolved_discovery_summary']['provider_level_unresolved_count'],items_with_doi=doi,items_without_doi=len(items)-doi,items_with_openalex_id=native,items_without_openalex_id=len(items)-native,publication_year_distribution=dict(sorted(years.items())),work_type_distribution=dict(sorted(types.items())))


def semantic_manifest(release_id,revision,screening_id,screening_sha,plan,source,items):
    return dict(release_id=release_id,release_label=plan['release_label'],release_schema_version=plan['release_schema_version'],project_revision_id=revision,screening_run_id=screening_id,screening_manifest_sha256=screening_sha,release_plan=plan,release_plan_sha256=digest(plan),source=source,item_set_sha256=digest(items),ordered_items=[{k:i[k] for k in ['corpus_item_id','publication_entity_id','identity_sha256','metadata_sha256','screening_provenance_sha256','discovery_provenance_sha256','oa_locations_sha256','item_sha256']} for i in items],statistics=statistics(source,items),export_schema=dict(version='corpus-portable/1',files=['corpus.jsonl','corpus.csv','manifest.json','SHA256SUMS.txt'],encoding='UTF-8',csv_cells='sorted JSON arrays for multivalued metadata',checksum_self_excluded=True))
