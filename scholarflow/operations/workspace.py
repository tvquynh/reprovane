"""Offline diagnostics and backup-first upgrades; inspection never opens the source DB writable."""
import hashlib,json,os,shutil,sqlite3,sys,tempfile
from contextlib import contextmanager
from pathlib import Path
from ..core import ValidationError,ArtifactIntegrityError,MigrationDriftError
from ..core.db import Database
from ..core.migrations import MIGRATION_DIR
from ..core.provenance import canonical_json
from ..corpus_release.model import digest,sha
from .lock import workspace_lock,lock_diagnosis


def history(connection):
    migrations=[dict(version=int(p.name[:4]),name=p.name,sha256=sha(p.read_bytes())) for p in sorted(MIGRATION_DIR.glob('*.sql'))]
    rows=[dict(r) for r in connection.execute('SELECT version,name,sha256 FROM schema_migration ORDER BY version')]
    if rows!=migrations[:len(rows)] or connection.execute('PRAGMA user_version').fetchone()[0]!=len(rows):raise MigrationDriftError('Historical migration drift')
    return dict(schema_version=len(rows),latest_version=len(migrations),migrations=migrations,pending=[m['name'] for m in migrations[len(rows):]])


def snapshot_database(workspace,target):
    root=Path(workspace);path=root/'db/litintel.sqlite'
    if not path.is_file() or path.is_symlink() or root.is_symlink() or (root/'db').is_symlink():raise ValidationError('Existing ordinary workspace database required')
    if lock_diagnosis(root) in ('ACTIVE_WRITER_OR_INACCESSIBLE','UNSAFE_LOCK_PATH'):raise ValidationError('Quiescent workspace required for read snapshot')
    files=[path,Path(str(path)+'-wal')]
    def fingerprint():return {p.name:sha(p.read_bytes()) for p in files if p.exists()}
    before=fingerprint();dest=Path(target)/'db';dest.mkdir(parents=True)
    for p in files:
        if p.exists():
            if p.is_symlink():raise ValidationError('Database sidecar symlink refused')
            shutil.copyfile(p,dest/p.name)
    if fingerprint()!=before:raise ArtifactIntegrityError('Database changed while snapshotting')
    return before


@contextmanager
def read_database(workspace):
    # WAL recovery/schema registration happens only in a disposable copy. CAS stays read-only by convention.
    with tempfile.TemporaryDirectory(prefix='scholarflow-read-') as folder:
        snapshot_database(workspace,folder)
        with Database(Path(folder)) as db:
            db.workspace=Path(workspace).resolve()
            yield db


def initialize(workspace):
    from ..legacy_migration import LegacyMigrationStore
    with workspace_lock(workspace),Database(Path(workspace)) as db:
        LegacyMigrationStore(db);result=history(db.connection)
        if db.execute('PRAGMA foreign_key_check').fetchone():raise ArtifactIntegrityError('Foreign key check failed')
        return dict(initialized=True,**result)


def verify_sealed(db,full=False):
    from ..fulltext_acquisition import FullTextStore
    from ..legacy_migration import LegacyMigrationStore
    from ..frontier import FrontierStore
    from ..search_validation import SearchValidationStore
    from ..venue_resolution import VenueResolutionStore
    ft=FullTextStore(db);legacy=LegacyMigrationStore(db);foundation=ft.releases.foundation
    routes=[('provider_snapshot','snapshot_id','sealed_at_utc IS NOT NULL',foundation.verify_provider_snapshot),
        ('resolution_run','resolution_run_id','sealed_at_utc IS NOT NULL',ft.releases.discovery.resolver.verify_resolution_run),
        ('venue_resolution_run','venue_resolution_run_id','sealed_at_utc IS NOT NULL',VenueResolutionStore(db).verify_venue_resolution_run),
        ('discovery_run','discovery_run_id',"state='SEALED'",ft.releases.discovery.verify),
        ('frontier_run','frontier_run_id',"state='SEALED'",FrontierStore(db).verify),
        ('search_validation_run','validation_run_id',"state='SEALED'",SearchValidationStore(db).verify),
        ('screening_run','screening_run_id',"state='SEALED'",ft.releases.screening.verify),
        ('corpus_release','release_id',"state='SEALED'",ft.releases.verify),
        ('fulltext_run','run_id',"state='SEALED'",ft.verify),
        ('legacy_migration_run','migration_id',"state='COMPLETE'",legacy.verify)]
    result={}
    for table,key,predicate,verify in routes:
        ids=[r[0] for r in db.execute('SELECT '+key+' FROM '+table+' WHERE '+predicate+' ORDER BY '+key)]
        selected=ids if full else ids[:1]
        for rid in selected:verify(rid)
        result[table]=dict(sealed_count=len(ids),verified_count=len(selected))
    return result


