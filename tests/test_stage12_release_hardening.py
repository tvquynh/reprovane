"""Final operational gates; historical tests remain unchanged."""
import ast,contextlib,hashlib,io,json,os,shutil,sqlite3,subprocess,sys,tempfile,tomllib,unittest
from pathlib import Path
from unittest.mock import patch
from stage12_fixtures import LEGACY,e2e
from reprovane import __version__
from reprovane.__main__ import main,ROUTES
from reprovane.core import FoundationError,ValidationError,ArtifactIntegrityError,MigrationDriftError
from reprovane.core.db import Database
from reprovane.core.foundation import Foundation
from reprovane.core.migrations import MIGRATION_DIR
from reprovane.legacy_migration import audit,plan,execute,LegacyMigrationStore
from reprovane.legacy_migration.inventory import sensitive,LIMITS
from reprovane.operations.workspace import doctor,initialize,upgrade,read_database,history
from reprovane.operations.lock import workspace_lock,lock_diagnosis
from reprovane.corpus_release.model import digest,sha

ROOT=Path(__file__).parents[1]


def file_hashes(root):return {p.relative_to(root).as_posix():sha(p.read_bytes()) for p in Path(root).rglob('*') if p.is_file()}


class TempCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.src=self.root/'legacy source';shutil.copytree(LEGACY,self.src);self.target=self.root/'modern workspace'
    def tearDown(self):self.temp.cleanup()
    def migrate(self):self.planned=plan(self.src);self.result=execute(self.src,self.target,self.planned);return self.result


class TestLegacyInventory(TempCase):
    def test_detect_real_flat_schema(self):
        a=audit(self.src);self.assertEqual(a['detected_version'],'4.1.0');self.assertEqual(a['counts']['migration_safe_records'],3)
    def test_duplicate_work_ids(self):self.assertEqual(audit(self.src)['duplicate_provider_ids'],['https://openalex.org/W1'])
    def test_unsupported_reported(self):self.assertEqual(next(i for i in audit(self.src)['files'] if i['path']=='unknown.bin')['warning'],'UNSUPPORTED_ARTIFACT')
    def test_cursor_historical(self):self.assertEqual(next(i for i in audit(self.src)['files'] if 'checkpoint' in i['path'])['warning'],'CURSOR_PRESERVED_NEVER_RESUMED')
    def test_pdf_unbound(self):self.assertEqual(next(i for i in audit(self.src)['files'] if i['path'].endswith('.pdf'))['warning'],'LEGACY_FULLTEXT_UNBOUND')
    def test_secret_reported_no_value(self):
        text=json.dumps(audit(self.src));self.assertIn('REDACTED_SECRET_PRESENT',text);self.assertNotIn('SYNTHETIC_LEGACY_SECRET_NOT_REAL',text)
    def test_fingerprint_deterministic(self):self.assertEqual(audit(self.src),audit(self.src))
    def test_source_location_independent(self):
        other=self.root/'nguồn khác';shutil.copytree(self.src,other);self.assertEqual(audit(self.src),audit(other))
    def test_bytes_change_fingerprint(self):
        before=audit(self.src)['source_fingerprint'];(self.src/'unknown.bin').write_bytes(b'changed');self.assertNotEqual(before,audit(self.src)['source_fingerprint'])
    def test_unknown_version_not_guessed(self):(self.src/'VERSION').unlink();(self.src/'pilot_run_metadata.json').unlink();self.assertIsNone(audit(self.src)['detected_version'])
    def test_version_from_actual_run_metadata(self):(self.src/'VERSION').unlink();self.assertEqual(audit(self.src)['detected_version'],'4.1.0')
    def test_dry_run_no_target(self):
        before=file_hashes(self.src);plan(self.src);self.assertFalse(self.target.exists());self.assertEqual(before,file_hashes(self.src))
    def test_plan_hash_canonical(self):p=plan(self.src);self.assertEqual(p['plan_sha256'],digest(p['plan']))
    def test_target_policy_change_plan(self):self.assertNotEqual(plan(self.src)['plan_sha256'],plan(self.src,'another-key')['plan_sha256'])
    def test_excluded_directory_explicit(self):(self.src/'.git').mkdir();(self.src/'.git/ignored').write_bytes(b'not in scope');self.assertNotIn('.git/ignored',[x['path'] for x in audit(self.src)['files']])
    def test_file_count_bound(self):
        with patch.dict(LIMITS,max_files=1),self.assertRaises(ValidationError):audit(self.src)
    def test_file_byte_bound(self):
        with patch.dict(LIMITS,max_file_bytes=1),self.assertRaises(ValidationError):audit(self.src)
    def test_total_byte_bound(self):
        with patch.dict(LIMITS,max_total_bytes=1),self.assertRaises(ValidationError):audit(self.src)
    def test_private_query_detected(self):self.assertTrue(sensitive(b'https://example.org/a?token=synthetic'))
    def test_public_url_not_drive_path(self):self.assertFalse(sensitive(b'https://example.org/a'))
    def test_exact_runtime_secret(self):
        with patch.dict(os.environ,OPENALEX_API_KEY='SYNTHETIC_RUNTIME_ONLY'):
            (self.src/'pilot_summary.json').write_text('{"opaque":"SYNTHETIC_RUNTIME_ONLY"}');self.assertNotIn('SYNTHETIC_RUNTIME_ONLY',json.dumps(audit(self.src)))
    def test_no_archive_extraction(self):
        (self.src/'bundle.zip').write_bytes(b'not an archive');entry=next(i for i in audit(self.src)['files'] if i['path']=='bundle.zip');self.assertFalse(entry['copy_evidence'])


