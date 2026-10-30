"""Read-only bounded inventory of the actual v4 file families, never archive extraction."""
import csv,io,json,os,re
from pathlib import Path
from ..core import ValidationError
from ..core.provenance import canonical_json
from ..corpus_release.model import digest,sha

LIMITS=dict(max_files=1000,max_file_bytes=64*1024*1024,max_total_bytes=256*1024*1024,max_records=100000)
SECRET=re.compile(r'(?i)(api[_-]?key|authorization|bearer\s+\S+|access[_-]?token|password|cookie|(?<![a-z0-9])[a-z]:[\\/]|/home/|/Users/|(?:token|sig|signature)=)')
EXCLUDED={'.git','__pycache__','.venv','venv'}


def sensitive(body,private=()):
    text=body.decode('utf-8',errors='replace')
    # Bound email recognition around actual @ positions; long author/title tokens must stay linear.
    email=any(re.search(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}',text[max(0,m.start()-128):m.start()+254]) for m in re.finditer('@',text))
    return bool(email or SECRET.search(text) or any(v and v in text for v in private))


def runtime_private(private=()):
    from ..fulltext_acquisition.transport import runtime_credentials
    return tuple(private)+tuple(v for v in runtime_credentials() if v)+tuple(os.environ.get(k,'') for k in ('GITHUB_TOKEN','GH_TOKEN','OPENAI_API_KEY'))


def family(name):
    if name in ('VERSION','version.txt'): return 'VERSION'
    if name.endswith(('_raw.jsonl','_raw.csv','_enriched.jsonl','_enriched.csv')): return 'OPENALEX_ROWS'
    if name.endswith('_main.csv'): return 'LEGACY_MAIN'
    if name.endswith('_excluded.csv'): return 'LEGACY_EXCLUDED'
    if name.endswith(('_source_audit.csv','_preprints.csv')): return 'LEGACY_VENUE_OR_PREPRINT'
    if name.endswith('_checkpoint.json'): return 'LEGACY_CURSOR_NOT_RESUMABLE'
    if name.endswith(('_run_metadata.json','_summary.json')): return 'LEGACY_QUERY_OR_SUMMARY'
    if name.lower().endswith('.pdf'): return 'LEGACY_FULLTEXT_UNBOUND'
    if name in ('fetch_state.json','blocked_hosts.json','downloaded.csv','manual_download.csv','manual_download.html','manual_download.ris','fetch_log.csv','fetch_summary.json'): return 'LEGACY_OA_OUTPUT'
    if name.startswith('config') or name.endswith(('.log','.env')) or name=='.env': return 'CONFIG_OR_LOG_NOT_COPIED'
    return 'UNSUPPORTED'


def record_rows(path,body):
    if path.suffix.lower()=='.jsonl':
        rows=[json.loads(line) for line in body.decode('utf-8-sig').splitlines() if line.strip()]
    elif path.suffix.lower()=='.csv': rows=list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'),newline='')))
    else: return []
    if len(rows)>LIMITS['max_records'] or any(not isinstance(r,dict) for r in rows): raise ValueError()
    return rows


def native(row):
    value=row.get('openalex_id') or row.get('id')
    if not isinstance(value,str): return None
    match=re.fullmatch(r'(?:https://openalex.org/)?(W[0-9]+)',value)
    return 'https://openalex.org/'+match[1] if match else None


def files_in(root):
    root=Path(root)
    if not root.is_dir() or root.is_symlink(): raise ValidationError('Legacy source must be an ordinary directory')
    found=[]
    for folder,dirs,files in os.walk(root,followlinks=False):
        if any((Path(folder)/d).is_symlink() for d in dirs): raise ValidationError('Source symlink directories refused')
        dirs[:]=sorted(d for d in dirs if d not in EXCLUDED)
        for name in sorted(files):
            p=Path(folder)/name
            if p.is_symlink() or not p.is_file(): raise ValidationError('Source symlink/non-file refused')
            found.append(p)
            if len(found)>LIMITS['max_files']: raise ValidationError('Source file count exceeds finite scope')
    return sorted(found,key=lambda p:p.relative_to(root).as_posix())


def audit(root,*,private=()):
    # Known configured values are checked without logging or serializing them.
    private=runtime_private(private)
    root=Path(root); entries=[]; counts=dict(records=0,with_doi=0,with_openalex_id=0,migration_safe_records=0,manual_interpretation=0); ids={}; total=0; versions=[]
    for path in files_in(root):
        size=path.stat().st_size;total+=size
        if size>LIMITS['max_file_bytes'] or total>LIMITS['max_total_bytes']: raise ValidationError('Legacy source exceeds finite byte limits')
        body=path.read_bytes()
        if len(body)!=size: raise ValidationError('Source drift while reading')
        original=path.relative_to(root).as_posix(); rel='redacted-name/'+sha(original.encode()) if sensitive(original.encode(),private) else original
        kind=family(path.name); secret=sensitive(body,private) or rel!=original
        copy=not secret and kind not in ('CONFIG_OR_LOG_NOT_COPIED','UNSUPPORTED')
        row=dict(path=rel,byte_size=size,sha256=sha(body),recognized_type=kind,copy_evidence=copy,warning='REDACTED_SECRET_PRESENT' if secret else 'CONFIG_NOT_COPIED' if kind=='CONFIG_OR_LOG_NOT_COPIED' else 'UNSUPPORTED_ARTIFACT' if kind=='UNSUPPORTED' else None,record_count=0,safe_record_count=0)
        if kind=='VERSION' and not secret:
            token=body.decode('utf-8',errors='replace').strip()
            if re.fullmatch(r'\d+\.\d+\.\d+(?:[a-z0-9.-]+)?',token):versions.append(dict(path=rel,version=token))
        if kind=='LEGACY_QUERY_OR_SUMMARY' and not secret:
            try:
                metadata=json.loads(body);token=metadata.get('tool_version') if isinstance(metadata,dict) else None
                if isinstance(token,str) and re.fullmatch(r'\d+\.\d+\.\d+(?:[a-z0-9.-]+)?',token):versions.append(dict(path=rel,version=token))
            except (ValueError,UnicodeError):row['warning']='UNSUPPORTED_METADATA_FORMAT'
        if copy and kind in ('OPENALEX_ROWS','LEGACY_MAIN','LEGACY_EXCLUDED','LEGACY_VENUE_OR_PREPRINT'):
            try: records=record_rows(path,body)
            except (ValueError,UnicodeError,csv.Error): records=[];row['warning']='UNSUPPORTED_RECORD_FORMAT';row['copy_evidence']=False
            row['record_count']=len(records)
            for rec in records:
                oid=native(rec);counts['records']+=1;counts['with_doi']+=bool(rec.get('doi'));counts['with_openalex_id']+=bool(oid)
                if kind=='OPENALEX_ROWS' and oid: row['safe_record_count']+=1;counts['migration_safe_records']+=1;ids[oid]=ids.get(oid,0)+1
                else: counts['manual_interpretation']+=1
        if not secret:
            if kind=='LEGACY_FULLTEXT_UNBOUND': row['warning']='LEGACY_FULLTEXT_UNBOUND'
            if kind=='LEGACY_CURSOR_NOT_RESUMABLE': row['warning']='CURSOR_PRESERVED_NEVER_RESUMED'
            if kind in ('LEGACY_MAIN','LEGACY_EXCLUDED'): row['warning']='HISTORICAL_CLASSIFICATION_ONLY'
        entries.append(row)
    distinct={v['version'] for v in versions};version=next(iter(distinct)) if len(distinct)==1 else None
    return dict(schema='legacy-v4-inventory/1',detected_version=version,version_sources=versions,version_conflict=len(distinct)>1,baseline_if_version_4_1_0='f70796d1ef6003e24ce3afa1328088cc515dc0a0' if version=='4.1.0' else None,files=entries,source_fingerprint=digest(entries),counts=counts,duplicate_provider_ids=sorted(k for k,v in ids.items() if v>1),limits=LIMITS,excluded_directory_names=sorted(EXCLUDED),planned_objects=['ReviewProject','ProjectRevision','LEGACY_V4_IMPORT ProviderSnapshot','ProviderRecord','typed observations','Stage03 resolution','legacy artifact/maps/warnings'],interpretation='Historical evidence only; no screening/corpus/fulltext authority; no legacy cursor resume.')


def plan(root,project_key='legacy-v4-import',revision_number=1,*,private=()):
    inventory=audit(root,private=private)
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',project_key) or type(revision_number) is not int or revision_number<1: raise ValidationError('Invalid explicit target project')
    body=dict(schema='legacy-v4-plan/1',inventory=inventory,source_inventory_sha256=inventory['source_fingerprint'],adapters=['V4_FLAT_OPENALEX_1','ATTACHED_OPENALEX_WORK_1','HISTORICAL_ONLY_1'],target=dict(project_key=project_key,revision_number=revision_number),output_destination_policy='SEPARATE_NEW_WORKSPACE_RUNTIME_PATH_EXCLUDED',import_policy='LEGACY_INCOMPLETE_REQUEST_PROVENANCE',unsupported_policy='REPORT_NO_COPY',duplicate_policy='PRESERVE_OCCURRENCES_STAGE03_ONLY',fulltext_policy='UNBOUND_EVIDENCE_NO_PRIMARY',secret_policy='HASH_AND_WARN_NO_COPY')
    return dict(plan=body,plan_sha256=digest(body))
