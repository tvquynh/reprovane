"""Manifest-driven LOCAL SCImago package validation/import; no network acquisition."""
import csv
import hashlib
import json
import math
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from ..core import ValidationError,ArtifactIntegrityError
from ..core.foundation import Foundation
from ..core.provenance import canonical_json,sanitize_locator
from ..observations import ObservationStore,NormalizationProvenance

REPOSITORY='tvquynh/wosaide-journal-lists'
BRANCH='control'
NORMALIZER=NormalizationProvenance('scimago-local-normalizer','1','1')
SOURCE_REQUIRED={'Sourceid','Title','Type','Issn','SJR','SJR Best Quartile','H index','Country','Region','Coverage','Categories','Areas','Rank','Open Access','Open Access Diamond','Publisher'}


def _filename(value):
    if not isinstance(value,str) or not value or value in ('.','..') or any(c in value for c in '/\\:') or '..' in value:
        raise ValidationError('Unsafe package filename')
    return value


def _year(value):
    if type(value) is not int or not 1<=value<=9999: raise ValidationError('Invalid snapshot year')
    return value


@dataclass(frozen=True)
class LocalScimagoPackage:
    root: Path
    manifest: dict
    years: tuple
    latest_year: int
    source_files: dict
    apc_files: dict
    checksums: dict
    delimiter: str
    encoding: str
    checksums_filename: str
    manifest_sha256: str
    checksums_sha256: str

    def path(self,name):
        path=self.root/_filename(name)
        if not path.resolve().is_relative_to(self.root): raise ValidationError('Package path escapes root')
        return path


def parse_scimago_manifest_bytes(manifest_bytes):
    """Pure shared Stage04/05 semantic plan; no file or network access."""
    try:
        manifest=json.loads(manifest_bytes.decode('utf-8-sig'))
    except (ValueError,UnicodeError): raise ValidationError('Cannot read valid local manifest') from None
    if not isinstance(manifest,dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] not in (1,2):
        raise ValidationError('Unsupported manifest schema version')
    try:
        cfg=manifest['sjr']; version=manifest['schema_version']
        if cfg.get('primary_source_key','Sourceid')!='Sourceid': raise ValidationError('Unsupported SCImago source-key profile')
        if version==1:
            first,last=_year(cfg['first_year']),_year(cfg['last_year'])
            if first>last: raise ValidationError('Reversed annual range')
            years=list(range(first,last+1)); pattern=cfg['url_pattern']
        else:
            years=cfg['available_years']; pattern=cfg['file_pattern']
            if not isinstance(years,list) or not years: raise ValidationError('Annual years are required')
            for y in years: _year(y)
            if years!=sorted(set(years)): raise ValidationError('Annual years must be sorted and unique')
        latest=_year(cfg['latest_snapshot_year'])
        if latest not in years or (version==2 and latest!=max(years)): raise ValidationError('Invalid manifest latest year')
        if not isinstance(pattern,str) or pattern.count('{year}')!=1 or any(c in pattern.replace('{year}','') for c in '{}'):
            raise ValidationError('Invalid annual filename pattern')
        source_files={y:_filename(pattern.replace('{year}',str(y))) for y in years}
        jv=manifest.get('journal_value') or {}; apc={}
        snapshots=jv.get('snapshots',[]) if version==2 else ([{'year':jv['year'],'file':jv['file']}] if jv else [])
        if not isinstance(snapshots,list): raise ValidationError('Invalid Journal Value snapshots')
        for row in snapshots:
            y=_year(row['year'])
            if y in apc: raise ValidationError('Duplicate Journal Value year')
            apc[y]=_filename(row['file'])
        checksum_cfg=manifest['checksums']
        if str(checksum_cfg['algorithm']).lower()!='sha256' or checksum_cfg.get('verification_required',True) is not True:
            raise ValidationError('SHA-256 verification is mandatory')
        checksum_file=_filename(checksum_cfg['file'])
        delimiter=cfg.get('delimiter',';'); encoding=cfg.get('encoding','utf-8')
        if not isinstance(delimiter,str) or len(delimiter)!=1 or not isinstance(encoding,str): raise ValidationError('Invalid CSV format')
        declared=list(source_files.values())+list(apc.values())
        if len(set(declared))!=len(declared) or set(declared)&{'manifest.json',checksum_file}: raise ValidationError('Duplicate or reserved data filename')
        count=(manifest.get('files') or {}).get('expected_total_data_files')
        if count is not None and (type(count) is not int or count!=len(declared)): raise ValidationError('Manifest file-count mismatch')
        return dict(manifest=manifest,years=tuple(years),latest_year=latest,source_files=source_files,apc_files=apc,delimiter=delimiter,encoding=encoding,checksums_filename=checksum_file)
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ValidationError('Invalid or incomplete local SCImago package') from None