class TestMigration(TempCase):
    def test_source_unchanged(self):before=file_hashes(self.src);self.migrate();self.assertEqual(before,file_hashes(self.src))
    def test_three_occurrences_two_identities(self):
        r=self.migrate()
        with Database(self.target) as db:
            s=LegacyMigrationStore(db);run=s.get(r['migration_id']);self.assertEqual(len(s.rows('legacy_migration_record_map',r['migration_id'])),3);self.assertEqual(len(s.resolver.list_publication_entities(run['resolution_run_id'])),2)
    def test_raw_bytes_sha_preserved(self):
        r=self.migrate()
        with Database(self.target) as db:
            s=LegacyMigrationStore(db);row=next(i for i in s.rows('legacy_migration_artifact',r['migration_id']) if i['relative_path']=='pilot_raw.jsonl');self.assertEqual(row['artifact_sha256'],sha((self.src/'pilot_raw.jsonl').read_bytes()))
    def test_no_fake_request(self):
        r=self.migrate()
        with Database(self.target) as db:
            s=LegacyMigrationStore(db);snap=s.foundation.get_provider_snapshot(s.get(r['migration_id'])['snapshot_id']);self.assertIsNone(snap['request_spec_sha256']);self.assertIn('LEGACY_INCOMPLETE_REQUEST_PROVENANCE',snap['metadata_json']);self.assertEqual(db.execute('SELECT count(*) FROM openalex_acquisition').fetchone()[0],0)
    def test_native_id_exact(self):
        self.migrate()
        with Database(self.target) as db:self.assertEqual({r[0] for r in db.execute('SELECT provider_native_id FROM provider_record')},{'https://openalex.org/W1','https://openalex.org/W2'})
    def test_no_screening(self):
        self.migrate()
        with Database(self.target) as db:
            for table in ('screening_decision','screening_outcome','corpus_release','corpus_release_item','fulltext_artifact','venue_membership_observation','venue_metric_observation'):self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
    def test_fulltext_not_bound(self):
        self.migrate()
        with Database(self.target) as db:self.assertEqual(db.execute('SELECT count(*) FROM fulltext_attempt').fetchone()[0],0)
    def test_secret_bytes_not_copied(self):
        self.migrate();secret=b'SYNTHETIC_LEGACY_SECRET_NOT_REAL';self.assertTrue(all(secret not in p.read_bytes() for p in (self.target/'raw').rglob('*') if p.is_file()))
    def test_replay_no_duplicates(self):
        r=self.migrate();before=file_hashes(self.target/'raw');again=execute(self.src,self.target,self.planned);self.assertTrue(again['reused']);self.assertEqual(r['manifest_sha256'],again['manifest_sha256']);self.assertEqual(before,file_hashes(self.target/'raw'))
    def test_changed_plan_needs_new_target(self):
        self.migrate()
        with self.assertRaises(ValidationError):execute(self.src,self.target,plan(self.src,'new-key'))
    def test_source_drift_blocks_before_target(self):
        p=plan(self.src);(self.src/'unknown.bin').write_bytes(b'drift')
        with self.assertRaises(ValidationError):execute(self.src,self.target,p)
        self.assertFalse(self.target.exists())
    def test_plan_tamper_blocks(self):
        p=plan(self.src);p['plan']['secret_policy']='COPY'
        with self.assertRaises(ValidationError):execute(self.src,self.target,p)
    def test_overlapping_source_target_refused(self):
        for target in (self.src,self.src/'child',self.root):
            with self.subTest(target=target.name),self.assertRaises(ValidationError):execute(self.src,target,plan(self.src))
    def test_nonowned_target_refused(self):
        self.target.mkdir();(self.target/'important.txt').write_text('keep')
        with self.assertRaises(ValidationError):self.migrate()
        self.assertEqual((self.target/'important.txt').read_text(),'keep')
    def test_fault_rolls_back_leaves_open(self):
        p=plan(self.src)
        def fail():raise RuntimeError('synthetic fault')
        with self.assertRaises(RuntimeError):execute(self.src,self.target,p,fault=fail)
        with Database(self.target) as db:
            s=LegacyMigrationStore(db);self.assertEqual(db.execute('SELECT state FROM legacy_migration_run').fetchone()[0],'OPEN');self.assertEqual(db.execute('SELECT count(*) FROM provider_record').fetchone()[0],0)
            with self.assertRaises(ValidationError):s.verify('lm_'+p['plan_sha256'][:40])
        self.assertTrue(execute(self.src,self.target,p)['verified'])
    def test_verifier_source_drift(self):
        r=self.migrate();(self.src/'unknown.bin').write_bytes(b'drift')
        with Database(self.target) as db,self.assertRaises(ArtifactIntegrityError):LegacyMigrationStore(db).verify(r['migration_id'],self.src)
    def test_verifier_without_source(self):
        r=self.migrate()
        with Database(self.target) as db:self.assertFalse(LegacyMigrationStore(db).verify(r['migration_id'])['source_checked'])
    def test_manifest_tamper(self):
        r=self.migrate()
        with Database(self.target) as db:
            s=LegacyMigrationStore(db);db.execute('DROP TRIGGER legacy_run_update');db.execute("UPDATE legacy_migration_run SET manifest_sha256=?",('0'*64,))
            with self.assertRaises(ArtifactIntegrityError):s.verify(r['migration_id'])
    def test_cas_tamper(self):
        r=self.migrate()
        with Database(self.target) as db:
            s=LegacyMigrationStore(db);key=next(a['artifact_sha256'] for a in s.rows('legacy_migration_artifact',r['migration_id']) if a['artifact_sha256']);s.foundation.artifacts.path_for_hash(key).write_bytes(b'changed')
            with self.assertRaises(ArtifactIntegrityError):s.verify(r['migration_id'])
    def test_complete_immutable(self):
        self.migrate()
        with Database(self.target) as db:
            LegacyMigrationStore(db)
            with self.assertRaises(FoundationError):db.execute("UPDATE legacy_migration_run SET state='OPEN'")
    def test_warning_immutable(self):
        self.migrate()
        with Database(self.target) as db:
            LegacyMigrationStore(db)
            with self.assertRaises(FoundationError):db.execute('DELETE FROM legacy_migration_warning')
    def test_unicode_no_mojibake(self):
        self.migrate()
        with Database(self.target) as db:self.assertTrue(any('Nghiên cứu' in (r[0] or '') for r in db.execute('SELECT title FROM publication_metadata_observation')))
    def test_no_absolute_semantic_path(self):
        r=self.migrate()
        with Database(self.target) as db:
            text=LegacyMigrationStore(db).get(r['migration_id'])['manifest_json'];self.assertNotIn(str(self.root),text);self.assertNotIn('/home/',text)
    def test_schema13_idempotent(self):
        self.migrate()
        with Database(self.target) as db:LegacyMigrationStore(db);LegacyMigrationStore(db);self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],13)


