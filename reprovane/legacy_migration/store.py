"""Copy-on-write legacy import; Stage03 alone owns publication identity."""
import json
from pathlib import Path
from contextlib import contextmanager
from ..core import ValidationError,ArtifactIntegrityError
from ..core.db import Database
from ..core.foundation import Foundation
from ..core.migrations import apply_migrations
from ..core.ids import utc_now
from ..core.provenance import canonical_json
from ..observations import ObservationStore
from ..observations.store import TABLES
from ..resolution import ResolutionStore
from ..corpus_release.model import digest,sha
from .inventory import audit,plan,files_in,record_rows,native,sensitive,runtime_private
from .normalize import normalize


class LegacyMigrationStore:
    def __init__(self,db):
        from ..corpus_release import CorpusReleaseStore
        self.db=db;self.upstream=CorpusReleaseStore(db);self.foundation=self.upstream.foundation;self.observations=ObservationStore(db);self.resolver=self.upstream.discovery.resolver
        if not hasattr(db,'_legacy_migration_auth'): db._legacy_migration_auth=None
        db.connection.create_function('legacy_migration_authorized',1,lambda mid:int(db._legacy_migration_auth==mid))
        apply_migrations(db.connection,target_version=13)
    @contextmanager
    def authorized(self,mid):
        old=self.db._legacy_migration_auth;self.db._legacy_migration_auth=mid
        try:yield
        finally:self.db._legacy_migration_auth=old
    def rows(self,table,mid):return [dict(r) for r in self.db.execute('SELECT * FROM '+table+' WHERE migration_id=? ORDER BY rowid',(mid,))]
    def get(self,mid):return self.foundation._one('legacy_migration_run','migration_id',mid)
    def _observations(self,record_ids):
        result={}
        for table in TABLES.values():
            rows=[dict(r) for r in self.db.execute('SELECT * FROM '+table+' ORDER BY observation_id') if r['provider_record_id'] in record_ids]
            result[table]=rows
        return result
    def _manifest(self,mid,ctx):
        maps=self.rows('legacy_migration_record_map',mid);records=[self.foundation._one('provider_record','provider_record_id',m['provider_record_id']) for m in maps]
        obs=self._observations({r['provider_record_id'] for r in records})
        return dict(schema='legacy-v4-manifest/1',migration_id=mid,source_fingerprint=ctx['source_fingerprint'],plan_sha256=ctx['plan_sha256'],project_id=ctx['project_id'],revision_id=ctx['revision_id'],snapshot_id=ctx['snapshot_id'],resolution_run_id=ctx['resolution_run_id'],snapshot_sha256=self.foundation.get_provider_snapshot(ctx['snapshot_id'])['content_manifest_sha256'],artifacts=self.rows('legacy_migration_artifact',mid),record_maps=maps,records_sha256=digest(records),observations_sha256=digest(obs),warnings=self.rows('legacy_migration_warning',mid),record_count=len(records),interpretation='LEGACY_V4_IMPORT; incomplete original request provenance; classifications/cursors/OA remain historical, not screening/corpus/fulltext authority.')
    def verify(self,mid,source=None,*,private=()):
        run=self.get(mid)
        if run['state']!='COMPLETE':raise ValidationError('Legacy migration is incomplete')
        p=json.loads(run['plan_json'])
        if digest(p)!=run['plan_sha256'] or p['source_inventory_sha256']!=run['source_fingerprint']:raise ArtifactIntegrityError('Migration plan mismatch')
        if source is not None and audit(source,private=private)!=p['inventory']:raise ArtifactIntegrityError('Legacy source drift')
        self.foundation.verify_provider_snapshot(run['snapshot_id']);self.resolver.verify_resolution_run(run['resolution_run_id'])
        artifacts=self.rows('legacy_migration_artifact',mid)
        if [json.loads(r['inventory_json']) for r in artifacts]!=p['inventory']['files']:raise ArtifactIntegrityError('Migration inventory mismatch')
        maps=self.rows('legacy_migration_record_map',mid)
        for a in artifacts:
            inv=json.loads(a['inventory_json'])
            if digest(inv)!=a['inventory_sha256'] or bool(a['artifact_sha256'])!=inv['copy_evidence']:raise ArtifactIntegrityError('Artifact mapping mismatch')
            if a['artifact_sha256']:
                if a['artifact_sha256']!=inv['sha256']:raise ArtifactIntegrityError('Raw source SHA mismatch')
                stored=self.foundation.artifacts.verify(a['artifact_sha256']);body=(self.db.workspace/stored.relative_path).read_bytes()
                rows=record_rows(Path(a['relative_path']),body) if inv['recognized_type']=='OPENALEX_ROWS' else []
                expected=[(i,r) for i,r in enumerate(rows) if native(r)]
                actual=[m for m in maps if m['relative_path']==a['relative_path']]
                if len(actual)!=len(expected):raise ArtifactIntegrityError('Legacy record map count mismatch')
                for m,(ordinal,row) in zip(actual,expected):
                    rec=self.foundation._one('provider_record','provider_record_id',m['provider_record_id'])
                    if m['row_ordinal']!=ordinal or m['original_row_sha256']!=digest(row) or rec['provider_native_id']!=native(row) or rec['artifact_sha256']!=a['artifact_sha256'] or rec['record_content_sha256']!=digest(row) or rec['snapshot_id']!=run['snapshot_id'] or rec['record_locator']!=a['relative_path']+'#row='+str(ordinal):raise ArtifactIntegrityError('Legacy record linkage mismatch')
        snapshot=self.foundation.get_provider_snapshot(run['snapshot_id'])
        if snapshot['request_spec_sha256'] is not None or snapshot['acquisition_mode']!='MANUAL_IMPORT' or snapshot['dataset']!='LEGACY_V4_IMPORT':raise ArtifactIntegrityError('Fabricated request provenance')
        expected=self._manifest(mid,run)
        if canonical_json(expected)!=run['manifest_json'] or digest(expected)!=run['manifest_sha256']:raise ArtifactIntegrityError('Migration manifest mismatch')
        return dict(verified=True,migration_id=mid,record_count=len(maps),manifest_sha256=run['manifest_sha256'],source_checked=source is not None,mode='OFFLINE_LEGACY_PROVENANCE_ONLY')


