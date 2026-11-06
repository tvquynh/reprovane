import copy,hashlib,io,json,shutil,socket,unittest
from pathlib import Path
from unittest.mock import patch
from fulltext_acquisition_fixtures import FullCase,policy,location,PDF,PDF2,Response,Opener,public_dns,FIX
from reprovane.fulltext_acquisition import FullTextPolicy,FullTextStore,verify_export
from reprovane.fulltext_acquisition.candidates import location_routes,make_candidates
from reprovane.fulltext_acquisition.safety import normalize_url,sanitize_url,public_address,validate_pdf
from reprovane.fulltext_acquisition.transport import SafeTransport,FetchError
from reprovane.fulltext_acquisition.__main__ import main
from reprovane.core import FoundationError,ValidationError,ArtifactIntegrityError,MigrationDriftError
from reprovane.core.db import Database
from reprovane.core.migrations import MIGRATION_DIR,apply_migrations
from reprovane.corpus_release.model import digest
from pypdf import PdfWriter


class TestPolicy(unittest.TestCase):
    def test_canonical(self):
        p=policy(); q=dict(reversed(list(p.items()))); q['allowed_versions'].reverse()
        self.assertEqual(FullTextPolicy.from_value(p),FullTextPolicy.from_value(q))
    def test_changed_versions(self): self.assertNotEqual(FullTextPolicy.from_value(policy()).sha256,FullTextPolicy.from_value(policy(allowed_versions=['publishedVersion'])).sha256)
    def test_changed_limit(self): self.assertNotEqual(FullTextPolicy.from_value(policy()).sha256,FullTextPolicy.from_value(policy(max_pdf_bytes=2000)).sha256)
    def test_changed_rank(self): self.assertNotEqual(FullTextPolicy.from_value(policy()).sha256,FullTextPolicy.from_value(policy(version_rank=['acceptedVersion','publishedVersion','submittedVersion','UNKNOWN'])).sha256)
    def test_required_explicit(self):
        p=policy(); del p['retry_backoff']
        with self.assertRaises(ValidationError): FullTextPolicy.from_value(p)
    def test_no_manual_ingest(self):
        with self.assertRaises(ValidationError): FullTextPolicy.from_value(policy(manual_ingest_enabled=True))
    def test_inverted_limits(self):
        with self.assertRaises(ValidationError): FullTextPolicy.from_value(policy(min_pdf_bytes=300,max_pdf_bytes=200))
    def test_unknown_extra_field(self):
        with self.assertRaises(ValidationError): FullTextPolicy.from_value(policy(scrape=True))


def bad_policy_test(field,value):
    def test(self):
        with self.assertRaises(ValidationError): FullTextPolicy.from_value(policy(**{field:value}))
    return test
for field,vals in {'retry_count':[-1,6,float('inf')],'max_redirects':[-1,11],
    'request_timeout':[0,float('nan'),float('inf')],'max_pdf_bytes':[0,-1,None],
    'allowed_versions':[['guess']],'allowed_schemes':[['file']],
    'global_download_budget':[{'items':1},dict(items=1,http_requests=1,bytes=1,artifacts=1,provider_lookups=float('inf'))]}.items():
    for n,v in enumerate(vals): setattr(TestPolicy,f'test_invalid_{field}_{n}',bad_policy_test(field,v))


