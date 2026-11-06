"""Canonical portable files and DB-free integrity verification. No source authentication."""
import csv,io,json,re
from pathlib import Path
from ..core import ValidationError,ArtifactIntegrityError
from .model import sha,digest,json_bytes,private_check,validate_item,values,semantic_manifest
from .plan import CorpusReleasePlan

FILES=('SHA256SUMS.txt','corpus.csv','corpus.jsonl','manifest.json')
CSV_FIELDS=['corpus_item_id','publication_entity_id','title','publication_year','doi','openalex_id','venue','work_type','screening_resolution_basis']


def render_data(items):
    output=io.StringIO(newline=''); writer=csv.DictWriter(output,fieldnames=CSV_FIELDS,lineterminator='\n'); writer.writeheader()
    for item in items:
        row={k:item[k] for k in ['corpus_item_id','publication_entity_id']}
        row.update({k:json_bytes(values(item,k)).decode().rstrip('\n') for k in ['title','publication_year','venue','work_type']})
        row.update(doi=json_bytes(item['identity']['normalized_dois']).decode().rstrip('\n'),openalex_id=json_bytes(item['identity']['openalex_work_ids']).decode().rstrip('\n'),screening_resolution_basis=item['screening_provenance']['resolution_basis']); writer.writerow(row)
    return {'corpus.jsonl':b''.join(json_bytes(i) for i in items),'corpus.csv':output.getvalue().encode('utf-8')}


def portable_manifest(manifest,inventory):
    source=manifest['source']; counts=manifest['statistics']
    return dict(release_id=manifest['release_id'],release_label=manifest['release_label'],project_revision_id=manifest['project_revision_id'],screening_run_id=manifest['screening_run_id'],screening_manifest_sha256=manifest['screening_manifest_sha256'],release_plan_sha256=manifest['release_plan_sha256'],release_semantic_manifest_sha256=digest(manifest),release_manifest=manifest,item_count=counts['corpus_item_count'],screen_include_count=source['screen_include_count'],screen_exclude_count=source['screen_exclude_count'],unresolved_discovery_count=counts['unresolved_discovery_evidence_count'],unresolved_discovery_sha256=source['unresolved_discovery_sha256'],schema='corpus-portable/1',file_inventory=inventory)


def render_files(manifest,items):
    files=render_data(items); inventory=[dict(name=k,sha256=sha(v),bytes=len(v)) for k,v in sorted(files.items())]; p=portable_manifest(manifest,inventory); files['manifest.json']=json_bytes(p)
    files['SHA256SUMS.txt']=''.join(sha(data)+'  '+name+'\n' for name,data in sorted(files.items())).encode('utf-8')
    return files,p


def strict_json(text):
    def pairs(values):
        result={}
        for k,v in values:
            if k in result: raise ValueError('Duplicate JSON key')
            result[k]=v
        return result
    return json.loads(text,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Non-finite JSON')))


def verify_export(directory,*,expected_manifest_sha256=None):
    try:
        root=Path(directory)
        if not root.is_dir() or {p.name for p in root.iterdir()}!=set(FILES) or any(p.is_symlink() or not p.is_file() for p in root.iterdir()): raise ValueError('Exact regular-file inventory required')
        files={name:(root/name).read_bytes() for name in FILES}; checks={}
        for line in files['SHA256SUMS.txt'].decode('utf-8').splitlines():
            match=re.fullmatch(r'([0-9a-f]{64})  (corpus\.jsonl|corpus\.csv|manifest\.json)',line)
            if not match or match[2] in checks: raise ValueError('Invalid checksum inventory')
            checks[match[2]]=match[1]
        if set(checks)!=set(FILES)-{'SHA256SUMS.txt'} or any(sha(files[k])!=v for k,v in checks.items()): raise ValueError('Portable checksum mismatch')
        p=strict_json(files['manifest.json'].decode('utf-8')); manifest=p['release_manifest']; plan=CorpusReleasePlan.from_value(manifest['release_plan']).as_dict()
        items=[strict_json(line) for line in files['corpus.jsonl'].decode('utf-8').splitlines()]
        source=manifest['source']; included=source['screen_include_refs']; excluded=source['screen_exclude_refs']
        if source['screening_candidate_count']!=len(included)+len(excluded) or source['screen_include_count']!=len(included) or source['screen_exclude_count']!=len(excluded): raise ValueError('Screening count mismatch')
        if any(r['kind']!='SCREENING_INCLUDE_REFERENCE' for r in included) or any(r['kind']!='SCREENING_EXCLUSION_AUDIT' for r in excluded) or {r['publication_entity_id'] for r in included}&{r['publication_entity_id'] for r in excluded}: raise ValueError('Excluded evidence cannot be membership')
        if digest(source['unresolved_discovery_summary'])!=source['unresolved_discovery_sha256']: raise ValueError('Unresolved summary mismatch')
        if len(items)!=len(included) or len({i['corpus_item_id'] for i in items})!=len(items) or len({i['publication_entity_id'] for i in items})!=len(items) or {i['publication_entity_id'] for i in items}!={r['publication_entity_id'] for r in included}: raise ValueError('Corpus membership mismatch')
        key='publication_entity_id' if plan['ordering_policy']=='PUBLICATION_ENTITY_ID' else 'corpus_item_id'
        if items!=sorted(items,key=lambda i:i[key]): raise ValueError('Corpus order mismatch')
        by_entity={r['publication_entity_id']:r for r in included}
        for index,item in enumerate(items):
            validate_item(item); private_check(item); s=item['screening_provenance']; ref=by_entity[item['publication_entity_id']]
            if type(item['ordinal']) is not int or item['ordinal']!=index or item['release_id']!=manifest['release_id'] or s['outcome']!='SCREEN_INCLUDE' or s['reason_codes'] or s['screening_manifest_sha256']!=manifest['screening_manifest_sha256'] or s['screening_run_id']!=manifest['screening_run_id'] or s['candidate_id']!=ref['candidate_id'] or s['outcome_sha256']!=ref['outcome_sha256'] or item['identity']['publication_entity_id']!=item['publication_entity_id'] or any(set(r['fields'])!=set(plan['metadata_fields']) for r in item['metadata']['records']): raise ValueError('Corpus item source mismatch')
        rebuilt=semantic_manifest(manifest['release_id'],manifest['project_revision_id'],manifest['screening_run_id'],manifest['screening_manifest_sha256'],plan,source,items)
        if rebuilt!=manifest or digest(manifest)!=p['release_semantic_manifest_sha256'] or expected_manifest_sha256 is not None and digest(manifest)!=expected_manifest_sha256: raise ValueError('Release semantic manifest mismatch')
        expected,_=render_files(manifest,items)
        if files!=expected: raise ValueError('Noncanonical or inconsistent portable projection')
        private_check(p)
        return dict(valid=True,release_id=manifest['release_id'],release_manifest_sha256=digest(manifest),corpus_item_count=len(items),screen_include_count=source['screen_include_count'],screen_exclude_count=source['screen_exclude_count'],file_count=4,scope='Portable self-consistency only; DB verification establishes upstream provenance. Use a trusted expected manifest hash for external identity binding.')
    except (ValueError,KeyError,TypeError,UnicodeError,OSError,ValidationError) as exc:
        raise ArtifactIntegrityError('Portable corpus verification failed') from None