def parse_scimago_checksums_bytes(checksum_bytes):
    """Pure exact checksum parser shared by local validation and remote planning."""
    try:
        text=checksum_bytes.decode('utf-8-sig'); hashes={}
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith('#'): continue
            match=re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?(.+?)\s*',line.strip())
            if not match: raise ValidationError('Malformed checksum entry')
            name=_filename(match[2])
            if name in hashes: raise ValidationError('Duplicate checksum entry')
            hashes[name]=match[1].lower()
        return hashes
    except UnicodeError: raise ValidationError('Invalid checksum encoding') from None


def declared_scimago_files(plan,hashes):
    names=sorted(list(plan['source_files'].values())+list(plan['apc_files'].values()))
    if any(name not in hashes for name in names): raise ValidationError('Declared file lacks checksum')
    return {name:hashes[name] for name in names}


def validate_local_package(root: Path) -> LocalScimagoPackage:
    root=Path(root).resolve()
    if not (root/'manifest.json').resolve().is_relative_to(root): raise ValidationError('Manifest escapes package')
    try:
        manifest_bytes=(root/'manifest.json').read_bytes(); plan=parse_scimago_manifest_bytes(manifest_bytes)
        checksum_file=plan['checksums_filename']
        if not (root/checksum_file).resolve().is_relative_to(root): raise ValidationError('Checksums escape package')
        checksum_bytes=(root/checksum_file).read_bytes(); hashes=parse_scimago_checksums_bytes(checksum_bytes)
        declared=declared_scimago_files(plan,hashes)
        package=LocalScimagoPackage(root,**plan,checksums=hashes,manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),checksums_sha256=hashlib.sha256(checksum_bytes).hexdigest())
        for name in declared:
            digest=hashlib.sha256()
            with package.path(name).open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
            if digest.hexdigest()!=hashes[name]: raise ArtifactIntegrityError('Declared file SHA-256 mismatch')
        return package
    except (KeyError,TypeError,ValueError,OSError,LookupError):
        raise ValidationError('Invalid or incomplete local SCImago package') from None


def _number(value,integer=False):
    value=value.strip()
    if value in ('','-'): return None
    try:
        number=float(value.replace(' ','').replace(',','.'))
        if not math.isfinite(number) or number<0 or (integer and not number.is_integer()): raise ValueError()
        return int(number) if integer else number
    except ValueError: raise ValidationError('Malformed SCImago numeric value') from None