class TestCandidates(unittest.TestCase):
    def cs(self,locs,provider='OPENALEX',p=None): return make_candidates('cri_synthetic',location_routes(dict(locations=locs),provider,'https://openalex.org/W1','fte_test'),p or policy(provider_sources_enabled=['OPENALEX']))
    def test_published(self): self.assertTrue(self.cs([location()])[0]['eligible'])
    def test_accepted(self): self.assertTrue(self.cs([location('acceptedVersion')])[0]['eligible'])
    def test_submitted(self): self.assertFalse(self.cs([location('submittedVersion')])[0]['eligible'])
    def test_unknown(self): self.assertFalse(self.cs([location(None)])[0]['eligible'])
    def test_unknown_explicit_policy(self): self.assertTrue(self.cs([location(None)],p=policy(provider_sources_enabled=['OPENALEX'],allow_unknown_version=True))[0]['eligible'])
    def test_closed(self): self.assertFalse(self.cs([location(is_oa=False)])[0]['eligible'])
    def test_landing_only(self): self.assertEqual(self.cs([location(pdf_url=None,landing_page_url='https://papers.example.org/a')])[0]['eligibility_reason'],'NO_DIRECT_PDF')
    def test_published_over_accepted(self): self.assertEqual(self.cs([location('acceptedVersion',url='https://papers.example.org/b'),location()])[0]['version'],'publishedVersion')
    def test_publisher_over_repository(self): self.assertEqual(self.cs([location(host='repository',url='https://papers.example.org/b'),location()])[0]['host_type'],'publisher')
    def test_stable_tie(self):
        a=location(url='https://papers.example.org/a');b=location(url='https://papers.example.org/b')
        self.assertEqual([c['url'] for c in self.cs([a,b])],[c['url'] for c in self.cs([b,a])])
    def test_same_url_multiple_routes(self):
        rs=location_routes(dict(locations=[location()]),'OPENALEX','W1','e1')+location_routes(dict(oa_locations=[dict(url_for_pdf='https://papers.example.org/a.pdf',version='publishedVersion',host_type='publisher')]),'UNPAYWALL','10.1234/a','e2')
        cs=make_candidates('i',rs,policy(provider_sources_enabled=['OPENALEX','UNPAYWALL']));self.assertEqual(len(cs),1);self.assertEqual(len(cs[0]['routes']),2)
    def test_conflicting_version_fails_closed(self): self.assertEqual(self.cs([location(),location('submittedVersion')])[0]['eligibility_reason'],'VERSION_CONFLICT')
    def test_no_claim_combination(self):
        cs=self.cs([location(is_oa=False),location(None)])
        self.assertFalse(cs[0]['eligible'])
    def test_best_never_overrides_version(self):
        rs=location_routes(dict(best_oa_location=location('submittedVersion')),'OPENALEX','W1','e');self.assertFalse(make_candidates('i',rs,policy(provider_sources_enabled=['OPENALEX']))[0]['eligible'])
    def test_cached_content_does_not_prove_version(self):
        rs=location_routes(dict(has_content={'pdf':True},content_urls={'pdf':'https://content.openalex.org/works/W1.pdf'},open_access={'is_oa':True}),'OPENALEX','W1','e')
        cs=make_candidates('i',rs,policy(provider_sources_enabled=['OPENALEX_CONTENT'])); self.assertEqual(cs[0]['version'],'UNKNOWN');self.assertFalse(cs[0]['eligible'])
    def test_cached_fallback_url(self): self.assertEqual(location_routes(dict(has_content={'pdf':True}),'OPENALEX','W1','e')[0]['url'],'https://content.openalex.org/works/W1.pdf')
    def test_unpaywall_best_and_all(self):
        loc=dict(host_type='repository',version='acceptedVersion',url_for_pdf='https://papers.example.org/a',url_for_landing_page='https://papers.example.org/page',license='cc-by',is_best=True)
        rs=location_routes(dict(best_oa_location=loc,oa_locations=[loc]),'UNPAYWALL','10.1234/a','e');self.assertEqual(len(rs),4);self.assertEqual(rs[0]['license'],'cc-by')
    def test_unpaywall_null_best(self): self.assertEqual(location_routes(dict(best_oa_location=None,oa_locations=[]),'UNPAYWALL','10.1234/a','e'),[])
    def test_url_signed_provenance_redacted(self):
        c=self.cs([location(url='https://papers.example.org/a?token=secretvalue&x=1')])[0];self.assertNotIn('secretvalue',json.dumps(c));self.assertTrue(c['runtime_url_required'])


class TestSafety(unittest.TestCase):
    def test_conservative_url(self): self.assertEqual(normalize_url('HTTPS://EXAMPLE.ORG:443/A?b=2&a=1#section'),'https://example.org/A?b=2&a=1')
    def test_query_semantics(self): self.assertNotEqual(normalize_url('https://example.org/a?x=1&y=2'),normalize_url('https://example.org/a?y=2&x=1'))
    def test_public_dns(self): self.assertEqual(public_address('https://example.org/a',['https'],public_dns)[1],'93.184.216.34')
    def test_dns_mixed_private(self):
        with self.assertRaises(ValidationError): public_address('https://example.org',['https'],lambda *a,**k:public_dns()+[(2,1,6,'',('10.0.0.1',443))])
    def test_pdf_valid(self): self.assertEqual(validate_pdf(PDF,policy())['pages'],1)
    def test_pdf_hash(self): self.assertEqual(validate_pdf(PDF,policy())['artifact_sha256'],hashlib.sha256(PDF).hexdigest())
    def test_pdf_zero_pages(self):
        b=io.BytesIO();PdfWriter().write(b)
        with self.assertRaises(ValidationError): validate_pdf(b.getvalue(),policy())
    def test_pdf_truncated(self):
        with self.assertRaises(ValidationError): validate_pdf(PDF[:-30],policy())
    def test_pdf_html(self):
        with self.assertRaises(ValidationError): validate_pdf(b'<html>not pdf</html>'*100,policy())
    def test_pdf_fake_header(self):
        with self.assertRaises(ValidationError): validate_pdf(b'%PDF-1.7\n'+b'x'*500+b'%%EOF',policy())
    def test_pdf_min(self):
        with self.assertRaises(ValidationError): validate_pdf(PDF,policy(min_pdf_bytes=1000))
    def test_pdf_max(self):
        with self.assertRaises(ValidationError): validate_pdf(PDF,policy(max_pdf_bytes=200))


def private_test(address):
    def test(self):
        with self.assertRaises(ValidationError): public_address('https://example.org',['http','https'],lambda *a,**k:[(2,1,6,'',(address,443))])
    return test
for n,address in enumerate(['127.0.0.1','10.0.0.1','192.168.1.1','172.16.0.1','169.254.169.254','::1','fc00::1','fe80::1','0.0.0.0','224.0.0.1']): setattr(TestSafety,'test_private_ip_'+str(n),private_test(address))
def unsafe_url_test(value):
    def test(self):
        with self.assertRaises(ValidationError): normalize_url(value)
    return test
