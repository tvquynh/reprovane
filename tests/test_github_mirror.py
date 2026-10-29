import hashlib,json,tempfile,unittest,ast
from pathlib import Path
from unittest.mock import patch
import requests
from mirror_fixtures import Session,Response,REPO,A,B,raw,api
from scholarflow.mirror import (GitHubClient,MirrorCache,MirrorSyncError,GitHubFetchError,GitHubResolutionError,MirrorIntegrityError,MirrorCacheError)
from scholarflow.mirror.models import repository,ref,safe_path,commit


class TestGitHubTransport(unittest.TestCase):
    def setUp(self):
        self.session=Session(); self.sleeps=[]; self.client=GitHubClient(session=self.session,sleeper=self.sleeps.append)
    def tearDown(self): self.client.close()
    def test_repository_short(self): self.assertEqual(repository('tvquynh/wosaide-journal-lists'),REPO)
    def test_repository_url(self): self.assertEqual(repository('https://github.com/TVQUYNH/wosaide-journal-lists/'),REPO)
    def test_repository_rejects_unsafe(self):
        for value in ['',None,'https://evil.test/a/b','http://github.com/a/b','https://github.com/a/b?token=x','a/..','a/b/c','a/CON','a/b.git/..']:
            with self.subTest(value=value),self.assertRaises(MirrorSyncError): repository(value)
    def test_ref_rejects_before_network(self):
        for value in ['',None,'../x','/control','https://evil.test','a//b','a\\b','a..b','a.lock','a?token=secret','a\n']:
            with self.subTest(value=value),self.assertRaises(MirrorSyncError): self.client.resolve_ref(REPO,value)
        self.assertFalse(self.session.calls)
    def test_safe_ref_path_encoded(self):
        self.session.routes[api('heads%2Fcontrol')]=Response(json.dumps({'sha':A}).encode())
        self.client.resolve_ref(REPO,'heads/control'); self.assertIn('heads%2Fcontrol',self.session.calls[0]['url'])
    def test_branch_resolves_full_sha(self):
        r=self.client.resolve_ref(REPO,'control'); self.assertEqual((r.repository,r.requested_ref,r.resolved_commit_sha),(REPO,'control',A)); self.assertTrue(r.resolved_at_utc.endswith('Z'))
    def test_uppercase_sha_normalized(self):
        self.session.routes[api()]=Response(json.dumps({'sha':'AB'*20}).encode()); self.assertEqual(self.client.resolve_ref(REPO,'control').resolved_commit_sha,'ab'*20)
    def test_invalid_sha_response(self):
        for payload in [b'{}',b'[]',b'bad',json.dumps({'sha':'a'*39}).encode(),json.dumps({'sha':None}).encode()]:
            self.session.routes[api()]=Response(payload)
            with self.subTest(payload=payload),self.assertRaises(GitHubResolutionError): self.client.resolve_ref(REPO,'control')
    def test_ref_404(self):
        self.session.routes[api()]=Response(b'secret response',404)
        with self.assertRaises(GitHubResolutionError) as cm: self.client.resolve_ref(REPO,'control')
        self.assertNotIn('secret',str(cm.exception)); self.assertEqual(len(self.session.calls),1)
    def test_full_sha_verified_once(self):
        self.assertEqual(self.client.resolve_ref(REPO,A).resolved_commit_sha,A); self.assertEqual(self.session.calls[0]['url'],api(A))
    def test_full_sha_cannot_resolve_different_commit(self):
        self.session.routes[api(A)]=Response(json.dumps({'sha':B}).encode())
        with self.assertRaises(GitHubResolutionError): self.client.resolve_ref(REPO,A)
    def test_short_sha_not_immutable_for_fetch(self):
        with self.assertRaises(MirrorSyncError): self.client.fetch_bytes_at_commit(REPO,'aaaaaaa','SCIMAGO/x.csv')
        self.assertFalse(self.session.calls)
    def test_commit_raw_url(self):
        self.session.files[raw(A,'SCIMAGO/manifest.json')]=b'{}'; self.assertEqual(self.client.fetch_bytes_at_commit(REPO,A,'SCIMAGO/manifest.json'),b'{}'); self.assertIn('/'+A+'/',self.session.calls[0]['url']); self.assertNotIn('/control/',self.session.calls[0]['url'])
    def test_mutable_raw_ref_rejected(self):
        with self.assertRaises(MirrorSyncError): self.client.fetch_bytes_at_commit(REPO,'control','x.csv')
        self.assertFalse(self.session.calls)
    def test_unsafe_paths_before_network(self):
        for value in ['../a','/a','a/../b','a\\b','https://evil.test/a','a?x=1','a//b','CON','a:ads','a\x00','a/.','a/']:
            with self.subTest(value=value),self.assertRaises(MirrorSyncError): self.client.fetch_bytes_at_commit(REPO,A,value)
        self.assertFalse(self.session.calls)
    def test_raw_404_and_403_permanent(self):
        for code in [400,401,403,404,422,301,302,307,308]:
            self.session.calls=[]; self.session.routes[raw(A,'x')]=Response(b'credential response',code)
            with self.assertRaises(GitHubFetchError) as cm: self.client.fetch_bytes_at_commit(REPO,A,'x')
            self.assertEqual(len(self.session.calls),1); self.assertNotIn('credential',str(cm.exception))
    def test_retry_429_capped_after(self):
        self.session.routes[raw(A,'x')]=[Response(status=429,headers={'Retry-After':'9999'}),Response(b'ok')]
        self.assertEqual(self.client.fetch_bytes_at_commit(REPO,A,'x'),b'ok'); self.assertEqual(self.sleeps,[10])
    def test_retry_503_bounded(self):
        self.session.routes[raw(A,'x')]=[Response(status=503) for _ in range(3)]
        with self.assertRaises(GitHubFetchError): self.client.fetch_bytes_at_commit(REPO,A,'x')
        self.assertEqual(len(self.session.calls),3); self.assertEqual(self.sleeps,[1,2])
    def test_transient_http_codes(self):
        for code in [500,502,504]:
            self.session.routes[raw(A,'x')]=[Response(status=code),Response(b'ok')]; self.assertEqual(self.client.fetch_bytes_at_commit(REPO,A,'x'),b'ok')
    def test_connect_read_retries_bounded(self):
        for exc in [requests.ConnectTimeout,requests.ReadTimeout,requests.ConnectionError]:
            self.session.calls=[]; self.session.routes[raw(A,'x')]=[exc('sensitive details') for _ in range(3)]
            with self.assertRaises(GitHubFetchError) as cm: self.client.fetch_bytes_at_commit(REPO,A,'x')
            self.assertEqual(len(self.session.calls),3); self.assertNotIn('sensitive',str(cm.exception))
    def test_tls_failure_permanent(self):
        self.session.routes[raw(A,'x')]=requests.exceptions.SSLError('secret')
        with self.assertRaises(GitHubFetchError): self.client.fetch_bytes_at_commit(REPO,A,'x')
        self.assertEqual(len(self.session.calls),1)
    def test_retry_stream_resets_destination(self):
        self.session.routes[raw(A,'x')]=[Response(b'bad',error=requests.ReadTimeout()),Response(b'good')]
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'x'; actual,count=self.client.fetch_to_file(REPO,A,'x',target); self.assertEqual(target.read_bytes(),b'good'); self.assertEqual(count,4); self.assertEqual(actual,hashlib.sha256(b'good').hexdigest())
    def test_declared_size_cap(self):
        self.session.routes[raw(A,'x')]=Response(b'x',headers={'Content-Length':str(self.client.max_file_bytes+1)})
        with self.assertRaises(GitHubFetchError): self.client.fetch_bytes_at_commit(REPO,A,'x')
    def test_stream_size_cap(self):
        client=GitHubClient(session=self.session,max_file_bytes=3); self.session.routes[raw(A,'x')]=Response(b'abcd')
        with self.assertRaises(GitHubFetchError): client.fetch_bytes_at_commit(REPO,A,'x')
    def test_control_size_cap(self):
        client=GitHubClient(session=self.session,max_control_bytes=2); self.session.routes[raw(A,'manifest.json')]=Response(b'123')
        with self.assertRaises(GitHubFetchError): client.fetch_bytes_at_commit(REPO,A,'manifest.json',control=True)
    def test_useragent_https_timeouts_redirect_boundary(self):
        self.client.resolve_ref(REPO,'control'); call=self.session.calls[0]; self.assertEqual(call['timeout'],(10,60)); self.assertTrue(call['stream']); self.assertFalse(call['allow_redirects']); self.assertIn('User-Agent',call['headers']); self.assertNotIn('Authorization',call['headers']); self.assertFalse(self.session.trust_env)
    def test_no_credential_configuration(self):
        with GitHubClient() as client: self.assertIsNone(client.session.auth); self.assertFalse(client.session.trust_env)
    def test_response_closed_success_failure(self):
        self.session.routes[raw(A,'x')]=Response(b'body',404)
        with self.assertRaises(GitHubFetchError): self.client.fetch_bytes_at_commit(REPO,A,'x')
        self.assertTrue(self.session.responses[-1].closed)
    def test_bad_limits_rejected(self):
        for kwargs in [dict(max_attempts=0),dict(max_attempts=11),dict(max_file_bytes=0),dict(connect_timeout=float('nan')),dict(read_timeout=-1)]:
            with self.subTest(kwargs=kwargs),self.assertRaises(MirrorSyncError): GitHubClient(**kwargs)
    def test_retry_after_invalid_finite(self):
        for value in ['nan','inf','bad','-9']:
            self.sleeps=[]; self.client.sleeper=self.sleeps.append; self.session.routes[raw(A,'x')]=[Response(status=429,headers={'Retry-After':value}),Response(b'ok')]; self.client.fetch_bytes_at_commit(REPO,A,'x'); self.assertEqual(self.sleeps,[1])
    def test_generic_wos_and_scopus_paths(self):
        for path in ['WOS/SCIE.csv','SCOPUS/ext_list_Jul_2026.csv']:
            self.session.files[raw(A,path)]=b'opaque'; self.assertEqual(self.client.fetch_bytes_at_commit(REPO,A,path),b'opaque'); self.assertIn('/'+A+'/',self.session.calls[-1]['url'])