def _rows(path,year,dataset,delimiter,encoding):
    codec='utf-8-sig' if encoding.lower().replace('_','-') in ('utf-8','utf-8-sig') else encoding
    try:
        with path.open(encoding=codec,newline='') as stream:
            reader=csv.reader(stream,delimiter=delimiter); headers=next(reader,[]); seen={}; unique=[]
            for h in headers:
                h=h.strip(); seen[h]=seen.get(h,0)+1; unique.append(h if seen[h]==1 else h+'__'+str(seen[h]))
            required=SOURCE_REQUIRED if dataset=='source_list' else {'Sourceid','Title','Type','Issn','Publisher','Open Access','Open Access Diamond','Estimated APC (USD)'}
            if not required<=set(unique) or any(seen.get(h,0)>1 for h in required if h!='Publisher'):
                raise ValidationError('SCImago required semantic columns missing or ambiguous')
            for ordinal,cells in enumerate(reader,start=2):
                if not cells or not any(cells): continue
                if len(cells)>len(unique) and any(cells[len(unique):]): raise ValidationError('Unexpected extra CSV cells')
                cells=cells+['']*max(0,len(unique)-len(cells)); row={h:cells[i].strip() for i,h in enumerate(unique)}
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',row.get('Sourceid','')):
                    raise ValidationError('Missing or malformed Sourceid')
                row['_layout']='header_aligned'
                if dataset=='journal_value' and len(cells)>=13 and headers[:10]==['Rank','Sourceid','Title','Type','Issn','Publisher','Open Access','Open Access Diamond','SJR','SJR Best Quartile'] and headers[10]==f'Total Docs. ({year})' and headers[11:13]==['Estimated APC (USD)','Estimated value (USD)'] and cells[5].strip() in ('Yes','No') and cells[6].strip() in ('Yes','No'):
                    row.update({'Open Access':cells[5].strip(),'Open Access Diamond':cells[6].strip(),'Publisher':cells[7].strip(),'_layout':'known_journal_value_shift_v1'})
                if dataset=='source_list':
                    for field,integer in [('SJR',False),('H index',True),('Rank',True)]: _number(row[field],integer)
                    row['SJR Best Quartile']=row['SJR Best Quartile'].upper()
                    if row['SJR Best Quartile'] not in ('','-','Q1','Q2','Q3','Q4'): raise ValidationError('Unsupported quartile token')
                else:
                    if any(row[field].casefold() not in ('','yes','no') for field in ('Open Access','Open Access Diamond')):
                        raise ValidationError('Unsupported or ambiguous Journal Value column layout')
                    _number(row['Estimated APC (USD)'])
                yield ordinal,row,sorted(set(unique)-required-{'Publisher__2'})
    except (OSError,UnicodeError,csv.Error,LookupError): raise ValidationError('SCImago CSV cannot be parsed') from None


@contextmanager
def _transaction(db):
    if not db.connection.in_transaction:
        with db.transaction(): yield
    else:
        db.execute('SAVEPOINT scimago_import')
        try: yield; db.execute('RELEASE scimago_import')
        except BaseException:
            db.connection.execute('ROLLBACK TO scimago_import'); db.connection.execute('RELEASE scimago_import'); raise


