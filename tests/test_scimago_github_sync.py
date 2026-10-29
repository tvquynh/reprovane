import ast,hashlib,json
from pathlib import Path
from unittest.mock import patch
from mirror_fixtures import SyncCase,REPO,A,B,C,raw,Response
from venue_fixtures import checksums
from scholarflow.mirror import MirrorSyncError,MirrorIntegrityError,MirrorOfflineError,MirrorStateError,import_synced_scimago
from scholarflow.providers.scimago import validate_local_package,parse_scimago_manifest_bytes,parse_scimago_checksums_bytes,declared_scimago_files
from scholarflow.core.db import Database


class TestScimagoGitHubSync(SyncCase):
    def test_first_sync_downloads_declared(self):
        r=self.sync(); self.assertEqual(r.status,'UPDATED'); self.assertEqual(r.downloaded_files,('scimagojr-2022.csv','scimagojr-2023.csv')); self.assertEqual(r.reused_files,()); self.assertEqual(len(self.data_calls()),2); self.assertTrue(r.verified)
    def test_all_requests_same_resolved_sha(self):
        r=self.sync(); urls=[c['url'] for c in self.session.calls]; self.assertEqual(len([u for u in urls if 'api.github' in u]),1)
        for url in urls[1:]: self.assertIn('/'+r.resolved_commit_sha+'/',url); self.assertNotIn('/control/',url)
        self.assertIn('/manifest.json',urls[1]); self.assertIn('/SHA256SUMS.txt',urls[2]); self.assertTrue(all('.csv' in u for u in urls[3:]))
    def test_branch_move_during_fetch_still_pinned(self):
        original=self.client.fetch_bytes_at_commit
        def moved(repo,sha,path,**kw):
            result=original(repo,sha,path,**kw); self.session.current=B; return result
        with patch.object(self.client,'fetch_bytes_at_commit',side_effect=moved): result=self.sync()
        self.assertEqual(result.resolved_commit_sha,A); self.assertTrue(all('/'+A+'/' in c['url'] for c in self.session.calls[1:]))
    def test_same_commit_no_data_or_controls_download(self):
        first=self.sync(); before=self.snapshot_bytes(first); n=len(self.session.calls); r=self.sync(); self.assertEqual(r.status,'UP_TO_DATE'); self.assertEqual(len(self.session.calls)-n,1); self.assertEqual(r.downloaded_files,()); self.assertEqual(r.downloaded_bytes,0); self.assertEqual(self.snapshot_bytes(first),before)
    def test_future_year_downloads_only_new(self):
        a=self.sync(); old=self.snapshot_bytes(a); self.seed(B,years=(2022,2023,2031)); n=len(self.session.calls); b=self.sync()
        self.assertEqual(self.data_calls(n),[raw(B,'SCIMAGO/scimagojr-2031.csv')]); self.assertEqual(b.downloaded_files,('scimagojr-2031.csv',)); self.assertEqual(b.reused_files,('scimagojr-2022.csv','scimagojr-2023.csv')); self.assertEqual(b.latest_snapshot_year,2031); self.assertEqual(validate_local_package(Path(b.snapshot_directory)/'SCIMAGO').years,(2022,2023,2031)); self.assertEqual(self.snapshot_bytes(a),old); self.assertEqual(self.state()['last_successful_commit_sha'],B)
    def test_changed_old_hash_only_that_object_downloads(self):
        self.sync()
        def edit(root):
            p=root/'scimagojr-2022.csv'; p.write_bytes(p.read_bytes().replace(b'Alpha Journal',b'Changed Journal')); checksums(root)
        self.seed(B,edit=edit); n=len(self.session.calls); r=self.sync(); self.assertEqual(self.data_calls(n),[raw(B,'SCIMAGO/scimagojr-2022.csv')]); self.assertEqual(r.reused_files,('scimagojr-2023.csv',))
    def test_downloaded_bytes_exact_data_only(self):
        r=self.sync(); self.assertEqual(r.downloaded_bytes,sum(len(self.session.files[raw(A,'SCIMAGO/'+n)]) for n in r.downloaded_files))
    def test_lists_sorted(self):
        self.seed(A,apc=True); r=self.sync(); self.assertEqual(r.downloaded_files,tuple(sorted(r.downloaded_files))); self.assertEqual(r.journal_value_years,(2022,))
    def test_v1_manifest_plan(self):
        self.seed(A,version=1); r=self.sync(); self.assertEqual(r.available_sjr_years,(2022,2023)); self.assertEqual(r.manifest_schema_version,1)
    def test_v2_manifest_plan(self): self.assertEqual(self.sync().manifest_schema_version,2)
    def test_apc_optional_absent(self): self.assertEqual(self.sync().journal_value_years,())
    def test_apc_independent_old_year(self):
        self.seed(A,years=(2022,2023,2031),apc=True); r=self.sync(); self.assertEqual(r.journal_value_years,(2022,)); self.assertIn('journal-value-2022.csv',r.downloaded_files)
    def test_apc_declared_checksum_required(self):
        def edit(root):
            p=root/'SHA256SUMS.txt'; p.write_text('\n'.join(l for l in p.read_text().splitlines() if 'journal-value' not in l))
        self.seed(A,apc=True,edit=edit)
        with self.assertRaises(MirrorIntegrityError): self.sync()
        self.assertFalse(self.data_calls())
    def test_missing_checksum_no_data_fetch(self):
        self.session.files[raw(A,'SCIMAGO/SHA256SUMS.txt')]=b''
        with self.assertRaises(MirrorIntegrityError): self.sync()
        self.assertFalse(self.data_calls()); self.assertFalse(self.cache.state_path(REPO,'SCIMAGO').exists())
    def test_duplicate_checksum_rejected(self):
        key=raw(A,'SCIMAGO/SHA256SUMS.txt'); self.session.files[key]*=2
        with self.assertRaises(MirrorIntegrityError): self.sync()
        self.assertFalse(self.data_calls())
    def test_unsafe_checksum_rejected(self):
        key=raw(A,'SCIMAGO/SHA256SUMS.txt'); self.session.files[key]+=('a'*64+'  ../escape.csv\n').encode()
        with self.assertRaises(MirrorIntegrityError): self.sync()
    def test_extra_checksum_informational_not_downloaded(self):
        key=raw(A,'SCIMAGO/SHA256SUMS.txt'); self.session.files[key]+=('a'*64+'  extra.csv\n').encode(); r=self.sync(); self.assertNotIn('extra.csv',r.downloaded_files); self.assertEqual(len(self.data_calls()),2)
    def test_unsupported_manifest_schema(self):
        self.edit_manifest(lambda m:m.update(schema_version=3))
        with self.assertRaises(MirrorIntegrityError): self.sync()
        self.assertEqual(len(self.session.calls),2)
    def edit_manifest(self,change,sha=A):
        key=raw(sha,'SCIMAGO/manifest.json'); m=json.loads(self.session.files[key]); change(m); self.session.files[key]=json.dumps(m).encode()
    def test_invalid_latest(self):
        self.edit_manifest(lambda m:m['sjr'].update(latest_snapshot_year=2031))
        with self.assertRaises(MirrorIntegrityError): self.sync()
        self.assertFalse(self.data_calls())
    def test_unsorted_duplicate_v2_years(self):
        for years in [[2023,2022],[2022,2022]]:
            self.edit_manifest(lambda m:m['sjr'].update(available_years=years))
            with self.assertRaises(MirrorIntegrityError): self.sync()
    def test_historical_baseurl_never_called(self):
        self.edit_manifest(lambda m:m.update(base_url='https://scopus.cuatui.cloud/old/')); self.sync(); self.assertTrue(all(c['url'].startswith(('https://api.github.com/','https://raw.githubusercontent.com/')) for c in self.session.calls)); self.assertFalse(any('cuatui' in c['url'] for c in self.session.calls))
    def test_no_readme_repository_state_dependency(self):
        self.sync(); self.assertFalse(any('README' in c['url'] or 'repository_state' in c['url'] for c in self.session.calls))
    def test_control_own_hashes_exact_preserved(self):
        r=self.sync(); p=Path(r.snapshot_directory)/'SCIMAGO'; self.assertEqual(r.manifest_sha256,hashlib.sha256(self.session.files[raw(A,'SCIMAGO/manifest.json')]).hexdigest()); self.assertEqual(r.checksums_sha256,hashlib.sha256((p/'SHA256SUMS.txt').read_bytes()).hexdigest()); self.assertNotIn('manifest.json',(p/'SHA256SUMS.txt').read_text())
    def test_snapshot_full_sha_namespace(self):
        r=self.sync(); self.assertIn(A,Path(r.snapshot_directory).parts); self.assertIn('tvquynh',Path(r.snapshot_directory).parts); self.assertEqual(r.snapshot_directory,str(self.cache.snapshot_path(REPO,A,'SCIMAGO')))
    def test_complete_stage04_package(self): self.assertTrue(validate_local_package(Path(self.sync().snapshot_directory)/'SCIMAGO'))
    def test_sidecar_outside_package(self):
        r=self.sync(); root=Path(r.snapshot_directory); self.assertTrue((root/'sync_provenance.json').is_file()); self.assertFalse((root/'SCIMAGO'/'sync_provenance.json').exists()); side=self.cache.read_json(root/'sync_provenance.json'); self.assertEqual(side['declared_data_files'],validate_local_package(root/'SCIMAGO').checksums)
    def test_materialized_equals_cas(self):
        r=self.sync(); pkg=validate_local_package(Path(r.snapshot_directory)/'SCIMAGO')
        for name,digest in pkg.checksums.items(): self.assertEqual((pkg.root/name).read_bytes(),self.cache.object_path(digest).read_bytes()); self.assertFalse((pkg.root/name).is_symlink())
    def test_old_snapshot_immutable(self):
        a=self.sync(); old=self.snapshot_bytes(a); self.seed(B,years=(2022,2023,2031)); b=self.sync(); self.assertNotEqual(a.snapshot_directory,b.snapshot_directory); self.assertEqual(self.snapshot_bytes(a),old)
    def test_state_after_success_exact(self):
        r=self.sync(); state=self.state(); self.assertEqual(state['last_successful_commit_sha'],A); self.assertEqual(state['snapshot_path'],r.snapshot_directory); self.assertTrue(state['convenience_metadata_only']); self.assertEqual(state['manifest_sha256'],r.manifest_sha256)
    def failure_at(self,path,response=None,wrong=False):
        a=self.sync(); state=self.cache.state_path(REPO,'SCIMAGO').read_bytes(); old=self.snapshot_bytes(a); self.seed(B,years=(2022,2023,2031))
        if wrong: self.session.files[raw(B,path)]=b'incorrect bytes'
        else: self.session.routes[raw(B,path)]=response or Response(status=404)
        with self.assertRaises(MirrorSyncError): self.sync()
        self.assertEqual(self.cache.state_path(REPO,'SCIMAGO').read_bytes(),state); self.assertEqual(self.snapshot_bytes(a),old); self.assertFalse(self.cache.snapshot_path(REPO,B,'SCIMAGO').exists())
    def test_manifest_failure_retains_state(self): self.failure_at('SCIMAGO/manifest.json')
    def test_checksum_failure_retains_state(self): self.failure_at('SCIMAGO/SHA256SUMS.txt')
    def test_data_404_retains_state(self): self.failure_at('SCIMAGO/scimagojr-2031.csv')
    def test_hash_mismatch_retains_state(self): self.failure_at('SCIMAGO/scimagojr-2031.csv',wrong=True)
    def test_failed_stage04_validation_retains_state(self):
        a=self.sync(); old=self.state(); self.seed(B,years=(2022,2023,2031))
        with patch('scholarflow.mirror.scimago_sync.validate_local_package',side_effect=MirrorIntegrityError('injected validation failure')):
            with self.assertRaises(MirrorIntegrityError): self.sync()
        self.assertEqual(self.state(),old); self.assertFalse(self.cache.snapshot_path(REPO,B,'SCIMAGO').exists())
    def test_object_orphans_reusable_after_failure(self):
        self.seed(A,years=(2022,2023,2031)); self.session.routes[raw(A,'SCIMAGO/scimagojr-2031.csv')]=Response(status=404)
        with self.assertRaises(MirrorSyncError): self.sync()
        del self.session.routes[raw(A,'SCIMAGO/scimagojr-2031.csv')]; n=len(self.session.calls); r=self.sync(); self.assertEqual(self.data_calls(n),[raw(A,'SCIMAGO/scimagojr-2031.csv')]); self.assertEqual(len(r.reused_files),2)
    def test_state_write_failure_rolls_back_new_promotion(self):
        a=self.sync(); state=self.state(); self.seed(B,years=(2022,2023,2031))
        with patch('scholarflow.mirror.scimago_sync._write_state',side_effect=MirrorStateError('injected state failure')):
            with self.assertRaises(MirrorStateError): self.sync()
        self.assertEqual(self.state(),state); self.assertFalse(self.cache.snapshot_path(REPO,B,'SCIMAGO').exists()); self.assertTrue(Path(a.snapshot_directory).is_dir())
    def test_no_partial_staging_after_failure(self):
        final=self.cache.snapshot_path(REPO,A,'SCIMAGO')
        with patch('scholarflow.mirror.cache.MirrorCache.materialize_file',side_effect=MirrorIntegrityError('injected copy failure')):
            with self.assertRaises(MirrorIntegrityError): self.sync()
        self.assertFalse(final.exists()); self.assertEqual(list(final.parent.glob('.snapshot-*')),[]); self.assertFalse(self.cache.state_path(REPO,'SCIMAGO').exists())
    def test_online_corrupt_object_redownload(self):
        a=self.sync(); pkg=validate_local_package(Path(a.snapshot_directory)/'SCIMAGO'); name='scimagojr-2022.csv'; self.cache.object_path(pkg.checksums[name]).write_bytes(b'corrupt'); self.seed(B); n=len(self.session.calls); r=self.sync(); self.assertEqual(self.data_calls(n),[raw(B,'SCIMAGO/'+name)]); self.assertTrue(self.cache.verify_object(pkg.checksums[name]))
    def test_same_commit_corrupt_cas_recovered_snapshot_unchanged(self):
        a=self.sync(); original=self.snapshot_bytes(a); pkg=validate_local_package(Path(a.snapshot_directory)/'SCIMAGO'); name='scimagojr-2022.csv'; self.cache.object_path(pkg.checksums[name]).write_bytes(b'bad'); n=len(self.session.calls); r=self.sync(); self.assertEqual(r.status,'UPDATED'); self.assertEqual(self.data_calls(n),[raw(A,'SCIMAGO/'+name)]); self.assertEqual(self.snapshot_bytes(a),original)
    def test_same_commit_control_cas_recovered_verified_copy(self):
        a=self.sync(); original=self.snapshot_bytes(a); self.cache.object_path(a.manifest_sha256).write_bytes(b'bad'); n=len(self.session.calls); r=self.sync(); self.assertEqual(r.status,'UP_TO_DATE'); self.assertTrue(self.cache.verify_object(a.manifest_sha256)); self.assertEqual(len(self.session.calls)-n,1); self.assertEqual(self.snapshot_bytes(a),original)
    def test_corrupt_completed_snapshot_fails_online_without_mutation(self):
        a=self.sync(); p=Path(a.snapshot_directory)/'SCIMAGO'/'scimagojr-2022.csv'; p.write_bytes(b'corrupt'); old=self.state()
        with self.assertRaises(MirrorIntegrityError): self.sync()
        self.assertEqual(p.read_bytes(),b'corrupt'); self.assertEqual(self.state(),old)
    def test_offline_exact_zero_network(self):
        a=self.sync(); self.session.calls=[]
        r=self.sync(requested_ref=A,offline=True); self.assertEqual(r.status,'OFFLINE_REUSED'); self.assertEqual(r.resolved_commit_sha,A); self.assertFalse(self.session.calls)
    def test_offline_does_not_create_network_client(self):
        from scholarflow.mirror import sync_scimago_from_github
        self.sync()
        with patch('scholarflow.mirror.scimago_sync.GitHubClient',side_effect=AssertionError('network client forbidden')):
            self.assertEqual(sync_scimago_from_github(self.workspace,offline=True).status,'OFFLINE_REUSED')
    def test_offline_missing_exact_fails(self):
        with self.assertRaises(MirrorOfflineError): self.sync(requested_ref=B,offline=True)
        self.assertFalse(self.session.calls)
    def test_offline_control_actual_stored_commit(self):
        self.sync(); self.session.current=B; self.session.calls=[]; r=self.sync(offline=True); self.assertEqual(r.resolved_commit_sha,A); self.assertEqual(r.requested_ref,'control'); self.assertEqual(r.status,'OFFLINE_REUSED'); self.assertFalse(self.session.calls)
    def test_offline_no_state_branch_fails(self):
        with self.assertRaises(MirrorOfflineError): self.sync(offline=True)
        self.assertFalse(self.session.calls)
    def test_offline_other_branch_not_assumed(self):
        self.sync(); self.session.calls=[]
        with self.assertRaises(MirrorOfflineError): self.sync(requested_ref='different',offline=True)
        self.assertFalse(self.session.calls)
    def test_offline_corrupt_object_fails(self):
        r=self.sync(); pkg=validate_local_package(Path(r.snapshot_directory)/'SCIMAGO'); self.cache.object_path(next(iter(pkg.checksums.values()))).write_bytes(b'bad'); self.session.calls=[]
        with self.assertRaises(MirrorOfflineError): self.sync(offline=True)
        self.assertFalse(self.session.calls)
    def test_offline_corrupt_package_fails(self):
        r=self.sync(); (Path(r.snapshot_directory)/'SCIMAGO'/'scimagojr-2022.csv').write_bytes(b'bad'); self.session.calls=[]
        with self.assertRaises(MirrorOfflineError): self.sync(offline=True)
        self.assertFalse(self.session.calls)
    def test_offline_corrupt_sidecar_fails(self):
        r=self.sync(); p=Path(r.snapshot_directory)/'sync_provenance.json'; m=json.loads(p.read_text()); m['manifest_sha256']='0'*64; p.write_text(json.dumps(m)); self.session.calls=[]
        with self.assertRaises(MirrorOfflineError): self.sync(offline=True)
        self.assertFalse(self.session.calls)
    def test_missing_state_exact_snapshot_still_usable(self):
        r=self.sync(); self.cache.state_path(REPO,'SCIMAGO').unlink(); self.assertEqual(self.sync(requested_ref=A,offline=True).resolved_commit_sha,A)
    def test_corrupt_state_exact_snapshot_still_usable(self):
        self.sync(); self.cache.state_path(REPO,'SCIMAGO').write_bytes(b'invalid'); self.assertEqual(self.sync(requested_ref=A,offline=True).status,'OFFLINE_REUSED')
    def test_stale_state_path_not_authority(self):
        self.sync(); state=self.state(); state['snapshot_path']='C:/untrusted'; self.cache.atomic_json(self.cache.state_path(REPO,'SCIMAGO'),state)
        with self.assertRaises(MirrorOfflineError): self.sync(offline=True)
        self.assertEqual(self.sync(requested_ref=A,offline=True).status,'OFFLINE_REUSED')
    def test_stale_missing_state_online_reuses_snapshot(self):
        r=self.sync(); self.cache.state_path(REPO,'SCIMAGO').unlink(); n=len(self.session.calls); self.assertEqual(self.sync().status,'UP_TO_DATE'); self.assertEqual(len(self.session.calls)-n,1)
    def test_stage04_handoff_exact_commit_provenance(self):
        result=self.sync()
        with Database(self.root/'research') as db:
            imported=list(import_synced_scimago(db,result)); self.assertEqual(len(imported),2)
            for item in imported:
                s=item['snapshot']; m=json.loads(s['metadata_json']); self.assertEqual(s['mirror_commit_sha'],A); self.assertTrue(m['immutable_distribution_reference']); self.assertEqual(m['distribution_branch_requested'],'control'); self.assertIn('/'+A+'/',s['source_locator']); self.assertNotIn('/control/',s['source_locator']); self.assertEqual(len(m['manifest_sha256']),64); self.assertEqual(len(s['mirror_commit_sha']),40)
    def test_stage04_handoff_optional_apc(self):
        self.seed(A,apc=True); result=self.sync()
        with Database(self.root/'research') as db:
            imported=list(import_synced_scimago(db,result)); self.assertEqual([x['snapshot']['dataset'] for x in imported],['source_list','source_list','journal_value']); self.assertTrue(all(x['snapshot']['mirror_commit_sha']==A for x in imported))
    def test_stage04_handoff_rejects_changed_result_bytes(self):
        result=self.sync(); (Path(result.snapshot_directory)/'SCIMAGO'/'manifest.json').write_text('{}')
        with Database(self.root/'research') as db:
            with self.assertRaises(Exception): list(import_synced_scimago(db,result))
            self.assertEqual(db.execute('SELECT count(*) FROM provider_snapshot').fetchone()[0],0)
    def test_no_automatic_ingestion(self):
        self.sync(); self.assertFalse((self.workspace/'literature.sqlite3').exists())
    def test_unsafe_repository_path_pre_network(self):
        for value in ['../SCIMAGO','/SCIMAGO','WOS\\x','https://host/']:
            with self.assertRaises(MirrorSyncError): self.sync(repository_path=value)
        self.assertFalse(self.session.calls)
    def test_no_git_or_semantic_scope_creep(self):
        root=Path(__file__).parents[1]/'scholarflow'/'mirror'
        for p in root.glob('*.py'):
            tree=ast.parse(p.read_text())
            for n in ast.walk(tree):
                if isinstance(n,ast.Import): self.assertFalse({a.name.split('.')[0] for a in n.names}&{'subprocess','git','openai'})
                if isinstance(n,ast.Constant) and type(n.value) is int: self.assertNotIn(n.value,[2010,2025,16,17])
    def test_shared_parser_same_plan(self):
        result=self.sync(); root=Path(result.snapshot_directory)/'SCIMAGO'; local=validate_local_package(root); plan=parse_scimago_manifest_bytes((root/'manifest.json').read_bytes()); declared=declared_scimago_files(plan,parse_scimago_checksums_bytes((root/plan['checksums_filename']).read_bytes())); self.assertEqual(declared,local.checksums); self.assertEqual(plan['years'],local.years)
    def test_manifest_defined_checksum_filename(self):
        key=raw(A,'SCIMAGO/SHA256SUMS.txt'); self.session.files[raw(A,'SCIMAGO/sha-data.txt')]=self.session.files.pop(key); self.edit_manifest(lambda m:m['checksums'].update(file='sha-data.txt')); result=self.sync(); self.assertTrue((Path(result.snapshot_directory)/'SCIMAGO'/'sha-data.txt').is_file())
