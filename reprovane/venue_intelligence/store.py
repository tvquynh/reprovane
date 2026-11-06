"""Atomic/idempotent typed imports over sealed Stage05 pinned file snapshots."""
import hashlib,json,re
import sqlite3
from contextlib import nullcontext
from collections import Counter,defaultdict
from pathlib import Path
from ..core import ValidationError,ArtifactIntegrityError
from ..core.foundation import Foundation
from ..core.ids import new_id
from ..core.migrations import apply_migrations
from ..core.provenance import canonical_json
from ..observations import ObservationStore,NormalizationProvenance
from ..observations.validation import object_json,text
from ..providers.scimago import _transaction
from ..venue_resolution import VenueResolutionStore
from ..mirror import MirrorCache
from ..mirror.pinned_files import sync_pinned_files,verify_pinned_files
from .parsers import parse_rows

SCOPUS_PATH='SCOPUS/ext_list_Jul_2026.csv'
COLLECTIONS=('SCIE','SSCI','AHCI','ESCI')
DEFAULT_FILES=(SCOPUS_PATH,*(f'WOS/{c}.csv' for c in COLLECTIONS),'SCOPUS/README.md','WOS/README.md')
PROFILES=[dict(normalizer_name=p+'-venue-list',normalizer_version='1',observation_schema_version='1') for p in ['scopus','wos']]


def sync_venue_lists(workspace,*,scopus_path=SCOPUS_PATH,**kwargs):
    return sync_pinned_files(workspace,(scopus_path,*DEFAULT_FILES[1:]),**kwargs)