class TestOperations(TempCase):
    def test_doctor_no_workspace_no_writes(self):before=file_hashes(self.root);r=doctor();self.assertTrue(r['offline']);self.assertEqual(before,file_hashes(self.root))
    def test_doctor_missing_workspace_no_create(self):self.assertEqual(doctor(self.target)['database'],'ABSENT');self.assertFalse(self.target.exists())
    def test_init_all_migrations(self):self.assertEqual(initialize(self.target)['schema_version'],13)
    def test_doctor_source_bytes_unchanged(self):
        initialize(self.target);before=file_hashes(self.target);r=doctor(self.target,full=True);self.assertEqual(r['schema_version'],13);self.assertEqual(before,file_hashes(self.target))
    def test_doctor_sealed_migration(self):self.migrate();self.assertEqual(doctor(self.target,full=True)['sealed_integrity']['legacy_migration_run']['verified_count'],1)
    def test_doctor_never_resolves_credentials(self):
        with patch('reprovane.fulltext_acquisition.transport.runtime_credentials',side_effect=AssertionError('no read')):self.assertTrue(doctor()['offline'])
    def test_os_lock_refuses_second_writer(self):
        with workspace_lock(self.target):
            with self.assertRaises(ValidationError):
                with workspace_lock(self.target):pass
    def test_lock_crash_release_child(self):
        self.target.mkdir();code="from reprovane.operations.lock import workspace_lock; import os; c=workspace_lock("+repr(str(self.target))+"); c.__enter__(); os._exit(0)"
        p=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True);self.assertEqual(p.returncode,0)
        with workspace_lock(self.target):pass
    def test_inactive_lock_diagnosed(self):
        with workspace_lock(self.target):pass
        self.assertEqual(lock_diagnosis(self.target),'INACTIVE_METADATA_OS_LOCK_RELEASED')
    def test_active_writer_refuses_inspection(self):
        initialize(self.target)
        with workspace_lock(self.target),self.assertRaises(ValidationError):doctor(self.target)
    def test_backup_upgrade_schema12(self):
        from reprovane.fulltext_acquisition import FullTextStore
        with Database(self.target) as db:FullTextStore(db)
        before=file_hashes(self.target);result=upgrade(self.target,self.root/'upgraded',self.root/'backup');self.assertEqual(result['from_version'],12);self.assertEqual(result['to_version'],13);self.assertEqual(before,file_hashes(self.target));self.assertEqual(sha((self.root/'backup/database.sqlite').read_bytes()),result['backup_database_sha256'])
    def test_upgrade_idempotent_verified_replay(self):
        initialize(self.target);upgrade(self.target,self.root/'upgraded',self.root/'backup');self.assertTrue(upgrade(self.target,self.root/'upgraded',self.root/'backup')['reused'])
    def test_upgrade_drift_ledger(self):
        initialize(self.target)
        with contextlib.closing(sqlite3.connect(self.target/'db/litintel.sqlite')) as c:
            c.execute("UPDATE schema_migration SET sha256='bad' WHERE version=13");c.commit()
        with self.assertRaises(MigrationDriftError):upgrade(self.target,self.root/'upgraded',self.root/'backup')
    def test_upgrade_preserves_cas_and_manifests(self):self.migrate();upgrade(self.target,self.root/'upgraded',self.root/'backup');self.assertEqual(file_hashes(self.target/'raw'),file_hashes(self.root/'upgraded/raw'))
    def test_read_database_pending_wal(self):
        with Database(self.target) as db:
            Foundation(db).create_project('synthetic','Synthetic');before=file_hashes(self.target)
            with read_database(self.target) as rd:self.assertEqual(rd.execute('SELECT count(*) FROM review_project').fetchone()[0],1)
            self.assertEqual(before,file_hashes(self.target))
    def test_read_no_create_missing(self):
        with self.assertRaises(ValidationError):
            with read_database(self.target):pass
        self.assertFalse(self.target.exists())
    def test_unicode_nested_path(self):p=self.root/'Không gian nghiên cứu'/('long component '*2).strip()/'workspace';self.assertEqual(initialize(p)['schema_version'],13);self.assertTrue(doctor(p)['offline'])