def execute(source,target,planned,*,private=(),fault=None):
    private=runtime_private(private)
    if Path(source).is_symlink() or Path(target).is_symlink():raise ValidationError('Source/target symlink refused')
    source=Path(source).resolve();target=Path(target).resolve()
    if source==target or source.is_relative_to(target) or target.is_relative_to(source):raise ValidationError('Separate non-overlapping source and target required')
    if not isinstance(planned,dict) or set(planned)!={'plan','plan_sha256'} or digest(planned['plan'])!=planned['plan_sha256']:raise ValidationError('Exact canonical dry-run plan required')
    original=planned['plan'];current=plan(source,**dict(project_key=original['target']['project_key'],revision_number=original['target']['revision_number']),private=private)
    if current!=planned:raise ValidationError('Source/plan drift blocks migration')
    if target.is_symlink():raise ValidationError('Target symlink refused')
    if target.exists() and any(p.name not in ('db','.litintel-writer.lock','raw') for p in target.iterdir()):raise ValidationError('Target must be a new/owned migration workspace')
    mid='lm_'+planned['plan_sha256'][:40]
    from ..operations.lock import workspace_lock
    with workspace_lock(target),Database(target) as db:
        store=LegacyMigrationStore(db);old=db.execute('SELECT migration_id FROM legacy_migration_run').fetchall()
        if old:
            if len(old)!=1 or old[0][0]!=mid:raise ValidationError('Target belongs to a different migration identity')
            if store.get(mid)['state']=='COMPLETE':return dict(**store.verify(mid,source,private=private),project_id=store.get(mid)['project_id'],revision_id=store.get(mid)['revision_id'],reused=True)
        else:
            if db.execute('SELECT count(*) FROM review_project').fetchone()[0]:raise ValidationError('Target already has modern projects')
            with db.transaction(),store.authorized(mid):store.foundation._insert('legacy_migration_run',dict(migration_id=mid,source_fingerprint=original['source_inventory_sha256'],plan_json=canonical_json(original),plan_sha256=planned['plan_sha256'],project_id=None,revision_id=None,snapshot_id=None,resolution_run_id=None,state='OPEN',manifest_json=None,manifest_sha256=None,created_at=utc_now()))
        with db.transaction(),store.authorized(mid):
            f=store.foundation;project=f.create_project(original['target']['project_key'],'Legacy v4 evidence import')
            revision=f.create_project_revision(project['project_id'],original['target']['revision_number'],'json',canonical_json(dict(kind='LEGACY_V4_IMPORT',migration_plan_sha256=planned['plan_sha256'])))
            provider=f.register_provider('openalex','OpenAlex legacy v4 import')
            snapshot=f.create_provider_snapshot(provider['provider_id'],'LEGACY_V4_IMPORT','MANUAL_IMPORT',parser_name='legacy-v4-import',parser_version='1',schema_profile='legacy-v4-evidence',schema_version='1',upstream_release=original['inventory']['detected_version'],request_spec_sha256=None,source_locator='legacy-inventory:'+original['source_inventory_sha256'],metadata_json=canonical_json(dict(provenance='LEGACY_INCOMPLETE_REQUEST_PROVENANCE',migration_import_spec_sha256=planned['plan_sha256'])))
            indexed={('redacted-name/'+sha(p.relative_to(source).as_posix().encode()) if sensitive(p.relative_to(source).as_posix().encode(),private) else p.relative_to(source).as_posix()):p for p in files_in(source)}
            ids=[];profiles=set();pending=[]
            for ordinal,inv in enumerate(original['inventory']['files']):
                rel=inv['path'];body=indexed[rel].read_bytes()
                if sha(body)!=inv['sha256']:raise ArtifactIntegrityError('Source changed during migration')
                raw=None
                if inv['copy_evidence']:
                    artifact=f.artifacts.put_bytes(body);f.register_raw_artifact(artifact);raw=artifact.artifact_sha256
                    f.attach_artifact_to_snapshot(snapshot['snapshot_id'],raw,'LEGACY_V4_EVIDENCE',ordinal,source_locator=rel)
                f._insert('legacy_migration_artifact',dict(migration_id=mid,relative_path=rel,inventory_json=canonical_json(inv),inventory_sha256=digest(inv),artifact_sha256=raw))
                for code in sorted({v for v in [inv['warning'], 'REDACTED_SECRET_PRESENT' if sensitive(body,private) else None] if v}):f._insert('legacy_migration_warning',dict(migration_id=mid,relative_path=rel,code=code))
                if raw and inv['recognized_type']=='OPENALEX_ROWS':
                    for n,row in enumerate(record_rows(indexed[rel],body)):
                        oid=native(row)
                        if not oid:continue
                        rec=f.add_provider_record(snapshot['snapshot_id'],raw,rel+'#row='+str(n),provider_native_id=oid,ordinal=len(ids),record_content_sha256=digest(row),metadata_json=canonical_json(dict(source='LEGACY_V4_IMPORT',legacy_location=rel,legacy_row=n,original_request_provenance='INCOMPLETE')))
                        adapter='ATTACHED_OPENALEX_WORK_1' if 'id' in row and 'openalex_id' not in row else 'V4_FLAT_OPENALEX_1';pending.append((rec['provider_record_id'],row));profiles.add('openalex-work' if adapter=='ATTACHED_OPENALEX_WORK_1' else 'legacy-v4-flat');ids.append(rec['provider_record_id'])
                        f._insert('legacy_migration_record_map',dict(migration_id=mid,relative_path=rel,row_ordinal=n,provider_record_id=rec['provider_record_id'],adapter=adapter,original_row_sha256=digest(row)))
            f.seal_provider_snapshot(snapshot['snapshot_id'])
            for record_id,row in pending:normalize(store.observations,record_id,row)
            selector=dict(profiles=[dict(normalizer_name=name,normalizer_version='1',observation_schema_version='1') for name in sorted(profiles or {'legacy-v4-flat'})])
            resolution=store.resolver.create_resolution_run(revision['revision_id'],selector)
            store.resolver.add_resolution_inputs(resolution['resolution_run_id'],ids);store.resolver.generate_candidates(resolution['resolution_run_id']);store.resolver.evaluate_candidates_automatically(resolution['resolution_run_id']);store.resolver.materialize_resolution_run(resolution['resolution_run_id']);store.resolver.seal_resolution_run(resolution['resolution_run_id'])
            if fault:fault()
            if audit(source,private=private)!=original['inventory']:raise ArtifactIntegrityError('Source drift before completion')
            ctx=dict(source_fingerprint=original['source_inventory_sha256'],plan_sha256=planned['plan_sha256'],project_id=project['project_id'],revision_id=revision['revision_id'],snapshot_id=snapshot['snapshot_id'],resolution_run_id=resolution['resolution_run_id'])
            manifest=store._manifest(mid,ctx)
            db.execute("UPDATE legacy_migration_run SET project_id=?,revision_id=?,snapshot_id=?,resolution_run_id=?,state='COMPLETE',manifest_json=?,manifest_sha256=? WHERE migration_id=?",(ctx['project_id'],ctx['revision_id'],ctx['snapshot_id'],ctx['resolution_run_id'],canonical_json(manifest),digest(manifest),mid))
            result=store.verify(mid,source,private=private)
        return dict(**result,project_id=project['project_id'],revision_id=revision['revision_id'],reused=False)
