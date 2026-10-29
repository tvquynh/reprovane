import hashlib,io,json,sqlite3,os,shutil,unittest
from contextlib import redirect_stdout,redirect_stderr,closing
from unittest.mock import patch
from pathlib import Path
import requests
from openalex_acquisition_fixtures import AcquisitionCase,Response,Session,SYNTHETIC_KEY,SYNTHETIC_EMAIL,work,page
from scholarflow.core import ValidationError,ImmutabilityError
from scholarflow.core.db import Database
from scholarflow.openalex import OpenAlexClient
from scholarflow.openalex_http import OpenAlexHTTPError,OpenAlexPayloadError
from scholarflow.openalex_acquisition import AcquisitionSpec,OpenAlexAcquisitionStore
from scholarflow.openalex_acquisition.credentials import local_client
from scholarflow.openalex_acquisition.__main__ import main
from scholarflow.openalex_acquisition.normalize import normalize_work
from scholarflow.core.migrations import MIGRATION_DIR,apply_migrations


class TestAcquisitionSpec(unittest.TestCase):
    def test_equivalent_requests(self):
        a=AcquisitionSpec.from_value(dict(search='literal',filters={'publication_year':2024,'type':'article'},select=['title','id']))
        b=AcquisitionSpec.from_value(dict(select='id,title,id',filters={'type':'article','publication_year':'2024'},search='literal'))
        self.assertEqual(a.sha256,b.sha256)
    def test_mapping_and_identical_literal_filter(self): self.assertEqual(AcquisitionSpec.from_value(dict(filters={'publication_year':2024})).sha256,AcquisitionSpec.from_value(dict(filters='publication_year:2024')).sha256)
    def test_material_parameters_change_hash(self):
        base=AcquisitionSpec.from_value(dict(search='x'))
        for key,value in [('search','y'),('filters','type:article'),('corpus','all'),('sort','publication_date:desc'),('max_records',2),('page_size',2),('max_pages',2),('select',['title']),('query_mode','title_and_abstract')]:
            with self.subTest(key=key): self.assertNotEqual(base.sha256,AcquisitionSpec.from_value(dict(search='x',**{key:value}) if key!='search' else dict(search=value)).sha256)
    def test_credentials_excluded(self):
        spec=AcquisitionSpec.from_value(dict(search='literal',api_key=SYNTHETIC_KEY,email=SYNTHETIC_EMAIL)); self.assertEqual(spec.sha256,AcquisitionSpec.from_value(dict(search='literal')).sha256); self.assertNotIn(SYNTHETIC_KEY,spec.canonical); self.assertNotIn(SYNTHETIC_EMAIL,spec.canonical)
    def test_supported_page_size(self):
        for value in [0,101,200,True]:
            with self.assertRaises(ValidationError): AcquisitionSpec.from_value(dict(search='x',page_size=value))
    def test_broad_unbounded_rejected(self):
        for values in [{},dict(search='x',max_records=None),dict(search='x',max_pages=10001)]:
            with self.assertRaises(ValidationError): AcquisitionSpec.from_value(values)
    def test_select_id_always_included(self): self.assertEqual(AcquisitionSpec.from_value(dict(search='x',select=['title'])).as_dict()['select'],['id','title'])
    def test_nested_select_rejected(self):
        with self.assertRaises(ValidationError): AcquisitionSpec.from_value(dict(search='x',select=['open_access.is_oa']))
    def test_literal_search_not_rewritten(self):
        text='(malware AND "concept drift") OR naïve'; self.assertEqual(AcquisitionSpec.from_value(dict(search=text)).parameters('*',5)['search'],text)
    def test_explicit_core_default(self): self.assertEqual(AcquisitionSpec.from_value(dict(search='x')).parameters('*',1)['corpus'],'core')
    def test_scoped_search(self): self.assertIn('search.title_and_abstract',AcquisitionSpec.from_value(dict(search='x',query_mode='title_and_abstract')).parameters('*',1))
    def test_deprecated_secret_unknown_parameters_fail(self):
        for values in [dict(include_xpac=True),dict(filters='is_xpac:true'),dict(filters={'api_key':'value'}),dict(endpoint='authors')]:
            with self.assertRaises(ValidationError): AcquisitionSpec.from_value(dict(search='x',**values))