class TestCLI(TempCase):
    def invoke(self,args):
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):code=main(args)
        return code,out.getvalue(),err.getvalue()
    def test_version_only(self):
        with self.assertRaises(SystemExit) as ctx,contextlib.redirect_stdout(io.StringIO()) as out:main(['--version'])
        self.assertEqual(ctx.exception.code,0);self.assertEqual(out.getvalue().strip(),__version__)
    def test_root_help(self):
        with self.assertRaises(SystemExit),contextlib.redirect_stdout(io.StringIO()) as out:main(['--help'])
        self.assertIn('legacy',out.getvalue());self.assertIn('fulltext',out.getvalue())
    def test_offline_doctor(self):
        with patch('socket.socket.connect',side_effect=AssertionError('no network')):code,out,err=self.invoke(['doctor','--offline']);self.assertEqual(code,0);self.assertTrue(json.loads(out)['offline'])
    def test_dry_run_cli_no_target(self):code,out,err=self.invoke(['legacy','migrate','--dry-run','--source',str(self.src)]);self.assertEqual(code,0);self.assertIn('plan_sha256',json.loads(out));self.assertFalse(self.target.exists())
    def test_missing_plan_exit(self):self.assertEqual(self.invoke(['legacy','migrate','--source',str(self.src),'--target',str(self.target)])[0],5)
    def test_project_create_revision(self):
        code,out,_=self.invoke(['project','create','--workspace',str(self.target),'--key','synthetic','--display-name','Synthetic']);self.assertEqual(code,0);pid=json.loads(out)['project_id'];m=self.root/'protocol.json';m.write_text('{}');self.assertEqual(self.invoke(['project','revision','--workspace',str(self.target),'--project-id',pid,'--number','1','--manifest',str(m)])[0],0)
    def test_root_verify_is_read_only(self):initialize(self.target);before=file_hashes(self.target);self.assertEqual(self.invoke(['verify','--offline','--workspace',str(self.target)])[0],0);self.assertEqual(before,file_hashes(self.target))
    def test_delegated_inspect_missing_no_mutation(self):
        from reprovane.corpus_release.__main__ import main as corpus_cli
        with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(corpus_cli(['inspect','--workspace',str(self.target),'--release-id','missing']),2)
        self.assertFalse(self.target.exists())
    def test_legacy_help_exact(self):
        from test_regressions import load_cli
        literature_pipeline=load_cli(ROOT/"tests/legacy_reference")
        with patch('socket.socket.connect',side_effect=AssertionError('offline')),self.assertRaises(SystemExit),contextlib.redirect_stdout(io.StringIO()) as out:literature_pipeline.main(['--help'])
        self.assertIn('v4.1.0',out.getvalue())