def _import(database,package_root,year,dataset,*,distribution_resolved_commit_sha=None,
        distribution_repository=REPOSITORY,distribution_branch_requested=BRANCH,distribution_repository_path=None,
        source_csv_path=None,delimiter=None,encoding=None,source_locator=None,_validated_package=None):
    package=_validated_package or validate_local_package(package_root); _year(year)
    files=package.source_files if dataset=='source_list' else package.apc_files
    if year not in files: raise ValidationError('Year is not declared for this dataset')
    name=files[year]; source=package.path(name)
    if source_csv_path is not None and Path(source_csv_path).resolve()!=source.resolve(): raise ValidationError('CSV is not the declared package file')
    if distribution_resolved_commit_sha is not None and (not isinstance(distribution_resolved_commit_sha,str) or not re.fullmatch(r'[0-9a-fA-F]{40}',distribution_resolved_commit_sha)): raise ValidationError('Resolved Git commit must be full 40-hex')
    if not isinstance(distribution_repository,str) or not re.fullmatch(r'(?:https://github\.com/)?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',distribution_repository): raise ValidationError('Invalid distribution repository')
    if not isinstance(distribution_branch_requested,str) or not re.fullmatch(r'[A-Za-z0-9_./-]+',distribution_branch_requested): raise ValidationError('Invalid requested branch')
    commit=distribution_resolved_commit_sha.lower() if distribution_resolved_commit_sha else None
    repository_path=distribution_repository_path or 'SCIMAGO/'+name
    if repository_path.startswith(('/','\\')) or '..' in repository_path or '\\' in repository_path: raise ValidationError('Unsafe distribution path')
    foundation=Foundation(database); observations=ObservationStore(database)
    artifact=foundation.artifacts.put_file(source)
    if artifact.artifact_sha256!=package.checksums[name]: raise ArtifactIntegrityError('File changed after package verification')
    immutable=foundation.artifacts.path_for_hash(artifact.artifact_sha256)
    delimiter=delimiter or package.delimiter; encoding=encoding or package.encoding
    unknown=set(); layouts=set(); count=0
    for _,row,extra in _rows(immutable,year,dataset,delimiter,encoding): unknown.update(extra); layouts.add(row['_layout']); count+=1
    control_artifacts=[foundation.artifacts.put_file(package.path(n)) for n in ['manifest.json',package.checksums_filename]]
    if [a.artifact_sha256 for a in control_artifacts]!=[package.manifest_sha256,package.checksums_sha256]:
        raise ArtifactIntegrityError('Control files changed after validation')
    provenance=dict(distribution_repository=distribution_repository,distribution_branch_requested=distribution_branch_requested,
        distribution_resolved_commit_sha=commit,distribution_repository_path=repository_path,immutable_distribution_reference=bool(commit),
        historical_upstream_base_url=sanitize_locator(package.manifest.get('base_url')),manifest_schema_version=package.manifest['schema_version'],
        manifest_available_years=list(package.years),manifest_latest_year=package.latest_year,unknown_optional_columns=sorted(unknown),
        compatibility_layouts=sorted(layouts),row_count=count,apc_is_estimate=dataset=='journal_value',
        manifest_sha256=control_artifacts[0].artifact_sha256,checksums_sha256=control_artifacts[1].artifact_sha256,
        csv_delimiter=delimiter,csv_encoding=encoding)
    locator=source_locator or 'https://github.com/'+distribution_repository.removeprefix('https://github.com/')+'/blob/'+(commit or distribution_branch_requested)+'/'+repository_path
    with _transaction(database):
        provider=database.execute("SELECT * FROM provider WHERE semantic_namespace='scimago'").fetchone()
        provider=dict(provider) if provider else foundation.register_provider('scimago','SCImago')
        for a in [artifact]+control_artifacts: foundation.register_raw_artifact(a)
        snapshot=foundation.create_provider_snapshot(provider['provider_id'],dataset,'FILE',upstream_release=str(year),source_locator=locator,
            mirror_commit_sha=commit,parser_name='scimago-local',parser_version='1',schema_profile='scimago_source_list_v1' if dataset=='source_list' else 'scimago_journal_value_v1',schema_version='1',metadata_json=canonical_json(provenance))
        sid=snapshot['snapshot_id']; foundation.attach_artifact_to_snapshot(sid,artifact.artifact_sha256,'data',0,source_locator=locator)
        for role,a in zip(['manifest','checksums'],control_artifacts): foundation.attach_artifact_to_snapshot(sid,a.artifact_sha256,role,0)
        for ordinal,row,_ in _rows(immutable,year,dataset,delimiter,encoding):
            foundation.add_provider_record(sid,artifact.artifact_sha256,f'row:{ordinal:08d}',provider_native_id=row['Sourceid'],ordinal=ordinal,
                metadata_json=canonical_json(dict(csv_row=ordinal,layout=row['_layout'],snapshot_year=year)))
        foundation.seal_provider_snapshot(sid)
        records=iter(foundation.list_provider_records(sid))
        for (_,row,_),record in zip(_rows(immutable,year,dataset,delimiter,encoding),records):
            rid=record['provider_record_id']; publisher=row.get('Publisher') or row.get('Publisher__2') or None
            fields={k:v for k,v in dict(title=row.get('Title'),publisher_name=publisher,source_type=row.get('Type'),country_code=row.get('Country')).items() if v}
            addresses={'title':'Title','publisher_name':'Publisher','source_type':'Type','country_code':'Country'}
            if fields: observations.add_venue_observation(rid,NORMALIZER,**fields,field_provenance_json=canonical_json({k:'csv:'+addresses[k] for k in fields}))
            observations.add_venue_identifier_observation(rid,NORMALIZER,identifier_namespace='scimago_source_id',identifier_value_original=row['Sourceid'],identifier_value_normalized=row['Sourceid'],source_field_path='csv:Sourceid')
            for issn in re.split(r'[,;|]',row.get('Issn','')):
                if issn.strip(): observations.add_venue_identifier_observation(rid,NORMALIZER,identifier_namespace='issn',identifier_value_original=issn.strip(),identifier_value_normalized=re.sub(r'[^0-9A-Za-z]','',issn).upper() or issn.strip(),source_field_path='csv:Issn')
            def metric(code,value,kind,category=None,unit=None,field=None):
                if value is not None: observations.add_venue_metric_observation(rid,NORMALIZER,metric_namespace='scimago',metric_code=code,period_year=year,category_name=category,value_type=kind,**{'value_'+kind.lower():value},unit=unit,source_field_path='csv:'+(field or code))
            if dataset=='source_list':
                observations.add_venue_membership_observation(rid,NORMALIZER,membership_namespace='scimago',status_code='PRESENT_IN_SNAPSHOT')
                for field,namespace in [('Open Access','scimago_open_access'),('Open Access Diamond','scimago_open_access_diamond')]:
                    if row[field].casefold() in ('yes','no'): observations.add_venue_membership_observation(rid,NORMALIZER,membership_namespace=namespace,status_code=row[field].upper(),source_field_path='csv:'+field)
                metric('SJR',_number(row['SJR']),'REAL',field='SJR'); metric('H_INDEX_REPORTED',_number(row['H index'],True),'INTEGER',field='H index'); metric('RANK',_number(row['Rank'],True),'INTEGER',field='Rank')
                if row['SJR Best Quartile'] in ('Q1','Q2','Q3','Q4'): metric('BEST_QUARTILE',row['SJR Best Quartile'],'TEXT',field='SJR Best Quartile')
                for part in row['Categories'].split(';'):
                    match=re.fullmatch(r'\s*(.+?)\s*\((Q[1-4])\)\s*',part,flags=re.I)
                    if match: metric('QUARTILE',match[2].upper(),'TEXT',category=match[1],field='Categories')
            else: metric('APC_USD_ESTIMATE',_number(row['Estimated APC (USD)']),'REAL',unit='USD',field='Estimated APC (USD)')
        return dict(snapshot=foundation.get_provider_snapshot(sid),provider_records=foundation.list_provider_records(sid),normalizer_profile=dict(normalizer_name=NORMALIZER.normalizer_name,normalizer_version='1',observation_schema_version='1'))


def import_scimago_source_snapshot(database,package_root,snapshot_year,**kwargs):
    return _import(database,package_root,snapshot_year,'source_list',**kwargs)


def import_scimago_apc_snapshot(database,package_root,snapshot_year,**kwargs):
    return _import(database,package_root,snapshot_year,'journal_value',**kwargs)


def iter_import_scimago_package(database,package_root,**kwargs):
    """Import manifest-declared annual data incrementally, without a hard-coded year loop."""
    package=validate_local_package(package_root)
    for year in package.years:
        yield _import(database,package_root,year,'source_list',_validated_package=package,**kwargs)
    for year in sorted(package.apc_files):
        yield _import(database,package_root,year,'journal_value',_validated_package=package,**kwargs)
