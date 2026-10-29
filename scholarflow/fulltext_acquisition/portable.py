"""Portable metadata consistency is separate from DB/source and PDF-byte verification."""
import csv
import io
import json
from pathlib import Path
from ..core import ValidationError,ArtifactIntegrityError
from ..core.provenance import canonical_json
from ..corpus_release.model import digest,sha
from .policy import FullTextPolicy


def render_files(manifest):
    files={'fulltext_manifest.json':(canonical_json(manifest)+'\n').encode(),
           'fulltext_items.jsonl':b''.join((canonical_json(s)+'\n').encode() for s in manifest['items']),
           'manual_queue.jsonl':b''.join((canonical_json(s)+'\n').encode() for s in manifest['manual_queue'])}
    output=io.StringIO(newline=''); fields=['corpus_item_id','publication_entity_id','titles','dois','openalex_work_ids','landing_pages','candidates','status','reasons','release_id','release_manifest_sha256','fulltext_policy_sha256']
    writer=csv.DictWriter(output,fieldnames=fields,lineterminator='\n'); writer.writeheader()
    for row in manifest['manual_queue']:
        writer.writerow({k:canonical_json(v) if isinstance(v,(list,dict)) else v for k,v in row.items()})
    files['manual_queue.csv']=output.getvalue().encode()
    files['SHA256SUMS.txt']=''.join(sha(data)+'  '+name+'\n' for name,data in sorted(files.items())).encode()
    return files


def write_exact(root,files):
    if root.is_symlink(): raise ValidationError('Symlink export refused')
    if root.exists():
        if not root.is_dir() or any(p.name not in files or p.is_symlink() or not p.is_file() or p.read_bytes()!=files[p.name] for p in root.iterdir()):
            raise ValidationError('Differing existing bytes/extra files refused')
    else: root.mkdir(parents=True)
    for name,body in files.items():
        if not (root/name).exists():
            with (root/name).open('xb') as stream: stream.write(body)


def verify_export(directory,*,expected_manifest_sha256=None):
    root=Path(directory)
    try:
        names={'fulltext_manifest.json','fulltext_items.jsonl','manual_queue.csv','manual_queue.jsonl','SHA256SUMS.txt'}
        if root.is_symlink() or {p.name for p in root.iterdir()}!=names or any(p.is_symlink() or not p.is_file() for p in root.iterdir()): raise ValueError()
        def pairs(items):
            d={}
            for k,v in items:
                if k in d: raise ValueError()
                d[k]=v
            return d
        m=json.loads((root/'fulltext_manifest.json').read_bytes(),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        if m['schema']!='fulltext-manifest/1' or FullTextPolicy.from_value(m['fulltext_policy']).sha256!=m['fulltext_policy_sha256']: raise ValueError()
        ids=m['ordered_corpus_item_ids']
        if len(ids)!=len(set(ids)) or [s['corpus_item_id'] for s in m['items']]!=ids: raise ValueError()
        for status in m['items']:
            if digest({k:v for k,v in status.items() if k!='status_sha256'})!=status['status_sha256']: raise ValueError()
            if status['status'] not in ('ACQUIRED_AUTO','MANUAL_REQUIRED'): raise ValueError()
            if (status['status']=='ACQUIRED_AUTO')!=(status['selected_artifact_sha256'] is not None): raise ValueError()
            success=[a for a in status['attempts'] if a['outcome']['status']=='ACQUIRED']
            if status['selected_artifact_sha256'] and not any(a['candidate_id']==status['selected_candidate_id'] and a['outcome']['artifact_sha256']==status['selected_artifact_sha256'] for a in success): raise ValueError()
        unresolved=sorted(s['corpus_item_id'] for s in m['items'] if s['status']!='ACQUIRED_AUTO')
        if [r['corpus_item_id'] for r in m['manual_queue']]!=unresolved or digest(m['manual_queue'])!=m['manual_queue_sha256']: raise ValueError()
        if any(r['release_id']!=m['release_id'] or r['release_manifest_sha256']!=m['release_manifest_sha256'] or r['fulltext_policy_sha256']!=m['fulltext_policy_sha256'] for r in m['manual_queue']): raise ValueError()
        byid={s['corpus_item_id']:s for s in m['items']}
        if any(r['status']!=byid[r['corpus_item_id']]['status'] or r['reasons']!=byid[r['corpus_item_id']]['reasons'] or r['publication_entity_id']!=byid[r['corpus_item_id']]['publication_entity_id'] for r in m['manual_queue']): raise ValueError()
        artifacts={a['outcome']['artifact_sha256'] for s in m['items'] for a in s['attempts'] if a['outcome']['status']=='ACQUIRED'}
        if m['artifact_count']!=len(artifacts) or m['failure_count']!=len(unresolved) or m['budget']['artifacts']!=len(artifacts): raise ValueError()
        if expected_manifest_sha256 and digest(m)!=expected_manifest_sha256: raise ValueError()
        if any((root/name).read_bytes()!=body for name,body in render_files(m).items()): raise ValueError()
        return dict(verified=True,mode='PORTABLE_METADATA_ONLY_NO_DB_NO_PDF_BYTES',manifest_sha256=digest(m),item_count=len(ids),manual_queue_count=len(unresolved))
    except (OSError,ValueError,KeyError,TypeError,ValidationError): raise ArtifactIntegrityError('Portable full-text metadata verification failed') from None