def routed_help(family):
    def test(self):
        with patch('socket.socket.connect',side_effect=AssertionError('offline')),self.assertRaises(SystemExit) as ctx,contextlib.redirect_stdout(io.StringIO()):main([family,'--help'])
        self.assertEqual(ctx.exception.code,0)
    return test
for family in [*ROUTES,'workspace','legacy','project','resolution','venue-resolution','scimago']:setattr(TestCLI,'test_help_'+family.replace('-','_'),routed_help(family))


class TestReleasePackage(unittest.TestCase):
    def test_single_version_metadata(self):
        p=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'));self.assertEqual(p['tool']['setuptools']['dynamic']['version']['attr'],'reprovane._version.__version__');self.assertEqual(__version__,'2.0.0')
    def test_runtime_dependencies_explicit(self):self.assertEqual(tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['dependencies'],['requests>=2.31,<3','pypdf>=6,<7'])
    def test_installed_entrypoints_declared(self):p=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'));self.assertEqual(p['project']['scripts']['reprovane'],'reprovane.__main__:main')
    def test_migration_package_data(self):self.assertIn('migrations/*.sql',tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['tool']['setuptools']['package-data']['reprovane.core'])
    def test_no_unbounded_build_major(self):self.assertTrue(all('<' in v for v in tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['build-system']['requires']))
    def test_distribution_excludes_local_data(self):s=(ROOT/'MANIFEST.in').read_text();self.assertIn('prune data',s);self.assertIn('.env',s)
    def test_required_guides(self):
        for name in ['README.md','MIGRATION_TO_SCHOLARFLOW.md','OPERATIONS.md','SECURITY.md','ARCHITECTURE.md','CHANGELOG.md']:self.assertTrue((ROOT/name).is_file())