for n,value in enumerate(['file:///a.pdf','\\\\server\\file.pdf','ftp://example.org/a','https://u:p@example.org/a','https://example.org:8080/a','https://example.org/\nfoo']): setattr(TestSafety,'test_bad_url_'+str(n),unsafe_url_test(value))


class TestTransport(unittest.TestCase):
    def fetch(self,responses,p=None,url='https://papers.example.org/a',provider=None,credentials=None,resolver=public_dns):
        self.opener=Opener(responses);self.sleeps=[];self.events=[]
        t=SafeTransport(p or policy(),credentials=credentials,resolver=resolver,opener=self.opener,sleeper=self.sleeps.append)
        return t.fetch(url,purpose='PDF',max_bytes=(p or policy())['max_pdf_bytes'],reserve=lambda *a:('id',a[-1]),complete=lambda tid,r:self.events.append(r),provider=provider)
    def test_success(self): self.assertEqual(self.fetch([Response()])[0],PDF)
    def test_http_allowed(self): self.assertEqual(self.fetch([Response()],url='http://papers.example.org/a')[0],PDF)
    def test_429_retry(self): self.fetch([Response(status=429),Response()]);self.assertEqual(len(self.opener.calls),2)
    def test_500_retry(self): self.fetch([Response(status=500),Response()]);self.assertEqual(len(self.opener.calls),2)
    def test_timeout_retry(self): self.fetch([TimeoutError(),Response()]);self.assertEqual(len(self.opener.calls),2)
    def test_permanent_403(self):
        with self.assertRaises(FetchError) as ctx: self.fetch([Response(status=403)])
        self.assertEqual(ctx.exception.reason,'AUTH_REQUIRED');self.assertEqual(len(self.opener.calls),1)
    def test_login_html(self):
        with self.assertRaises(FetchError) as ctx:self.fetch([Response(b'<html>login</html>')])
        self.assertEqual(ctx.exception.reason,'AUTH_REQUIRED')
    def test_challenge_html(self):
        with self.assertRaises(FetchError) as ctx:self.fetch([Response(b'<html>Cloudflare captcha</html>')])
        self.assertEqual(ctx.exception.reason,'ANTI_BOT_BLOCKED')
    def test_bounded_retries(self):
        with self.assertRaises(FetchError):self.fetch([Response(status=500)]*3)
        self.assertEqual(len(self.opener.calls),3)
    def test_redirect(self): self.fetch([Response(status=302,headers={'Location':'/b'}),Response()]);self.assertEqual(self.opener.calls[-1]['url'],'https://papers.example.org/b')
    def test_private_redirect(self):
        def dns(host,*a,**kw):return public_dns() if host=='papers.example.org' else [(2,1,6,'',('10.0.0.1',443))]
        with self.assertRaises(FetchError):self.fetch([Response(status=302,headers={'Location':'https://private.example.org/a'})],resolver=dns)
        self.assertEqual(len(self.opener.calls),1)
    def test_redirect_limit(self):
        with self.assertRaises(FetchError): self.fetch([Response(status=302,headers={'Location':'/b'})]*5)
        self.assertEqual(len(self.opener.calls),5)
    def test_https_downgrade(self):
        with self.assertRaises(FetchError): self.fetch([Response(status=302,headers={'Location':'http://papers.example.org/b'})])
    def test_max_size(self):
        with self.assertRaises(FetchError): self.fetch([Response()],p=policy(max_pdf_bytes=200))
    def test_stream_oversize(self):
        with self.assertRaises(FetchError): self.fetch([Response(headers={'Content-Type':'application/pdf'})],p=policy(max_pdf_bytes=200))
    def test_content_type_not_authority(self): self.assertEqual(self.fetch([Response(headers={'Content-Type':'text/plain'})])[0],PDF)
    def test_header_auth_runtime_only(self):
        self.fetch([Response()],url='https://api.openalex.org/works/W1',credentials=('SYNTHETIC_KEY',''))
        self.assertEqual(self.opener.calls[0]['headers']['Authorization'],'Bearer SYNTHETIC_KEY');self.assertNotIn('SYNTHETIC_KEY',self.opener.calls[0]['url'])
    def test_no_auth_redirect_leak(self):
        self.fetch([Response(status=302,headers={'Location':'https://papers.example.org/a'}),Response()],url='https://content.openalex.org/works/W1.pdf',credentials=('SYNTHETIC_KEY',''))
        self.assertNotIn('Authorization',self.opener.calls[-1]['headers'])
    def test_email_runtime_only(self):
        self.fetch([Response()],url='https://api.unpaywall.org/v2/10.1234/a',provider='UNPAYWALL',credentials=('','synthetic@example.invalid'))
        self.assertIn('email=',self.opener.calls[0]['url']);self.assertNotIn('synthetic',json.dumps(self.events))
    def test_unpaywall_email_required(self):
        with self.assertRaises(FetchError):self.fetch([],url='https://api.unpaywall.org/v2/10.1234/a',provider='UNPAYWALL')