def doctor(workspace=None,*,full=False):
    import importlib.util
    probe=sqlite3.connect(':memory:')
    try:
        probe.execute('PRAGMA foreign_keys=ON');features=dict(backup=hasattr(sqlite3.Connection,'backup'),foreign_keys=probe.execute('PRAGMA foreign_keys').fetchone()[0]==1,json1=probe.execute("SELECT json_valid('{}')").fetchone()[0]==1)
    finally:probe.close()
    result=dict(schema='offline-doctor/1',offline=True,python_supported=sys.version_info>=(3,11),sqlite_version=sqlite3.sqlite_version,sqlite_features=features,configuration={name:('PRESENT_IN_PROCESS' if os.environ.get(name) else 'NOT_IN_PROCESS_USER_ENV_NOT_READ') for name in ('OPENALEX_API_KEY','UNPAYWALL_EMAIL')},dependencies={name:bool(importlib.util.find_spec(name)) for name in ('requests','pypdf')})
    if workspace is None:return result
    root=Path(workspace)
    result.update(workspace_exists=root.is_dir(),workspace_writable=os.access(root,os.W_OK) if root.exists() else False,lock=lock_diagnosis(root),legacy_detected=any(root.glob('*_raw.jsonl')) if root.is_dir() else False)
    if not (root/'db/litintel.sqlite').is_file():result['database']='ABSENT';return result
    with read_database(root) as db:
        result.update(history(db.connection));result['foreign_key_violations']=len(db.execute('PRAGMA foreign_key_check').fetchall())
        if result['foreign_key_violations']:raise ArtifactIntegrityError('Foreign key check failed')
        result['cas_registered']=db.execute('SELECT count(*) FROM raw_artifact').fetchone()[0]
        cas_root=root/'raw'/'sha256';structure_count=0
        if (root/'raw').is_symlink() or cas_root.is_symlink():raise ArtifactIntegrityError('CAS symlink directory refused')
        for entry in cas_root.rglob('*') if cas_root.exists() else []:
            if entry.is_symlink():raise ArtifactIntegrityError('CAS symlink refused')
            if entry.is_file():
                if len(entry.name)!=64 or any(c not in '0123456789abcdef' for c in entry.name) or entry.parent.name!=entry.name[:2]:raise ArtifactIntegrityError('Invalid CAS structure')
                structure_count+=1
        result['cas_objects_with_valid_structure']=structure_count
        from ..core.artifacts import ContentAddressedArtifactStore
        cas=ContentAddressedArtifactStore(root)
        hashes=[r[0] for r in db.execute('SELECT artifact_sha256 FROM raw_artifact ORDER BY artifact_sha256')]
        selected=hashes if full else hashes[:1]
        for key in selected:cas.verify(key)
        result['cas_verified']=len(selected);result['sealed_integrity']=verify_sealed(db,full)
    return result


def upgrade(source,target,backup):
    from ..legacy_migration import LegacyMigrationStore
    if any(Path(p).is_symlink() for p in (source,target,backup)):raise ValidationError('Upgrade symlink roots refused')
    source=Path(source).resolve();target=Path(target).resolve();backup=Path(backup).resolve()
    def source_hashes():return {p.name:sha(p.read_bytes()) for p in (source/'db').glob('litintel.sqlite*') if not p.name.endswith('-shm')}
    original_hashes=source_hashes()
    paths=[source,target,backup]
    if any(a==b or a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(paths) for b in paths[i+1:]):raise ValidationError('Separate source/target/backup directories required')
    if target.exists():
        if (target/'upgrade_manifest.json').is_file():
            manifest=json.loads((target/'upgrade_manifest.json').read_text(encoding='utf-8'))
            if manifest['source_database_inventory']!=original_hashes or sha((backup/'database.sqlite').read_bytes())!=manifest['backup_database_sha256']:raise ArtifactIntegrityError('Upgrade replay source/backup mismatch')
            doctor(target,full=True);return dict(reused=True,**manifest)
        raise ValidationError('New target required')
    if backup.exists():raise ValidationError('New backup destination required')
    # Source inspection is a read-only temporary copy, including pending WAL bytes.
    with read_database(source) as db:
        prior=history(db.connection);backup.mkdir(parents=True)
        destination=backup/'database.sqlite'
        out=sqlite3.connect(destination)
        try:db.connection.backup(out)
        finally:out.close()
        backup_sha=sha(destination.read_bytes())
        backup_info=dict(schema='workspace-backup/1',database_sha256=backup_sha,original_schema=prior['schema_version'],cas_policy='IMMUTABLE_CAS_NOT_DUPLICATED_IN_BACKUP; verified target copy holds CAS',private_config_policy='NOT_COPIED_RECONFIGURE_RUNTIME',migration_history=prior['migrations'][:prior['schema_version']])
        (backup/'backup_manifest.json').write_text(canonical_json(backup_info)+'\n',encoding='utf-8')
        if sha(destination.read_bytes())!=backup_sha:raise ArtifactIntegrityError('Backup SHA mismatch')
    target.mkdir(parents=True);(target/'db').mkdir();shutil.copyfile(destination,target/'db/litintel.sqlite')
    # Only immutable content store and portable JSON manifests; never copy private config/log/profile files.
    if (source/'raw').exists():
        if any(p.is_symlink() for p in (source/'raw').rglob('*')):raise ValidationError('CAS symlink refused')
        shutil.copytree(source/'raw',target/'raw')
    with workspace_lock(target),Database(target) as db:
        LegacyMigrationStore(db);after=history(db.connection)
        if db.execute('PRAGMA foreign_key_check').fetchone():raise ArtifactIntegrityError('Upgrade FK failure')
        verified=verify_sealed(db,full=True)
    if source_hashes()!=original_hashes:raise ArtifactIntegrityError('Upgrade source drift')
    manifest=dict(schema='workspace-upgrade/1',source_database_inventory=original_hashes,backup_database_sha256=backup_sha,from_version=prior['schema_version'],to_version=after['schema_version'],historical_hashes_verified=True,foreign_keys_verified=True,sealed_verified=verified,source_unchanged=True)
    (target/'upgrade_manifest.json').write_text(canonical_json(manifest)+'\n',encoding='utf-8')
    return manifest