class TestE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.first=e2e();cls.second=e2e()
    def test_final_e2e(self):self.assertEqual((self.first['corpus_items'],self.first['auto_acquired'],self.first['manual_queue']),(3,2,1))
    def test_repro_exact_outputs(self):self.assertEqual(self.first,self.second)
    def test_all_sealed_layers(self):self.assertTrue(all(v['verified_count']==v['sealed_count'] and v['verified_count'] for v in self.first['all_sealed_layers'].values()))
    def test_legacy_not_modern_decisions(self):self.assertFalse(any(self.first['zero_modern_authority_after_legacy'].values()))
    def test_excluded_not_corpus(self):self.assertEqual(self.first['screening_candidates']-self.first['screen_exclude'],self.first['corpus_items'])
    def test_failure_keeps_members(self):self.assertTrue(self.first['fulltext_does_not_change_membership']);self.assertEqual(self.first['corpus_items'],self.first['auto_acquired']+self.first['manual_queue'])
    def test_semantic_paths_portable(self):self.assertNotIn('C:\\',json.dumps(self.first));self.assertNotIn('/home/',json.dumps(self.first))
    def test_hashes_exact_repeated(self):self.assertEqual(self.first['corpus_item_hashes'],self.second['corpus_item_hashes']);self.assertEqual(self.first['export_file_hashes'],self.second['export_file_hashes'])


class TestStaticSecurity(unittest.TestCase):
    def test_migration_has_no_authority_writes(self):
        tree=ast.parse((ROOT/'reprovane/legacy_migration/store.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant) and isinstance(node.value,str):self.assertNotIn('INSERT INTO screening_',node.value);self.assertNotIn('INSERT INTO corpus_release',node.value)
    def test_no_unsafe_execution_or_network_in_migration(self):
        for p in (ROOT/'reprovane/legacy_migration').glob('*.py'):
            t=ast.parse(p.read_text(encoding='utf-8'))
            for n in ast.walk(t):
                if isinstance(n,ast.Call):self.assertNotIn(ast.unparse(n.func),['eval','exec','pickle.loads','subprocess.run','requests.get'])
    def test_no_ai_ocr_imports(self):
        for folder in ('screening','corpus_release','fulltext_acquisition','legacy_migration','operations'):
            for p in (ROOT/'reprovane'/folder).glob('*.py'):
                t=ast.parse(p.read_text(encoding='utf-8'))
                for n in ast.walk(t):
                    if isinstance(n,(ast.Import,ast.ImportFrom)):self.assertFalse(any(x in ast.unparse(n).lower() for x in ('import openai','import anthropic','import pytesseract','sentence_transformers')))
    def test_bounded_large_flat_inventory(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);row=dict(openalex_id='W1',title='Việt Nam '+'x'*10000,authors='Nguyễn '+'y'*10000,doi='10.1234/synthetic')
            (root/'load_raw.jsonl').write_text(''.join(json.dumps(dict(row,openalex_id='W'+str(i+1)),ensure_ascii=False)+'\n' for i in range(1000)),encoding='utf-8')
            result=audit(root);self.assertEqual(result['counts']['migration_safe_records'],1000)


if __name__=='__main__':unittest.main()