class TestMirrorCache(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.cache=MirrorCache(Path(self.temp.name)); self.data=b'first object'; self.digest=hashlib.sha256(self.data).hexdigest()
    def tearDown(self): self.temp.cleanup()
    def test_content_addressed_bytes(self):
        self.assertEqual(self.cache.put_bytes(self.data),self.digest); self.assertEqual(self.cache.object_path(self.digest).read_bytes(),self.data)
    def test_verified_existing_reuse(self):
        self.cache.put_bytes(self.data)
        with patch('scholarflow.mirror.cache.os.replace',side_effect=AssertionError('no replacement')): self.assertEqual(self.cache.put_bytes(self.data),self.digest)
    def test_corrupt_object_detected(self):
        self.cache.put_bytes(self.data); self.cache.object_path(self.digest).write_bytes(b'bad'); self.assertFalse(self.cache.verify_object(self.digest))
    def test_corrupt_object_recovered_verified(self):
        self.cache.put_bytes(self.data); self.cache.object_path(self.digest).write_bytes(b'bad'); self.cache.put_bytes(self.data); self.assertTrue(self.cache.verify_object(self.digest))
    def test_object_key_deterministic(self):
        self.assertEqual(self.cache.object_path(self.digest),self.cache.root/'objects'/'sha256'/self.digest[:2]/self.digest)
    def test_objects_differ_without_filename_identity(self): self.assertNotEqual(self.cache.put_bytes(b'a'),self.cache.put_bytes(b'b'))
    def test_hash_mismatch_no_object_promotion(self):
        with self.cache.temporary_object() as p:
            p.write_bytes(b'bad')
            with self.assertRaises(MirrorIntegrityError): self.cache.promote_object(p,self.digest)
        self.assertFalse(self.cache.object_path(self.digest).exists())
    def test_failed_atomic_object_leaves_no_success(self):
        with patch('scholarflow.mirror.cache.os.replace',side_effect=OSError()):
            with self.assertRaises(OSError): self.cache.put_bytes(self.data)
        self.assertFalse(self.cache.object_path(self.digest).exists()); self.assertFalse(list((self.cache.root/'objects'/'incoming').iterdir()))
    def test_namespace_repository_commit_and_subpath(self):
        values=[self.cache.snapshot_path(REPO,A,'SCIMAGO'),self.cache.snapshot_path('other/repo',A,'SCIMAGO'),self.cache.snapshot_path(REPO,B,'SCIMAGO'),self.cache.snapshot_path(REPO,A,'WOS')]; self.assertEqual(len(set(values)),4)
    def test_copy_isolated_from_cas_mutation(self):
        self.cache.put_bytes(self.data); target=self.cache.root/'test'/'copy'; self.cache.materialize_file(self.digest,target); self.cache.object_path(self.digest).write_bytes(b'bad'); self.assertEqual(target.read_bytes(),self.data)
    def test_materialization_requires_verified_object(self):
        self.cache.put_bytes(self.data); self.cache.object_path(self.digest).write_bytes(b'bad')
        with self.assertRaises(MirrorIntegrityError): self.cache.materialize_file(self.digest,self.cache.root/'bad')
    def test_materialization_never_overwrites(self):
        self.cache.put_bytes(self.data); target=self.cache.root/'copy'; self.cache.materialize_file(self.digest,target)
        with self.assertRaises(MirrorCacheError): self.cache.materialize_file(self.digest,target)
    def test_symlink_cache_rejected(self):
        target=self.cache.root/'objects'; target.mkdir(); link=target/'sha256'
        try: link.symlink_to(self.cache.root/'outside',target_is_directory=True)
        except OSError:
            with patch.object(Path,'is_symlink',return_value=True):
                with self.assertRaises(MirrorCacheError): self.cache.object_path(self.digest)
            return
        with self.assertRaises(MirrorCacheError): self.cache.object_path(self.digest)
    def test_symlink_api_rejects_mocked_link(self):
        with patch.object(Path,'is_symlink',return_value=True):
            with self.assertRaises(MirrorCacheError): self.cache.object_path(self.digest)
    def test_atomic_json_failure_retains_previous(self):
        target=self.cache.root/'state.json'; self.cache.atomic_json(target,{'old':True})
        with patch('scholarflow.mirror.cache.os.replace',side_effect=OSError()):
            with self.assertRaises(MirrorSyncError): self.cache.atomic_json(target,{'old':False})
        self.assertEqual(self.cache.read_json(target),{'old':True})
    def test_completed_snapshot_promotion_rejected(self):
        final=self.cache.snapshot_path(REPO,A,'SCIMAGO'); final.mkdir(parents=True)
        with self.cache.staging_snapshot(final) as staging:
            with self.assertRaises(MirrorCacheError): self.cache.promote_snapshot(staging,final)
    def test_sync_lock(self):
        with self.cache.sync_lock(REPO,'SCIMAGO'):
            with self.assertRaises(MirrorSyncError):
                with self.cache.sync_lock(REPO,'SCIMAGO'): pass
        self.assertFalse(self.cache.state_path(REPO,'SCIMAGO').with_suffix('.lock').exists())