class TestOpenAlexAcquisition(AcquisitionCase):
    def test_schema7_upgrade_idempotent(self):
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],7); OpenAlexAcquisitionStore(self.db); self.assertEqual(self.db.execute('SELECT max(version) FROM schema_migration').fetchone()[0],7)
    def test_explicit_schema6_upgrade(self):
        from scholarflow.venue_intelligence import VenueIntelligenceStore
        with Database(self.root/'upgrade') as db:
            VenueIntelligenceStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6); OpenAlexAcquisitionStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
    def test_migration_drift(self):
        directory=self.root/'migrations'; shutil.copytree(MIGRATION_DIR,directory); path=directory/'0007_openalex_acquisition.sql'; path.write_text(path.read_text()+'\n-- drift\n')
        with self.assertRaises(Exception): apply_migrations(self.db.connection,directory,target_version=7)
    def test_exact_cursor_paging_and_natural_completion(self):
        cursor='opaque+/== cursor'; r=self.acquire([Response(page([work(1),work(2)],cursor)),Response(page([work(3)],None))]); self.assertEqual(r['state'],'COMPLETE'); self.assertEqual(r['termination'],'NATURAL'); self.assertEqual([c['params']['cursor'] for c in self.session.calls],['*',cursor]); self.assertEqual(r['work_count'],3)
    def test_explicit_limit_not_query_exhaustion(self):
        r=self.acquire([Response(page([work(1),work(2)],'more'))],dict(search='x',max_records=2,page_size=2)); self.assertEqual(r['termination'],'RECORD_LIMIT'); self.assertEqual(r['next_cursor'],'more'); self.assertEqual(r['page_count'],1)
    def test_duplicate_work_across_pages(self):
        r=self.acquire([Response(page([work(1),work(2)],'two')),Response(page([work(1)],None))]); self.assertEqual(r['work_count'],2); rows=self.store.foundation.list_provider_records(r['snapshot_id']); self.assertEqual(len(rows),2); self.assertEqual(len(json.loads(next(x for x in rows if x['provider_native_id'].endswith('W1'))['metadata_json'])['occurrences']),2)
    def test_empty_terminal_page(self):
        r=self.acquire([Response(page([],None))]); self.assertEqual(r['termination'],'NATURAL'); self.assertEqual(r['work_count'],0)
    def test_page_bound_duplicate_loop(self):
        r=self.acquire([Response(page([work(1)],'next'))],dict(search='x',max_records=10,page_size=2,max_pages=1)); self.assertEqual(r['termination'],'PAGE_LIMIT')
    def test_missing_meta_fails(self):
        with self.assertRaises(ValidationError): self.acquire([Response(dict(results=[]))])
        self.assertEqual(self.db.execute('SELECT state FROM openalex_acquisition').fetchone()[0],'INTERRUPTED'); self.assertEqual(self.db.execute('SELECT count(*) FROM provider_snapshot').fetchone()[0],0)
    def test_missing_next_cursor_fails(self):
        with self.assertRaises(ValidationError): self.acquire([Response(dict(meta={},results=[]))])
    def test_invalid_work_id_not_synthesized(self):
        with self.assertRaises(ValueError): self.acquire([Response(page([dict(title='No ID',doi='10.1234/not-id')],None))])
    def test_cursor_cycle_fails(self):
        with self.assertRaises(ValidationError): self.acquire([Response(page([work(1)],'*'))])
    def test_oversized_result_count_fails(self):
        with self.assertRaises(ValidationError): self.acquire([Response(page([work(1),work(2),work(3)]))])
    def test_http_before_first_page_no_sealed_snapshot(self):
        with self.assertRaises(OpenAlexHTTPError): self.acquire([Response(status=400)])
        r=dict(self.db.execute('SELECT * FROM openalex_acquisition').fetchone()); self.assertEqual((r['state'],r['page_count'],r['snapshot_id']),('INTERRUPTED',0,None))
    def test_failure_page2_resume(self):
        spec=dict(search='resume',max_records=3,page_size=2)
        with self.assertRaises(OpenAlexHTTPError): self.acquire([Response(page([work(1),work(2)],'saved')),Response(status=400)],spec)
        r=dict(self.db.execute('SELECT * FROM openalex_acquisition').fetchone()); self.assertEqual(r['next_cursor'],'saved'); self.assertEqual(r['page_count'],1); self.assertIsNone(r['snapshot_id'])
        self.session.responses=[Response(page([work(3)],None))]; done=self.store.resume(r['acquisition_id'],spec,self.client); self.assertEqual(done['work_count'],3); self.assertEqual(self.session.calls[-1]['params']['cursor'],'saved')
    def test_changed_spec_cannot_resume(self):
        spec=AcquisitionSpec.from_value(dict(search='first')); r=self.store.create(self.store.ensure_revision(spec),spec)
        with self.assertRaises(ValidationError): self.store.resume(r['acquisition_id'],dict(search='different'),self.client)
        self.assertFalse(self.session.calls)
    def test_complete_idempotent_no_network(self):
        first=self.acquire([Response(page())]); count=self.db.execute('SELECT count(*) FROM publication_metadata_observation').fetchone()[0]; second=self.acquire(); self.assertEqual(first,second); self.assertEqual(len(self.session.calls),1); self.assertEqual(self.db.execute('SELECT count(*) FROM publication_metadata_observation').fetchone()[0],count)
    def test_repeated_normalization_same_record_idempotent(self):
        result=self.acquire([Response(page())]); record=self.store.foundation.list_provider_records(result['snapshot_id'])[0]
        before=self.store.observations.list_observations_for_provider_record(record['provider_record_id'])
        normalize_work(self.store.observations,record['provider_record_id'],work()); self.assertEqual(self.store.observations.list_observations_for_provider_record(record['provider_record_id']),before)
    def test_changed_request_creates_different_snapshot(self):
        first=self.acquire([Response(page())]); second=self.acquire([Response(page())],dict(search='another')); self.assertNotEqual(first['snapshot_id'],second['snapshot_id'])
    def test_raw_bytes_retained_and_hash(self):
        response=Response(page()); r=self.acquire([response]); receipt=self.store.pages(r['acquisition_id'])[0]; self.assertEqual(receipt['artifact_sha256'],hashlib.sha256(response.body).hexdigest()); self.assertEqual(self.store.foundation.artifacts.path_for_hash(receipt['artifact_sha256']).read_bytes(),response.body)
    def test_record_native_and_page_locator(self):
        r=self.acquire([Response(page())]); record=self.store.foundation.list_provider_records(r['snapshot_id'])[0]; self.assertEqual(record['provider_native_id'],'https://openalex.org/W1'); self.assertIn('/results/0',record['record_locator']); self.assertTrue(record['provider_record_id'].startswith('rec_'))
    def test_publication_fields_and_doi(self):
        self.acquire([Response(page())]); row=self.db.execute('SELECT * FROM publication_metadata_observation').fetchone(); self.assertEqual((row['title'],row['publication_year'],row['publication_date'],row['publication_type']),('Synthetic Work 1',2024,'2024-01-02','article')); self.assertEqual(self.db.execute("SELECT identifier_value_normalized FROM publication_identifier_observation WHERE identifier_namespace='doi'").fetchone()[0],'10.1234/test1')
    def test_absent_doi_no_fake(self):
        self.acquire([Response(page([work(doi=None)]))]); self.assertEqual(self.db.execute("SELECT count(*) FROM publication_identifier_observation WHERE identifier_namespace='doi'").fetchone()[0],0)
    def test_malformed_doi_retained_untrusted(self):
        self.acquire([Response(page([work(doi='not-a-doi')]))]); self.assertEqual(self.db.execute("SELECT identifier_value_normalized FROM publication_identifier_observation WHERE identifier_namespace='doi'").fetchone()[0],'not-a-doi')
    def test_authorship_order_native_author_ids(self):
        self.acquire([Response(page())]); rows=self.db.execute('SELECT * FROM publication_authorship_observation ORDER BY author_position').fetchall(); self.assertEqual([r['author_position'] for r in rows],[1,2]); self.assertEqual(rows[0]['provider_author_id_normalized'],'https://openalex.org/A1')
    def test_missing_author_name_preserves_position_gap(self):
        self.acquire([Response(page([work(authorships=[dict(author={'id':'https://openalex.org/A0'}),dict(author={'display_name':'Known'})])]))]); self.assertEqual(self.db.execute('SELECT author_position FROM publication_authorship_observation').fetchone()[0],2)
    def test_embedded_source_snapshot_separate(self):
        r=self.acquire([Response(page())]); record=self.store.foundation.list_provider_records(r['source_snapshot_id'])[0]; self.assertEqual(record['provider_native_id'],'https://openalex.org/S1'); self.assertEqual(self.store.foundation.get_provider_snapshot(r['source_snapshot_id'])['dataset'],'embedded_sources')
    def test_venue_issn_linking_evidence(self):
        self.acquire([Response(page())]); namespaces={r[0] for r in self.db.execute('SELECT identifier_namespace FROM venue_identifier_observation')}; self.assertTrue({'issn','issn_l','openalex_source_id'}<=namespaces)
    def test_no_provider_membership_inference(self):
        self.acquire([Response(page())]); self.assertEqual(self.db.execute('SELECT count(*) FROM venue_membership_observation').fetchone()[0],0)
    def test_citation_count_record_metadata_not_venue_metric(self):
        r=self.acquire([Response(page())]); row=self.store.foundation.list_provider_records(r['snapshot_id'])[0]; self.assertEqual(json.loads(row['metadata_json'])['reported_cited_by_count'],12); self.assertEqual(self.db.execute('SELECT count(*) FROM venue_metric_observation').fetchone()[0],0)
    def test_retraction_report_no_effective_date_invented(self):
        self.acquire([Response(page([work(is_retracted=True)]))]); row=self.db.execute('SELECT * FROM integrity_event_observation').fetchone(); self.assertEqual(row['event_type'],'RETRACTION'); self.assertIsNone(row['effective_date'])
    def test_no_reference_expansion_fulltext_author_entity(self):
        self.acquire([Response(page())]); self.assertEqual(len(self.session.calls),1); self.assertEqual(self.db.execute('SELECT count(*) FROM citation_edge_observation').fetchone()[0],0); self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE lower(name)='author_entity'").fetchone())
    def test_no_publication_resolution_performed(self):
        self.acquire([Response(page())]); self.assertEqual(self.db.execute('SELECT count(*) FROM resolution_run').fetchone()[0],0)
    def test_database_normalization_failure_atomic_resume_without_http(self):
        with patch('scholarflow.openalex_acquisition.store.normalize_work',side_effect=ValidationError('injected')):
            with self.assertRaises(ValidationError): self.acquire([Response(page())])
        r=dict(self.db.execute('SELECT * FROM openalex_acquisition').fetchone()); self.assertEqual(r['state'],'INTERRUPTED'); self.assertIsNone(r['snapshot_id']); self.assertEqual(self.db.execute('SELECT count(*) FROM provider_snapshot').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM publication_metadata_observation').fetchone()[0],0); done=self.store.resume(r['acquisition_id'],json.loads(r['request_spec_json']),self.client); self.assertEqual(done['state'],'COMPLETE'); self.assertEqual(len(self.session.calls),1)
    def test_corrupt_artifact_replay_fails(self):
        r=self.acquire([Response(page())]); receipt=self.store.pages(r['acquisition_id'])[0]; self.store.foundation.artifacts.path_for_hash(receipt['artifact_sha256']).write_bytes(b'corrupt')
        with self.assertRaises(Exception): self.store.resume(r['acquisition_id'],json.loads(r['request_spec_json']),self.client)
    def test_raw_receipt_update_delete_replace_guards(self):
        r=self.acquire([Response(page())]); row=self.store.pages(r['acquisition_id'])[0]
        with self.assertRaises(ImmutabilityError): self.db.execute('DELETE FROM openalex_acquisition_page WHERE acquisition_id=?',(r['acquisition_id'],))
        with closing(sqlite3.connect(self.db.path)) as conn:
            conn.execute('PRAGMA recursive_triggers=OFF')
            conn.create_function('openalex_acquisition_authorized',1,lambda aid:0)
            with self.assertRaises(sqlite3.IntegrityError): conn.execute('INSERT OR REPLACE INTO openalex_acquisition_page ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
    def test_completed_acquisition_locked(self):
        r=self.acquire([Response(page())])
        with self.assertRaises(ImmutabilityError): self.db.execute("UPDATE openalex_acquisition SET state='INTERRUPTED' WHERE acquisition_id=?",(r['acquisition_id'],))
    def test_keyless_first_page_can_seal_limit(self):
        self.client.api_key=''; r=self.acquire([Response(page([work(1)],'more'))],dict(search='x',max_records=1)); self.assertEqual(r['state'],'COMPLETE')
    def test_keyless_continuation_fails_before_network(self):
        self.client.api_key=''
        with self.assertRaises(ValidationError): self.acquire([Response(page([work(1)],'more'))])
        self.assertEqual(len(self.session.calls),1)
    def test_secret_absent_from_persisted_data(self):
        r=self.acquire([Response(page())]); text=json.dumps([r,self.store.pages(r['acquisition_id']),self.store.foundation.get_provider_snapshot(r['snapshot_id'])]); self.assertNotIn(SYNTHETIC_KEY,text); self.assertNotIn(SYNTHETIC_EMAIL,text); self.assertNotIn('Authorization',text); self.assertNotIn('api_key',self.session.calls[0]['params'])
    def test_secret_in_response_rejected_before_cas(self):
        with self.assertRaises(OpenAlexPayloadError): self.acquire([Response(page([work(title=SYNTHETIC_KEY)]))])
        self.assertEqual(self.db.execute('SELECT count(*) FROM raw_artifact').fetchone()[0],0)
    def test_secret_in_spec_rejected_before_revision(self):
        with self.assertRaises(OpenAlexPayloadError): self.acquire([],dict(search=SYNTHETIC_KEY))
        self.assertEqual(self.db.execute('SELECT count(*) FROM review_project').fetchone()[0],0)
    def test_rate_headers_only_numeric(self):
        r=self.acquire([Response(page(),headers={'X-RateLimit-Remaining':'42','X-RateLimit-Limit':'not-numeric','Set-Cookie':'private'})]); meta=json.loads(self.store.pages(r['acquisition_id'])[0]['response_metadata_json']); self.assertEqual(meta['rate'],{'x-ratelimit-remaining':42.0})
    def test_429_and_500_retries_then_success(self):
        self.acquire([Response(status=429,headers={'Retry-After':'1000'}),Response(status=500),Response(page())]); self.assertEqual(self.sleeps,[10,2]); self.assertEqual(len(self.session.calls),3)
    def test_retry_exhaustion_unsealed(self):
        with self.assertRaises(OpenAlexHTTPError): self.acquire([Response(status=429) for _ in range(3)])
        self.assertEqual(len(self.session.calls),3); self.assertEqual(self.db.execute('SELECT count(*) FROM provider_snapshot').fetchone()[0],0)
    def test_400_single_attempt_no_body_echo(self):
        with self.assertRaises(OpenAlexHTTPError) as cm: self.acquire([Response(status=400,body=SYNTHETIC_KEY.encode())])
        self.assertEqual(len(self.session.calls),1); self.assertNotIn(SYNTHETIC_KEY,str(cm.exception))
    def test_malformed_json_unsealed(self):
        with self.assertRaises(OpenAlexPayloadError): self.acquire([Response(body=b'not-json')])
        self.assertEqual(self.db.execute('SELECT count(*) FROM provider_snapshot').fetchone()[0],0)
    def test_timeouts_bounded_and_redacted(self):
        with self.assertRaises(requests.RequestException) as cm: self.acquire([requests.ReadTimeout(SYNTHETIC_KEY) for _ in range(3)])
        self.assertEqual(len(self.session.calls),3); self.assertNotIn(SYNTHETIC_KEY,str(cm.exception)); self.assertEqual(self.session.calls[0]['timeout'],(10,60))
    def test_redirects_do_not_leak_header(self):
        with self.assertRaises(OpenAlexHTTPError): self.acquire([Response(status=302)])
        self.assertFalse(self.session.calls[0]['allow_redirects']); self.assertEqual(len(self.session.calls),1)
    def test_page_size_cap(self):
        self.client.max_response_bytes=2
        with self.assertRaises(OpenAlexPayloadError): self.acquire([Response(page())])
    def test_config_then_environment_no_repr(self):
        config=self.root/'config.json'; config.write_text(json.dumps(dict(openalex=dict(api_key=SYNTHETIC_KEY,email=SYNTHETIC_EMAIL))))
        with patch.dict(os.environ,{'OPENALEX_API_KEY':'SYNTHETIC_OTHER_ENV_VALUE'}):
            client=local_client(config_path=config,session=Session()); self.assertEqual(client.api_key,SYNTHETIC_KEY); self.assertNotIn(SYNTHETIC_KEY,repr(client.private)); client.close()
    def test_new_windows_user_environment_fallback(self):
        config=self.root/'empty-config.json'; config.write_text('{}')
        with patch.dict(os.environ,{},clear=True),patch('scholarflow.openalex_acquisition.credentials._user_environment_key',return_value=SYNTHETIC_KEY):
            client=local_client(config_path=config,session=Session()); self.assertEqual(client.api_key,SYNTHETIC_KEY); client.close()
    def test_cli_help_no_credentials_network(self):
        with patch('scholarflow.openalex_acquisition.__main__.local_client',side_effect=AssertionError('forbidden')),redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as cm: main(['--help'])
        self.assertEqual(cm.exception.code,0)
    def test_nonofficial_host_rejected(self):
        with self.assertRaises(ValueError): OpenAlexClient(base_url='https://example.invalid/works')
    def test_unbounded_timeout_rejected(self):
        for timeout in [None,(1,None),(0,60),(float('inf'),1)]:
            with self.assertRaises(ValueError): OpenAlexClient(timeout=timeout)
    def test_escaped_private_response_rejected(self):
        data=json.dumps(page([work(title=SYNTHETIC_KEY)])).replace(SYNTHETIC_KEY,''.join('\\u'+format(ord(c),'04x') for c in SYNTHETIC_KEY))
        with self.assertRaises(OpenAlexPayloadError): self.acquire([Response(body=data.encode())])
    def test_runtime_email_not_persisted_if_returned(self):
        with self.assertRaises(OpenAlexPayloadError): self.acquire([Response(page([work(title=SYNTHETIC_EMAIL)]))])
    def test_cli_acquire_inspect(self):
        spec=self.root/'spec.json'; spec.write_text(json.dumps(dict(search='cli',max_records=1))); self.session.responses=[Response(page())]; out=io.StringIO()
        with patch('scholarflow.openalex_acquisition.__main__.local_client',return_value=self.client),redirect_stdout(out): self.assertEqual(main(['acquire','--workspace',str(self.root/'cli'),'--spec',str(spec)]),0)
        result=json.loads(out.getvalue()); out=io.StringIO()
        with patch('scholarflow.openalex_acquisition.__main__.local_client',side_effect=AssertionError('no network')),redirect_stdout(out): self.assertEqual(main(['inspect','--workspace',str(self.root/'cli'),'--acquisition-id',result['acquisition_id']]),0)
        self.assertIn('COMPLETE',out.getvalue())