class TestRuns(FullCase):
    def test_three_members_two_auto_one_manual(self):
        m=self.completed();self.assertEqual(len(m['items']),3);self.assertEqual(m['artifact_count'],2);self.assertEqual(len(m['manual_queue']),1);self.assertIn('ONLY_SUBMITTED_VERSION',m['manual_queue'][0]['reasons']);self.assertTrue(self.full.verify(self.fid)['verified'])
    def test_corpus_unchanged(self):
        self.discovered();before=self.releases.handoff(self.crid);self.full.acquire(self.fid,self.transport([Response(),Response(PDF2)]));self.full.finalize(self.fid);self.assertEqual(before,self.releases.handoff(self.crid))
    def test_input_release_bound(self): self.setup_run();self.assertEqual(self.full.get(self.fid)['release_manifest_sha256'],self.releases.get(self.crid)['release_manifest_sha256'])
    def test_same_policy_reuses(self): self.setup_run();self.assertEqual(self.full.create(self.crid,policy())['run_id'],self.fid)
    def test_changed_policy_new_run(self): self.setup_run();self.assertNotEqual(self.full.create(self.crid,policy(max_pdf_bytes=1000))['run_id'],self.fid)
    def test_only_members_no_screen_exclude(self):
        self.release(include_count=2);self.full=FullTextStore(self.db);self.fid=self.full.create(self.crid,policy())['run_id'];self.assertEqual(self.full.inspect(self.fid)['corpus_item_count'],2)
    def test_open_release_rejected(self):
        self.release(finalize=False);self.full=FullTextStore(self.db)
        with self.assertRaises(ValidationError):self.full.create(self.crid,policy())
    def test_materialized_release_rejected(self):
        self.release(finalize=False);self.releases.materialize(self.crid);self.full=FullTextStore(self.db)
        with self.assertRaises(ValidationError):self.full.create(self.crid,policy())
    def test_candidate_plan_frozen(self):
        self.discovered();before=self.full.rows('fulltext_plan',self.fid);self.full.acquire(self.fid,self.transport([Response(),Response(PDF2)]));self.assertEqual(before,self.full.rows('fulltext_plan',self.fid))
    def test_acquire_requires_discovery(self):
        self.setup_run()
        with self.assertRaises(ValidationError):self.full.acquire(self.fid,self.transport())
    def test_changed_policy_resume_refused(self):
        self.discovered()
        with self.assertRaises(ValidationError):self.full.acquire(self.fid,self.transport(),expected_policy_sha256='0'*64)
    def test_success_not_redownloaded(self):
        self.discovered();self.full.acquire(self.fid,self.transport([Response(),Response(PDF2)]));self.full.acquire(self.fid,self.transport());self.assertEqual(self.opener.calls,[])
    def test_permanent_not_retried(self):
        self.discovered(versions=('publishedVersion',));self.full.acquire(self.fid,self.transport([Response(status=403)]));self.full.acquire(self.fid,self.transport());self.assertEqual(self.opener.calls,[])
    def test_retryable_resume(self):
        self.discovered(versions=('publishedVersion',));self.full.acquire(self.fid,self.transport([Response(status=500)]*3));self.full.acquire(self.fid,self.transport([Response()]));self.full.finalize(self.fid);self.assertEqual(self.full.inspect(self.fid)['artifact_count'],1)
    def test_interrupted_resume(self):
        self.discovered(versions=('publishedVersion',));c=self.full.rows('fulltext_candidate',self.fid)[0]
        with self.full.write(self.fid): self.full.insert('fulltext_attempt',dict(run_id=self.fid,candidate_id=c['candidate_id'],attempt_id='fta_interrupted',ordinal=0,started_at='synthetic'))
        self.full.acquire(self.fid,self.transport([Response()]));self.full.finalize(self.fid);self.assertEqual(self.full.inspect(self.fid)['artifact_count'],1)
    def test_unknown_only_manual(self):
        self.discovered(versions=(None,));self.full.acquire(self.fid,self.transport());self.full.finalize(self.fid);self.assertIn('ONLY_UNKNOWN_VERSION',json.loads(self.full.get(self.fid)['manifest_json'])['manual_queue'][0]['reasons'])
    def test_landing_not_fetched(self):
        self.discovered(versions=('publishedVersion',),locations=[[location(pdf_url=None,landing_page_url='https://papers.example.org/page')]]);self.full.acquire(self.fid,self.transport());self.full.finalize(self.fid);self.assertEqual(self.opener.calls,[]);self.assertIn('NO_DIRECT_PDF',json.loads(self.full.get(self.fid)['manifest_json'])['manual_queue'][0]['reasons'])
    def test_no_locations_manual(self):
        self.discovered(versions=('publishedVersion',),locations=[[]]);self.full.finalize(self.fid);self.assertIn('NO_OA_LOCATION',json.loads(self.full.get(self.fid)['manifest_json'])['manual_queue'][0]['reasons'])
    def test_same_bytes_dedup(self):
        self.discovered(versions=('publishedVersion','acceptedVersion'));self.full.acquire(self.fid,self.transport([Response(),Response()]));self.full.finalize(self.fid);self.assertEqual(self.full.inspect(self.fid)['artifact_count'],1)
    def test_different_bytes_distinct(self): self.assertEqual(self.completed()['artifact_count'],2)
    def test_multiple_candidates_primary_rank(self):
        self.discovered(versions=('publishedVersion',),locations=[[location('acceptedVersion',url='https://papers.example.org/b'),location()]]);self.full.acquire(self.fid,self.transport([Response(),Response(PDF2)]));self.full.finalize(self.fid);m=json.loads(self.full.get(self.fid)['manifest_json']);self.assertEqual(m['items'][0]['selected_artifact_sha256'],hashlib.sha256(PDF).hexdigest());self.assertEqual(m['artifact_count'],2)
    def test_budget_items(self):
        p=policy();p['global_download_budget']['items']=1;self.discovered(protocol=p);self.full.acquire(self.fid,self.transport([Response()]));self.full.finalize(self.fid);self.assertEqual(self.full.budget(self.fid)['items'],1)
    def test_budget_requests(self):
        p=policy();p['global_download_budget']['http_requests']=1;self.discovered(protocol=p);self.full.acquire(self.fid,self.transport([Response()]));self.assertEqual(len(self.opener.calls),1)
    def test_budget_bytes(self):
        p=policy();p['global_download_budget']['bytes']=1;self.discovered(protocol=p);self.full.acquire(self.fid,self.transport());self.assertEqual(self.opener.calls,[])
    def test_per_item_limit(self):
        self.discovered(versions=('publishedVersion',),locations=[[location(url=f'https://papers.example.org/{i}') for i in range(5)]],protocol=policy(per_item_candidate_limit=1));self.full.acquire(self.fid,self.transport([Response()]));self.assertEqual(len(self.opener.calls),1)
    def test_invalid_pdf_manual(self):
        self.discovered(versions=('publishedVersion',));self.full.acquire(self.fid,self.transport([Response(b'<html>fake</html>'*50)]));self.full.finalize(self.fid);self.assertIn('PDF_VALIDATION_FAILED',json.loads(self.full.get(self.fid)['manifest_json'])['manual_queue'][0]['reasons'])
    def test_seal_needs_all_plans(self):
        self.setup_run()
        with self.assertRaises(ValidationError):self.full.finalize(self.fid)
    def test_sealed_replay(self): self.completed();before=self.full.get(self.fid);self.full.finalize(self.fid);self.assertEqual(before,self.full.get(self.fid))
    def test_offline_verification(self):
        self.completed()
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),patch('reprovane.fulltext_acquisition.transport.runtime_credentials',side_effect=AssertionError('credentials forbidden')):self.assertTrue(self.full.verify(self.fid)['verified'])
    def test_schema12_idempotent(self): self.setup_run();FullTextStore(self.db);self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],12)
    def test_sql_status_immutable(self):
        self.completed()
        with self.assertRaises(FoundationError):self.db.execute("UPDATE fulltext_status SET status_json='{}' WHERE run_id=?",(self.fid,))
    def test_sql_sealed_insert_blocked(self):
        self.completed();row=self.full.rows('fulltext_plan',self.fid)[0]
        with self.full.write(self.fid),self.assertRaises(FoundationError):self.full.insert('fulltext_plan',row)
    def test_sql_cross_release(self):
        self.setup_run()
        with self.full.write(self.fid),self.assertRaises(FoundationError):self.full.insert('fulltext_input',dict(run_id=self.fid,corpus_item_id='cri_unknown',input_json='{}',input_sha256=digest({})))
    def test_artifact_tamper(self):
        m=self.completed();path=self.full.cas.path_for_hash(next(s['selected_artifact_sha256'] for s in m['items'] if s['selected_artifact_sha256']));path.write_bytes(b'broken')
        with self.assertRaises(ArtifactIntegrityError):self.full.verify(self.fid)
    def test_manifest_tamper_detected(self):
        self.completed();self.db.execute('DROP TRIGGER fulltext_run_update');self.db.execute("UPDATE fulltext_run SET manifest_sha256=? WHERE run_id=?",('0'*64,self.fid))
        with self.assertRaises(ArtifactIntegrityError):self.full.verify(self.fid)
    def test_plan_tamper_detected(self):
        self.discovered();self.db.execute('DROP TRIGGER fulltext_plan_update');self.db.execute("UPDATE fulltext_plan SET plan_json='{}' WHERE run_id=?",(self.fid,))
        with self.assertRaises(ArtifactIntegrityError):self.full.verify(self.fid)
    def test_export_portable(self): self.completed();result=self.full.export(self.fid,self.root/'export');self.assertTrue(result['verified']);self.assertEqual(result['item_count'],3)
    def test_portable_no_database(self):
        self.completed();self.full.export(self.fid,self.root/'export')
        with patch('reprovane.core.db.Database',side_effect=AssertionError('no DB')):self.assertTrue(verify_export(self.root/'export')['verified'])
    def test_portable_expected_hash(self):
        self.completed();self.full.export(self.fid,self.root/'export')
        with self.assertRaises(ArtifactIntegrityError):verify_export(self.root/'export',expected_manifest_sha256='0'*64)
    def test_portable_extra_refused(self):
        self.completed();self.full.export(self.fid,self.root/'export');(self.root/'export/extra').write_text('x')
        with self.assertRaises(ArtifactIntegrityError):verify_export(self.root/'export')
    def test_export_idempotent(self): self.completed();self.assertEqual(self.full.export(self.fid,self.root/'export'),self.full.export(self.fid,self.root/'export'))
    def test_export_differing_refused(self):
        self.completed();self.full.export(self.fid,self.root/'export');(self.root/'export/manual_queue.csv').write_bytes(b'x')
        with self.assertRaises(ValidationError):self.full.export(self.fid,self.root/'export')
    def test_convenience_reuse(self): self.completed();self.assertEqual(self.full.export_pdfs(self.fid,self.root/'pdfs'),self.full.export_pdfs(self.fid,self.root/'pdfs'))
    def test_convenience_different_refused(self):
        self.completed();self.full.export_pdfs(self.fid,self.root/'pdfs');next((self.root/'pdfs').glob('*.pdf')).write_bytes(b'x')
        with self.assertRaises(ValidationError):self.full.export_pdfs(self.fid,self.root/'pdfs')
    def test_no_fake_pdf_placeholder(self):self.completed();self.full.export_pdfs(self.fid,self.root/'pdfs');self.assertEqual(len(list((self.root/'pdfs').glob('*.pdf'))),2)
    def test_manual_queue_order(self):self.discovered(versions=('submittedVersion','submittedVersion','submittedVersion'));self.full.finalize(self.fid);q=json.loads(self.full.get(self.fid)['manifest_json'])['manual_queue'];self.assertEqual([r['corpus_item_id'] for r in q],sorted(r['corpus_item_id'] for r in q))
    def test_tampered_release_rejected(self):
        self.setup_run();self.db.execute('DROP TRIGGER corpus_release_item_update');self.db.execute("UPDATE corpus_release_item SET item_sha256=? WHERE release_id=?",('0'*64,self.crid))
        with self.assertRaises(ArtifactIntegrityError):self.full.verify(self.fid)
    def test_missing_artifact_detected(self):
        m=self.completed();selected=next(s['selected_artifact_sha256'] for s in m['items'] if s['selected_artifact_sha256']);self.full.cas.path_for_hash(selected).unlink()
        with self.assertRaises(ArtifactIntegrityError):self.full.verify(self.fid)
    def test_status_tamper_detected(self):
        self.completed();self.db.execute('DROP TRIGGER fulltext_status_update');self.db.execute("UPDATE fulltext_status SET status_json='{}' WHERE run_id=?",(self.fid,))
        with self.assertRaises(ArtifactIntegrityError):self.full.verify(self.fid)
    def test_attempt_tamper_detected(self):
        self.completed();self.db.execute('DROP TRIGGER fulltext_attempt_result_update');self.db.execute("UPDATE fulltext_attempt_result SET result_json='{}' WHERE run_id=?",(self.fid,))
        with self.assertRaises(ArtifactIntegrityError):self.full.verify(self.fid)
    def test_primary_evidence_in_manifest(self):
        m=self.completed();self.assertTrue(all(s['selected_evidence'] for s in m['items'] if s['status']=='ACQUIRED_AUTO'))
    def test_request_failure_http_provenance(self):
        self.discovered(versions=('publishedVersion',));self.full.acquire(self.fid,self.transport([Response(status=403)]));r=json.loads(self.full.rows('fulltext_attempt_result',self.fid)[0]['result_json']);self.assertEqual(r['http']['http_status'],403);self.assertTrue(r['request_ids'])
    def test_runtime_value_refused_before_request_record(self):
        self.setup_run();t=self.transport(credentials=('SYNTHETIC_PRIVATE_VALUE',''))
        with self.assertRaises(FetchError):self.full._fetch(self.fid,t,'https://papers.example.org/SYNTHETIC_PRIVATE_VALUE','PDF',1000,'synthetic')
        self.assertEqual(self.full.rows('fulltext_request',self.fid),[]);self.assertEqual(self.opener.calls,[])
    def test_export_modified_checksum(self):
        self.completed();self.full.export(self.fid,self.root/'export');(self.root/'export/SHA256SUMS.txt').write_bytes(b'broken')
        with self.assertRaises(ArtifactIntegrityError):verify_export(self.root/'export')
    def test_export_missing_file(self):
        self.completed();self.full.export(self.fid,self.root/'export');(self.root/'export/manual_queue.csv').unlink()
        with self.assertRaises(ArtifactIntegrityError):verify_export(self.root/'export')
    def test_partial_export_retry(self):
        self.completed();self.full.export(self.fid,self.root/'export');(self.root/'export/manual_queue.csv').unlink();self.assertTrue(self.full.export(self.fid,self.root/'export')['verified'])
    def test_no_doi_queue_reason(self):
        from openalex_acquisition_fixtures import work
        self.release([work(1,doi=None,locations=[])],include_count=1);self.full=FullTextStore(self.db);self.fid=self.full.create(self.crid,policy())['run_id'];self.full.discover(self.fid,self.transport());self.full.finalize(self.fid);self.assertIn('NO_DOI',json.loads(self.full.get(self.fid)['manifest_json'])['manual_queue'][0]['reasons'])
    def test_auth_required_queue(self):
        self.discovered(versions=('publishedVersion',));self.full.acquire(self.fid,self.transport([Response(status=403)]));self.full.finalize(self.fid);self.assertIn('AUTH_REQUIRED',json.loads(self.full.get(self.fid)['manifest_json'])['manual_queue'][0]['reasons'])
    def test_bot_block_queue(self):
        self.discovered(versions=('publishedVersion',));self.full.acquire(self.fid,self.transport([Response(b'Cloudflare captcha') ]));self.full.finalize(self.fid);self.assertIn('ANTI_BOT_BLOCKED',json.loads(self.full.get(self.fid)['manifest_json'])['manual_queue'][0]['reasons'])
    def test_repeat_materialization_identical(self):
        self.completed();a=self.full.get(self.fid)['manifest_sha256'];self.full.materialize(self.fid);self.assertEqual(a,self.full.get(self.fid)['manifest_sha256'])
    def test_cli_safe_failure(self):
        with patch('sys.stderr',new_callable=io.StringIO):self.assertEqual(main(['verify-export','--directory',str(self.root/'missing')]),2)


