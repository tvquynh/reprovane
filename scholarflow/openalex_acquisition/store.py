"""Durable page receipts and atomic publication of complete typed snapshots."""
import hashlib,json
from pathlib import Path
from ..core import ValidationError,ArtifactIntegrityError
from ..core.db import Database
from ..core.foundation import Foundation
from ..core.ids import new_id,utc_now
from ..core.migrations import apply_migrations
from ..core.provenance import canonical_json
from ..observations import ObservationStore
from ..providers.scimago import _transaction
from ..venue_intelligence import VenueIntelligenceStore
from .spec import AcquisitionSpec
from .normalize import native_id,normalize_work,source_claims
from .observation_store import AcquisitionObservationStore


class OpenAlexAcquisitionStore:
    def __init__(self,database):
        self.db=database; VenueIntelligenceStore(database)
        self._authorized=None
        database.connection.create_function('openalex_acquisition_authorized',1,lambda aid:int(aid==self._authorized))
        apply_migrations(database.connection,target_version=7)
        self.foundation=Foundation(database); self.observations=AcquisitionObservationStore(database)

    def ensure_revision(self,spec):
        spec=AcquisitionSpec.from_value(spec)
        with _transaction(self.db):
            project=self.db.execute("SELECT * FROM review_project WHERE project_key='openalex-acquisition'").fetchone()
            project=dict(project) if project else self.foundation.create_project('openalex-acquisition','Explicit OpenAlex acquisition')
            revisions=self.foundation.list_project_revisions(project['project_id'])
            for r in revisions:
                if r['manifest_text']==spec.canonical: return r['revision_id']
            return self.foundation.create_project_revision(project['project_id'],len(revisions)+1,'json',spec.canonical)['revision_id']

    def create(self,revision_id,spec):
        spec=AcquisitionSpec.from_value(spec)
        with _transaction(self.db):
            old=self.db.execute('SELECT * FROM openalex_acquisition WHERE project_revision_id=? AND request_spec_sha256=?',(revision_id,spec.sha256)).fetchone()
            if old: return dict(old)
            row=dict(acquisition_id=new_id('oaa'),project_revision_id=revision_id,request_spec_json=spec.canonical,request_spec_sha256=spec.sha256,
                state='OPEN',page_count=0,work_count=0,next_cursor='*',termination=None,error_code=None,snapshot_id=None,source_snapshot_id=None,created_at_utc=utc_now(),updated_at_utc=utc_now())
            return self.foundation._insert('openalex_acquisition',row)

    def get(self,acquisition_id):
        row=self.db.execute('SELECT * FROM openalex_acquisition WHERE acquisition_id=?',(acquisition_id,)).fetchone()
        if row is None: raise ValidationError('Acquisition does not exist')
        return dict(row)

    def pages(self,acquisition_id): return [dict(r) for r in self.db.execute('SELECT * FROM openalex_acquisition_page WHERE acquisition_id=? ORDER BY page_index',(acquisition_id,))]

    def _update(self,aid,**values):
        self._authorized=aid
        try: self.db.execute('UPDATE openalex_acquisition SET '+','.join(k+'=?' for k in values)+',updated_at_utc=? WHERE acquisition_id=?',(*values.values(),utc_now(),aid))
        finally: self._authorized=None

    @staticmethod
    def _validate_page(data,params):
        meta=data.get('meta'); results=data.get('results')
        if not isinstance(meta,dict) or 'next_cursor' not in meta or not isinstance(results,list) or len(results)>params['per_page']: raise ValidationError('Invalid OpenAlex page envelope/count')
        cursor=meta['next_cursor']
        if cursor is not None and (not isinstance(cursor,str) or not cursor or len(cursor)>16384): raise ValidationError('Invalid next cursor')
        for work in results:
            if not isinstance(work,dict): raise ValidationError('Invalid OpenAlex work object')
            native_id(work.get('id'))
        return results,cursor

    def _replay(self,run):
        works={}; cursor='*'; pages=self.pages(run['acquisition_id']); seen=set()
        for expected,page in enumerate(pages,1):
            if page['page_index']!=expected or page['cursor_in']!=cursor: raise ArtifactIntegrityError('Acquisition page chain mismatch')
            artifact=self.foundation.artifacts.verify(page['artifact_sha256'])
            if artifact.byte_size!=page['byte_size']: raise ArtifactIntegrityError('Acquisition page size mismatch')
            data=json.loads(self.foundation.artifacts.path_for_hash(page['artifact_sha256']).read_bytes())
            params=json.loads(page['request_json']); results,cursor=self._validate_page(data,params)
            if params!=AcquisitionSpec(run['request_spec_json']).parameters(page['cursor_in'],AcquisitionSpec(run['request_spec_json']).as_dict()['max_records']-len(works)) or cursor!=page['cursor_out']: raise ArtifactIntegrityError('Acquisition request/page identity mismatch')
            self._collect(works,results,expected,page['artifact_sha256'])
            if cursor in seen: raise ArtifactIntegrityError('Repeated cursor')
            seen.add(page['cursor_in'])
        if len(pages)!=run['page_count'] or len(works)!=run['work_count'] or cursor!=run['next_cursor']: raise ArtifactIntegrityError('Acquisition progress mismatch')
        return works

    @staticmethod
    def _collect(works,results,page_index,artifact):
        for i,work in enumerate(results):
            oid=native_id(work['id']); anchor=dict(page_index=page_index,artifact_sha256=artifact,pointer=f'/results/{i}')
            if oid not in works: works[oid]=dict(work=work,anchors=[])
            works[oid]['anchors'].append(anchor)

    def acquire(self,spec,client,*,project_revision_id=None):
        spec=AcquisitionSpec.from_value(spec); client.private.check(spec.canonical)
        revision=project_revision_id or self.ensure_revision(spec)
        run=self.create(revision,spec)
        return self.resume(run['acquisition_id'],spec,client)

    def resume(self,acquisition_id,spec,client):
        spec=AcquisitionSpec.from_value(spec); client.private.check(spec.canonical); run=self.get(acquisition_id)
        if run['request_spec_sha256']!=spec.sha256 or run['request_spec_json']!=spec.canonical: raise ValidationError('Resume requires the identical acquisition specification')
        try:
            works=self._replay(run)
            if run['state']=='COMPLETE':
                self.foundation.verify_provider_snapshot(run['snapshot_id'])
                if run['source_snapshot_id']: self.foundation.verify_provider_snapshot(run['source_snapshot_id'])
                return run
            body=spec.as_dict()
            while run['termination'] is None:
                if run['next_cursor']!='*' and not client.api_key: raise ValidationError('API key required for cursor continuation')
                params=spec.parameters(run['next_cursor'],body['max_records']-len(works)); client.private.check(params)
                page=client.request_page(params); results,next_cursor=self._validate_page(page.data,params)
                if next_cursor is not None and next_cursor in {p['cursor_in'] for p in self.pages(acquisition_id)}|{run['next_cursor']}: raise ValidationError('OpenAlex cursor cycle')
                artifact=self.foundation.artifacts.put_bytes(page.body); new_works={oid:dict(work=value['work'],anchors=list(value['anchors'])) for oid,value in works.items()}
                self._collect(new_works,results,run['page_count']+1,artifact.artifact_sha256)
                termination='NATURAL' if not results or next_cursor is None else ('RECORD_LIMIT' if len(new_works)>=body['max_records'] else ('PAGE_LIMIT' if run['page_count']+1>=body['max_pages'] else None))
                with _transaction(self.db):
                    self.foundation.register_raw_artifact(artifact)
                    self._authorized=acquisition_id
                    try:
                        self.foundation._insert('openalex_acquisition_page',dict(acquisition_id=acquisition_id,page_index=run['page_count']+1,cursor_in=run['next_cursor'],cursor_out=next_cursor,
                            artifact_sha256=artifact.artifact_sha256,byte_size=artifact.byte_size,request_json=canonical_json(params),response_metadata_json=canonical_json(dict(http_status=200,rate=page.rate,parser_name='openalex-acquisition',parser_version='1')),raw_record_count=len(results),retrieved_at_utc=utc_now()))
                        self._update(acquisition_id,state='OPEN',page_count=run['page_count']+1,work_count=len(new_works),next_cursor=next_cursor,termination=termination,error_code=None)
                    finally: self._authorized=None
                works=new_works; run=self.get(acquisition_id)
            return self._finalize(run,works)
        except BaseException:
            if self.get(acquisition_id)['state']!='COMPLETE':
                with _transaction(self.db): self._update(acquisition_id,state='INTERRUPTED',error_code='ACQUISITION_INCOMPLETE')
            raise

    def _record(self,sid,aid,oid,artifact,pointer,metadata):
        rid='rec_'+hashlib.sha256(canonical_json([aid,oid]).encode()).hexdigest()[:32]
        # Same foundation row/guards, with a deterministic acquisition-scoped record ID.
        return self.foundation._insert('provider_record',dict(provider_record_id=rid,snapshot_id=sid,provider_native_id=oid,artifact_sha256=artifact,record_locator=pointer,
            ordinal=None,record_content_sha256=None,observed_at_utc=utc_now(),metadata_json=canonical_json(dict(openalex_acquisition=aid,**metadata))))

    def _finalize(self,run,works):
        aid=run['acquisition_id']; pages=self.pages(aid)
        # Re-verify exact bytes immediately before atomic semantic publication.
        if self._replay(run)!=works: raise ArtifactIntegrityError('Acquisition replay mismatch')
        with _transaction(self.db):
            current=self.get(aid)
            if current['state']=='COMPLETE': return current
            provider=self.db.execute("SELECT * FROM provider WHERE semantic_namespace='openalex'").fetchone()
            provider=dict(provider) if provider else self.foundation.register_provider('openalex','OpenAlex')
            sources={}
            for page in pages:
                data=json.loads(self.foundation.artifacts.path_for_hash(page['artifact_sha256']).read_bytes())
                for index,work in enumerate(data['results']):
                    source=(work.get('primary_location') or {}).get('source')
                    if isinstance(source,dict) and source.get('id'):
                        oid=native_id(source['id'],'S')
                        if oid not in sources: sources[oid]=dict(source=source,anchors=[])
                        sources[oid]['anchors'].append(dict(page_index=page['page_index'],artifact_sha256=page['artifact_sha256'],pointer=f'/results/{index}',source_pointer=f'/results/{index}/primary_location/source'))
            snapshots={}
            for dataset in (['works','embedded_sources'] if sources else ['works']):
                metadata=dict(acquisition_id=aid,project_revision_id=run['project_revision_id'],request_spec=json.loads(run['request_spec_json']),termination=run['termination'],page_count=len(pages),
                    unique_works=len(works),unique_sources=len(sources),duplicate_work_appearances=sum(p['raw_record_count'] for p in pages)-len(works),normalizer_profile='openalex-work/1/1')
                snapshot=self.foundation.create_provider_snapshot(provider['provider_id'],dataset,'API',parser_name='openalex-acquisition',parser_version='1',schema_profile='openalex_'+dataset+'_v1',schema_version='1',
                    source_locator='https://api.openalex.org/works',request_spec_sha256=run['request_spec_sha256'],metadata_json=canonical_json(metadata))
                for p in pages:
                    self.foundation.attach_artifact_to_snapshot(snapshot['snapshot_id'],p['artifact_sha256'],'api_page',p['page_index'],source_locator='https://api.openalex.org/works',request_metadata_json=p['request_json'],response_metadata_json=p['response_metadata_json'])
                snapshots[dataset]=snapshot
            normalized=[]
            for oid,value in works.items():
                work=value['work']; a=value['anchors'][0]
                metadata=dict(entity_type='work',occurrences=value['anchors'])
                if 'cited_by_count' in work: metadata['reported_cited_by_count']=work['cited_by_count']
                record=self._record(snapshots['works']['snapshot_id'],aid,oid,a['artifact_sha256'],f"page:{a['page_index']}"+a['pointer'],metadata)
                normalized.append((record,work))
            normalized_sources=[]
            for oid,value in sources.items():
                a=value['anchors'][0]; record=self._record(snapshots['embedded_sources']['snapshot_id'],aid,oid,a['artifact_sha256'],f"page:{a['page_index']}"+a['source_pointer'],dict(entity_type='source',occurrences=value['anchors']))
                normalized_sources.append((record,value['source']))
            # Accepted observation triggers REQUIRE a sealed anchor. Both the seal
            # and all typed writes are inside this one transaction: never visible partially.
            for snapshot in snapshots.values(): self.foundation.seal_provider_snapshot(snapshot['snapshot_id'])
            for record,work in normalized: normalize_work(self.observations,record['provider_record_id'],work)
            for record,source in normalized_sources: source_claims(self.observations,record['provider_record_id'],source)
            if len(self.foundation.list_provider_records(snapshots['works']['snapshot_id']))!=run['work_count']: raise ValidationError('Acquisition record reconciliation failed')
            self._update(aid,state='COMPLETE',snapshot_id=snapshots['works']['snapshot_id'],source_snapshot_id=snapshots.get('embedded_sources',{}).get('snapshot_id'),error_code=None)
            self.foundation.append_audit_event('OPENALEX_ACQUISITION_COMPLETE','provider_snapshot',snapshots['works']['snapshot_id'],'SYSTEM',payload_json=canonical_json(dict(acquisition_id=aid,termination=run['termination'],works=len(works))))
            return self.get(aid)