class VenueIntelligenceStore:
    def __init__(self,database):
        self.db=database; self._venues=None
        try: database.connection.execute("SELECT venue_authorized('', '')")
        except sqlite3.OperationalError: self._venues=VenueResolutionStore(database)
        self.observations=ObservationStore(database)
        apply_migrations(database.connection,target_version=6)

    @property
    def venues(self):
        if self._venues is None: self._venues=VenueResolutionStore(self.db)
        return self._venues

    def add_attribute(self,record_id,provenance,namespace,code,value,source_field_path):
        text(record_id); text(source_field_path)
        if namespace not in ('scopus','wos') or not isinstance(provenance,NormalizationProvenance): raise ValidationError('Invalid provider attribute provenance')
        allowed={'scopus':{'ASJC','COVERAGE','LANGUAGES','DISCONTINUED','RAW_STATUS'},'wos':{'CATEGORIES','LANGUAGES','PUBLISHER_ADDRESS'}}
        if code not in allowed[namespace]: raise ValidationError('Attribute code does not belong to provider taxonomy')
        body=canonical_json(value); object_json(body)
        row=dict(observation_id=new_id('vpa'),provider_record_id=record_id,attribute_namespace=namespace,attribute_code=code,
            value_json=body,source_field_path=source_field_path,**provenance.fields())
        with (nullcontext() if self.db.connection.in_transaction else self.db.transaction()):
            self.db.execute('INSERT INTO venue_provider_attribute_observation ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
        return row

    def list_attributes(self,record_id=None):
        return [dict(r) for r in self.db.execute('SELECT * FROM venue_provider_attribute_observation WHERE (? IS NULL OR provider_record_id=?) ORDER BY observation_id',(record_id,record_id))]

    def import_statistics(self,snapshot_id):
        snapshot=Foundation(self.db).get_provider_snapshot(snapshot_id)
        if snapshot['parser_name']!='venue-list-stage06': raise ValidationError('Not a Stage06 venue snapshot')
        return json.loads(snapshot['metadata_json'])['statistics']


def _prepare(root,files,provider,artifact_paths=None):
    groups={}; counts=Counter(); statuses=Counter(); types=Counter(); collections=Counter(); issns=set(); eissns=set(); duplicate=0; invalid=0; unknown=set()
    for path,collection in files:
        for row in parse_rows(artifact_paths[path] if artifact_paths else root/path,provider,collection):
            counts['rows_read']+=1; collections[collection or 'SCOPUS']+=1; invalid+=len(row['invalid_issns']); unknown.update(row['unknown_columns'])
            sig=hashlib.sha256(canonical_json(row['row_signature']).encode()).hexdigest()
            group=groups.setdefault(row['key'],dict(rows={},anchors=[],collections=set()))
            group['anchors'].append(dict(file=path,logical_row=row['ordinal'],collection=collection))
            if sig in group['rows']: duplicate+=1
            else:
                row.pop('row_signature'); row.pop('key'); group['rows'][sig]=row
            if collection: group['collections'].add(collection)
            if row['status']: statuses[row['status']]+=1
            if row['source_type']: types[row['source_type']]+=1
            for identifier in row['identifiers']:
                (issns if identifier['namespace']=='issn' else eissns).add(identifier['normalized'])
    unique_per_collection={c:sum(c in g['collections'] for g in groups.values()) for c in COLLECTIONS} if provider=='wos' else {}
    stats=dict(rows_read=counts['rows_read'],rows_accepted=counts['rows_read'],rows_rejected=0,duplicate_rows_consolidated=duplicate,
        logical_provider_records=len(groups),active=statuses['ACTIVE'],inactive=statuses['INACTIVE'],unknown_status=statuses['UNKNOWN'],
        unique_source_ids=len(groups) if provider=='scopus' else 0,unique_issns=len(issns),unique_eissns=len(eissns),source_type_counts=dict(sorted(types.items())),
        rows_per_collection={c:collections[c] for c in COLLECTIONS} if provider=='wos' else dict(collections),unique_venues_per_collection=unique_per_collection,
        multi_collection_venue_count=sum(len(g['collections'])>1 for g in groups.values()),invalid_issn_claims=invalid,
        unknown_columns=sorted(unknown),asjc_observation_count=0,asjc_code_count=0,category_observation_count=0,category_value_count=0,
        resolution=dict(state='NOT_RUN',matched=None,ambiguous=None,unresolved=None))
    # Deterministic semantic counts deduplicate identical attributes within a logical record.
    for group in groups.values():
        attrs={(code,canonical_json(value)) for row in group['rows'].values() for code,value in row['attributes'].items()}
        for code,body in attrs:
            value=json.loads(body)
            if code=='ASJC': stats['asjc_observation_count']+=1; stats['asjc_code_count']+=len(value['codes'])
            if code=='CATEGORIES': stats['category_observation_count']+=1; stats['category_value_count']+=len(value['values'])
    return list(groups.values()),stats


def import_venue_lists(database,mirror_workspace,snapshot_directory,*,provider='all',scopus_path=SCOPUS_PATH):
    if provider not in ('scopus','wos','all'): raise ValidationError('Unknown venue provider')
    cache=MirrorCache(mirror_workspace); root=Path(snapshot_directory); side=verify_pinned_files(cache,root)
    required=[scopus_path] if provider=='scopus' else [f'WOS/{c}.csv' for c in COLLECTIONS]
    if provider=='all': required=[scopus_path,*required]
    if any(p not in side['files'] for p in required): raise ValidationError('Pinned bundle lacks required provider file')
    store=VenueIntelligenceStore(database); foundation=Foundation(database); observations=store.observations
    artifact_cache={}
    for path in required:
        artifact=foundation.artifacts.put_file(root/path)
        if artifact.artifact_sha256!=side['files'][path]: raise ArtifactIntegrityError('Pinned input changed before parsing')
        artifact_cache[path]=artifact
    immutable_paths={path:foundation.artifacts.path_for_hash(a.artifact_sha256) for path,a in artifact_cache.items()}
    batches=[]
    for p in (['scopus','wos'] if provider=='all' else [provider]):
        files=[(scopus_path,None)] if p=='scopus' else [(f'WOS/{c}.csv',c) for c in COLLECTIONS]
        identity=dict(provider=p,repository=side['repository'],commit=side['resolved_commit_sha'],files={path:side['files'][path] for path,_ in files},parser='venue-list-stage06/1')
        key=hashlib.sha256(canonical_json(identity).encode()).hexdigest()
        groups,stats=_prepare(root,files,p,immutable_paths)  # Parse the exact retained CAS bytes before any semantic transaction.
        batches.append((p,files,key,groups,stats))
    result=[]
    with _transaction(database):
        for p,files,key,groups,stats in batches:
            existing=database.execute("SELECT * FROM provider_snapshot WHERE parser_name='venue-list-stage06' AND json_extract(metadata_json,'$.stage06_import_key')=?",(key,)).fetchone()
            if existing is not None:
                if not existing['sealed_at_utc']: raise ValidationError('Incomplete existing Stage06 import')
                foundation.verify_provider_snapshot(existing['snapshot_id'])
                result.append(dict(snapshot=dict(existing),statistics=json.loads(existing['metadata_json'])['statistics'],reused=True)); continue
            previous=database.execute('SELECT * FROM provider WHERE semantic_namespace=?',(p,)).fetchone()
            prov=dict(previous) if previous else foundation.register_provider(p,p.upper())
            artifacts={}
            for path,_ in files:
                a=artifact_cache[path]
                if a.artifact_sha256!=side['files'][path]: raise ArtifactIntegrityError('Pinned file changed before import')
                foundation.register_raw_artifact(a); artifacts[path]=a
            release=Path(scopus_path).stem.removeprefix('ext_list_') if p=='scopus' else None
            metadata=dict(stage06_import_key=key,distribution_repository=side['repository'],distribution_branch_requested=side['requested_ref'],
                distribution_resolved_commit_sha=side['resolved_commit_sha'],immutable_distribution_reference=True,
                files={path:side['files'][path] for path,_ in files},provider_snapshot_version=release,statistics=stats,
                wos_row_union='exact normalized title + identical ISSN/eISSN claims; missing valid IDs require exact complete row',
                integrity_basis=side['integrity_basis'])
            snapshot=foundation.create_provider_snapshot(prov['provider_id'],'source_titles' if p=='scopus' else 'core_collection','MIRROR',
                upstream_release=release,mirror_commit_sha=side['resolved_commit_sha'],source_locator='https://github.com/'+side['repository']+'/tree/'+side['resolved_commit_sha']+'/'+p.upper(),
                parser_name='venue-list-stage06',parser_version='1',schema_profile=p+'_venue_list_v1',schema_version='1',metadata_json=canonical_json(metadata))
            sid=snapshot['snapshot_id']
            for ordinal,(path,_) in enumerate(files):
                foundation.attach_artifact_to_snapshot(sid,artifacts[path].artifact_sha256,'provider_csv',ordinal,
                    source_locator='https://github.com/'+side['repository']+'/blob/'+side['resolved_commit_sha']+'/'+path)
            records=[]
            for ordinal,group in enumerate(groups):
                anchor=group['anchors'][0]; first=next(iter(group['rows'].values()))
                record=foundation.add_provider_record(sid,artifacts[anchor['file']].artifact_sha256,anchor['file']+f":row:{anchor['logical_row']:08d}",
                    provider_native_id=first['native'],ordinal=ordinal,metadata_json=canonical_json(dict(raw_row_anchors=group['anchors'])))
                records.append((record,group))
            foundation.seal_provider_snapshot(sid)
            norm=NormalizationProvenance(p+'-venue-list','1','1')
            for record,group in records:
                rid=record['provider_record_id']; seen=set()
                def once(kind,body,action):
                    signature=(kind,canonical_json(body))
                    if signature not in seen: action(); seen.add(signature)
                for row in group['rows'].values():
                    description=dict(title=row['title'],publisher_name=row['publisher'],source_type=row['source_type'])
                    once('venue',description,lambda:observations.add_venue_observation(rid,norm,**description,field_provenance_json=canonical_json(dict(source='csv',provider=p))))
                    for identifier in row['identifiers']:
                        once('id',identifier,lambda i=identifier:observations.add_venue_identifier_observation(rid,norm,identifier_namespace=i['namespace'],identifier_value_original=i['original'],identifier_value_normalized=i['normalized'],source_field_path='csv:'+i['field']))
                    if row['native']:
                        once('native',row['native'],lambda:observations.add_venue_identifier_observation(rid,norm,identifier_namespace='scopus_source_id',identifier_value_original=row['native'],identifier_value_normalized=row['native'],source_field_path='csv:Sourcerecord ID'))
                    if p=='scopus':
                        once('membership',row['status'],lambda:observations.add_venue_membership_observation(rid,norm,membership_namespace='scopus',status_code=row['status'],source_field_path='csv:Active or Inactive'))
                    for code,value in row['attributes'].items():
                        fields={'ASJC':'All Science Journal Classification Codes (ASJC)','CATEGORIES':'Web of Science Categories','COVERAGE':'Coverage',
                            'RAW_STATUS':'Active or Inactive','DISCONTINUED':'Titles Discontinued by Scopus','PUBLISHER_ADDRESS':'Publisher address',
                            'LANGUAGES':'Article Language in Source (Three-Letter ISO Language Codes)' if p=='scopus' else 'Languages'}
                        once('attribute:'+code,value,lambda c=code,v=value:store.add_attribute(rid,norm,p,c,v,'csv:'+fields[c]))
                for collection in sorted(group['collections']):
                    observations.add_venue_membership_observation(rid,norm,membership_namespace='wos',collection_code=collection,status_code='PRESENT_IN_SNAPSHOT',source_field_path='file:WOS/'+collection+'.csv')
            foundation.append_audit_event('VENUE_IMPORT','provider_snapshot',sid,'SYSTEM',payload_json=canonical_json(dict(import_key=key,statistics=stats)))
            result.append(dict(snapshot=foundation.get_provider_snapshot(sid),statistics=stats,reused=False))
    return result