class TestProviders(FullCase):
    def test_unpaywall_discover_download_and_verify(self):
        self.setup_run(versions=('publishedVersion',),protocol=policy(provider_sources_enabled=['UNPAYWALL']))
        item=json.loads(self.full.get(self.fid)['handoff_json'])['items'][0]
        data=dict(doi=item['dois'][0],best_oa_location=dict(host_type='repository',version='acceptedVersion',url_for_pdf='https://papers.example.org/a'),oa_locations=[])
        self.full.discover(self.fid,self.transport([Response(json.dumps(data).encode())],credentials=('','synthetic@example.invalid')))
        self.full.acquire(self.fid,self.transport([Response()]));self.full.finalize(self.fid);self.assertTrue(self.full.verify(self.fid)['verified'])
    def test_lookup_outside_handoff_rejected(self):
        self.setup_run(protocol=policy(provider_sources_enabled=['UNPAYWALL']))
        with self.assertRaises(ValidationError):self.full._lookup(self.fid,'UNPAYWALL','10.1234/not-member',self.transport())
    def test_lookup_reused_after_discovery_interruption(self):
        self.setup_run(versions=('publishedVersion',),protocol=policy(provider_sources_enabled=['OPENALEX']))
        item=json.loads(self.full.get(self.fid)['handoff_json'])['items'][0];wid=item['openalex_work_ids'][0]
        self.full._lookup(self.fid,'OPENALEX',wid,self.transport([Response(json.dumps(dict(id=wid,locations=[location()])).encode())]))
        self.full.discover(self.fid,self.transport());self.assertEqual(self.opener.calls,[])
    def test_unpaywall_rate_retry_evidence(self):
        self.setup_run(versions=('publishedVersion',),protocol=policy(provider_sources_enabled=['UNPAYWALL']))
        item=json.loads(self.full.get(self.fid)['handoff_json'])['items'][0]
        body=json.dumps(dict(doi=item['dois'][0],oa_locations=[],best_oa_location=None)).encode()
        self.full.discover(self.fid,self.transport([Response(status=429),Response(body)],credentials=('','synthetic@example.invalid')));self.assertEqual(len(self.opener.calls),2)
    def test_raw_response_byte_hash_preserved(self):
        self.setup_run(versions=('publishedVersion',),protocol=policy(provider_sources_enabled=['OPENALEX']))
        wid=json.loads(self.full.get(self.fid)['handoff_json'])['items'][0]['openalex_work_ids'][0];body=json.dumps(dict(id=wid,locations=[]),indent=2).encode()
        self.full.discover(self.fid,self.transport([Response(body)]));e=self.full.rows('fulltext_evidence',self.fid)[0];self.assertEqual(e['response_sha256'],hashlib.sha256(body).hexdigest());self.assertEqual(self.full.cas.path_for_hash(e['response_sha256']).read_bytes(),body)
    def test_signed_provider_response_refused(self):
        self.setup_run(versions=('publishedVersion',),protocol=policy(provider_sources_enabled=['OPENALEX']))
        wid=json.loads(self.full.get(self.fid)['handoff_json'])['items'][0]['openalex_work_ids'][0]
        body=json.dumps(dict(id=wid,locations=[location(url='https://papers.example.org/a?signature=synthetic-secret')])).encode()
        self.full.discover(self.fid,self.transport([Response(body)]));e=self.full.rows('fulltext_evidence',self.fid)[0];self.assertIsNone(e['response_sha256']);self.assertNotIn('synthetic-secret',json.dumps(e))
    def test_provider_artifact_tamper_detected(self):
        self.setup_run(versions=('publishedVersion',),protocol=policy(provider_sources_enabled=['OPENALEX']));wid=json.loads(self.full.get(self.fid)['handoff_json'])['items'][0]['openalex_work_ids'][0];body=json.dumps(dict(id=wid,locations=[])).encode();self.full.discover(self.fid,self.transport([Response(body)]));e=self.full.rows('fulltext_evidence',self.fid)[0];self.full.cas.path_for_hash(e['response_sha256']).write_bytes(b'{}')
        with self.assertRaises(ArtifactIntegrityError):self.full.verify(self.fid)
    def test_unpaywall_runtime_required(self):
        self.setup_run(protocol=policy(provider_sources_enabled=['UNPAYWALL']))
        with self.assertRaises(ValidationError):self.full.discover(self.fid,self.transport())
    def test_openalex_refresh_all_locations(self):
        self.setup_run(versions=('publishedVersion',),protocol=policy(provider_sources_enabled=['OPENALEX','OPENALEX_CONTENT']))
        item=json.loads(self.full.get(self.fid)['handoff_json'])['items'][0];wid=item['openalex_work_ids'][0]
        body=json.dumps(dict(id=wid,locations_count=101,locations=[location()],best_oa_location=location('submittedVersion',url='https://papers.example.org/preprint'),has_content={'pdf':True},content_urls={'pdf':'https://content.openalex.org/works/'+wid.rsplit('/',1)[-1]+'.pdf'})).encode()
        self.full.discover(self.fid,self.transport([Response(body)],credentials=('SYNTHETIC_SECRET','')));e=json.loads(self.full.rows('fulltext_evidence',self.fid)[0]['metadata_json']);self.assertEqual(e['locations_count'],101);self.assertNotIn('?',self.opener.calls[0]['url']);self.assertTrue(self.full.verify(self.fid)['verified'])
    def test_unpaywall_v2_doi_dedup_redaction(self):
        self.setup_run(versions=('publishedVersion',),protocol=policy(provider_sources_enabled=['UNPAYWALL']))
        item=json.loads(self.full.get(self.fid)['handoff_json'])['items'][0]
        data=dict(doi=item['dois'][0],best_oa_location=dict(host_type='publisher',version='publishedVersion',url_for_pdf='https://papers.example.org/a'),oa_locations=[])
        t=self.transport([Response(json.dumps(data).encode())],credentials=('','synthetic-private@example.invalid'))
        first=self.full._lookup(self.fid,'UNPAYWALL',item['dois'][0],t);second=self.full._lookup(self.fid,'UNPAYWALL',item['dois'][0],t)
        self.assertEqual(first,second);self.assertEqual(len(self.opener.calls),1);self.assertIn('/v2/'+item['dois'][0]+'?email=',self.opener.calls[0]['url']);self.assertNotIn('synthetic-private',json.dumps(first));self.assertNotIn('synthetic-private',json.dumps(self.full.rows('fulltext_request',self.fid)))
    def test_provider_email_echo_not_archived(self):
        self.setup_run(protocol=policy(provider_sources_enabled=['UNPAYWALL']));t=self.transport([Response(b'{"email":"synthetic-private@example.invalid"}')],credentials=('','synthetic-private@example.invalid'));e=self.full._lookup(self.fid,'UNPAYWALL',json.loads(self.full.get(self.fid)['handoff_json'])['items'][0]['dois'][0],t);self.assertIsNone(e['response_sha256']);self.assertNotIn('synthetic-private',str(e))
    def test_unpaywall_404(self):
        self.setup_run(protocol=policy(provider_sources_enabled=['UNPAYWALL']));e=self.full._lookup(self.fid,'UNPAYWALL',json.loads(self.full.get(self.fid)['handoff_json'])['items'][0]['dois'][0],self.transport([Response(status=404)],credentials=('','synthetic@example.invalid')));self.assertEqual(json.loads(e['metadata_json'])['failure_class'],'HTTP_404')
    def test_unpaywall_422(self):
        self.setup_run(protocol=policy(provider_sources_enabled=['UNPAYWALL']));e=self.full._lookup(self.fid,'UNPAYWALL',json.loads(self.full.get(self.fid)['handoff_json'])['items'][0]['dois'][0],self.transport([Response(status=422)],credentials=('','synthetic@example.invalid')));self.assertEqual(json.loads(e['metadata_json'])['failure_class'],'HTTP_422')


class TestSeparation(unittest.TestCase):
    def test_no_upstream_execution_or_analysis_calls(self):
        import ast
        root=Path(__file__).parents[1]/'reprovane/fulltext_acquisition'
        forbidden={'resolve','resolve_seeds','expand_backward','expand_forward','submit_decision','submit_adjudication','extract_text','ocr','execute_frontier','search','embeddings'}
        for path in root.glob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                    if node.func.attr=='search' and isinstance(node.func.value,ast.Name) and node.func.value.id in ('re','SENSITIVE'): continue
                    self.assertNotIn(node.func.attr,forbidden)
    def test_no_retired_search_endpoint(self):
        root=Path(__file__).parents[1]/'reprovane/fulltext_acquisition'
        self.assertNotIn('/v2/search',''.join(p.read_text(encoding='utf-8') for p in root.glob('*.py')))
    def test_migration_drift(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);md=root/'migrations';shutil.copytree(MIGRATION_DIR,md)
            with Database(root/'ws',migration_dir=md) as db:
                FullTextStore(db);(md/'0012_fulltext_acquisition.sql').write_text('-- drift\n'+(md/'0012_fulltext_acquisition.sql').read_text(encoding='utf-8'),encoding='utf-8')
                with self.assertRaises(MigrationDriftError):apply_migrations(db.connection,md,target_version=12)
