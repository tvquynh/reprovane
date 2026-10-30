import io,json,sqlite3,hashlib
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
from unittest.mock import patch
from venue_intelligence_fixtures import IntelligenceCase,SCOPUS_HEADER,WOS_HEADER,write_csv,scopus_row,wos_row
from mirror_fixtures import A,B,REPO,raw,Response
from venue_fixtures import VenueCase,issn,build_package,SELECTOR
from reprovane.core import ValidationError,ImmutabilityError
from reprovane.core.db import Database
from reprovane.core.foundation import Foundation
from reprovane.observations import NormalizationProvenance
from reprovane.venue_intelligence import VenueIntelligenceStore,import_venue_lists,sync_venue_lists,resolve_imported_snapshots,VenueIntelligence,PROFILES
from reprovane.venue_intelligence.parsers import parse_rows,coverage
from reprovane.mirror import MirrorSyncError,MirrorOfflineError
from reprovane.venue_intelligence.__main__ import main
from reprovane.providers.scimago import import_scimago_source_snapshot
from reprovane.venue_resolution.projection import ScimagoVenueProjection
from reprovane.core.migrations import apply_migrations


class TestVenueIntelligence(IntelligenceCase):
    def setUp(self):
        super().setUp(); self.folder=self.seed_lists(); self.db=Database(self.root/'research'); self.store=VenueIntelligenceStore(self.db)
    def tearDown(self): self.db.close(); super().tearDown()
    def sync_lists(self,**kw): return sync_venue_lists(self.workspace,client=self.client,**kw)
    def imported(self,**kw):
        result=self.sync_lists(); return import_venue_lists(self.db,self.workspace,result['snapshot_directory'],**kw)
    def resolution(self,items=None):
        items=items or self.imported(); return resolve_imported_snapshots(self.db,[i['snapshot']['snapshot_id'] for i in items])
    def parsed(self,provider='scopus',collection='SCIE'):
        return list(parse_rows(self.folder/('SCOPUS/ext_list_Jul_2026.csv' if provider=='scopus' else f'WOS/{collection}.csv'),provider,collection))
    def test_schema6_and_prior_upgrade(self):
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],6); self.assertEqual([r[0] for r in self.db.execute('SELECT version FROM schema_migration ORDER BY version')],[1,2,3,4,5,6])
    def test_real_scopus_header(self):
        row=self.parsed()[0]; self.assertEqual(row['native'],'100'); self.assertEqual(row['status'],'ACTIVE'); self.assertEqual(row['source_type'],'Journal')
    def test_real_wos_header_all_collections(self):
        for c in ['SCIE','SSCI','AHCI','ESCI']:
            write_csv(self.folder/f'WOS/{c}.csv',WOS_HEADER,[wos_row()]); self.assertEqual(self.parsed('wos',c)[0]['collection'],c)
    def test_multivalued_asjc(self): self.assertEqual(self.parsed()[0]['attributes']['ASJC']['codes'],['1407','1701'])
    def test_asjc_broad_categories_preserved(self): self.assertEqual(self.parsed()[0]['attributes']['ASJC']['categories'][0]['code'],'1400')
    def test_multiple_wos_categories_and_languages(self):
        row=self.parsed('wos')[0]; self.assertEqual(row['attributes']['CATEGORIES']['values'],['Information Systems','Management']); self.assertEqual(row['attributes']['LANGUAGES']['values'],['English','French'])
    def test_blank_optional_unknown_status(self):
        write_csv(self.folder/'SCOPUS/ext_list_Jul_2026.csv',SCOPUS_HEADER,[scopus_row(**{'Active or Inactive':'','Coverage':'','ISSN':'','Publisher':''})]); row=self.parsed()[0]; self.assertEqual(row['status'],'UNKNOWN'); self.assertEqual(row['identifiers'],[])
    def test_status_tokens_only_explicit(self):
        for token,expected in [(' inactive ','INACTIVE'),('Active','ACTIVE'),('discontinued','UNKNOWN'),('NULL','UNKNOWN')]:
            write_csv(self.folder/'SCOPUS/ext_list_Jul_2026.csv',SCOPUS_HEADER,[scopus_row(**{'Active or Inactive':token})]); self.assertEqual(self.parsed()[0]['status'],expected)
    def test_coverage_gaps_no_envelope(self):
        value=coverage('2026; 2023-2024; 1999-Present; unknown'); self.assertEqual(len(value['segments']),3); self.assertEqual(value['unparsed'],['unknown']); self.assertEqual(value['original'],'2026; 2023-2024; 1999-Present; unknown')
    def test_malformed_issn_retained_warning(self):
        write_csv(self.folder/'SCOPUS/ext_list_Jul_2026.csv',SCOPUS_HEADER,[scopus_row(ISSN='12345678')]); row=self.parsed()[0]; self.assertEqual(row['invalid_issns'],['12345678'])
    def test_extra_column_tolerated_and_logged(self):
        write_csv(self.folder/'SCOPUS/ext_list_Jul_2026.csv',SCOPUS_HEADER+['New optional'],[scopus_row(**{'New optional':'data'})]); self.assertIn('New optional',self.parsed()[0]['unknown_columns'])
    def test_missing_structural_column_fails(self):
        write_csv(self.folder/'SCOPUS/ext_list_Jul_2026.csv',[h for h in SCOPUS_HEADER if h!='Source Title'],[])
        with self.assertRaises(ValidationError): self.parsed()
    def test_duplicate_header_fails(self):
        p=self.folder/'WOS/SCIE.csv'; p.write_text('Journal title,Journal title\nAlpha,Alpha\n')
        with self.assertRaises(ValidationError): self.parsed('wos')
    def test_missing_title_row_fails(self):
        write_csv(self.folder/'SCOPUS/ext_list_Jul_2026.csv',SCOPUS_HEADER,[scopus_row(**{'Source Title':''})])
        with self.assertRaises(ValidationError): self.parsed()
    def test_bad_native_row_fails(self):
        write_csv(self.folder/'SCOPUS/ext_list_Jul_2026.csv',SCOPUS_HEADER,[scopus_row(**{'Sourcerecord ID':'../bad'})])
        with self.assertRaises(ValidationError): self.parsed()
    def test_bad_width_fails(self):
        p=self.folder/'WOS/SCIE.csv'; p.write_text(','.join(WOS_HEADER)+'\nshort,row\n')
        with self.assertRaises(ValidationError): self.parsed('wos')
    def test_bom_and_header_whitespace(self):
        write_csv(self.folder/'WOS/SCIE.csv',[' '+h+' ' for h in WOS_HEADER],[]); self.assertEqual(self.parsed('wos'),[])
    def test_import_idempotent(self):
        first=self.imported(); counts=[self.db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['provider_snapshot','venue_membership_observation','venue_provider_attribute_observation']]; second=self.imported(); self.assertEqual([i['snapshot']['snapshot_id'] for i in first],[i['snapshot']['snapshot_id'] for i in second]); self.assertTrue(all(i['reused'] for i in second)); self.assertEqual(counts,[self.db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['provider_snapshot','venue_membership_observation','venue_provider_attribute_observation']])
    def test_duplicate_rows_no_duplicate_memberships(self):
        self.seed_lists(scopus=[scopus_row(),scopus_row()],wos={'SCIE':[wos_row(),wos_row()],'SSCI':[wos_row()]}); imported=self.imported(); self.assertEqual(imported[0]['statistics']['logical_provider_records'],1); self.assertEqual(self.db.execute("SELECT count(*) FROM venue_membership_observation WHERE membership_namespace='scopus'").fetchone()[0],1); self.assertEqual(self.db.execute("SELECT count(*) FROM venue_membership_observation WHERE membership_namespace='wos'").fetchone()[0],2)
    def test_positive_collection_memberships(self):
        self.imported(); rows=self.db.execute("SELECT collection_code,status_code FROM venue_membership_observation WHERE membership_namespace='wos'").fetchall(); self.assertEqual({tuple(r) for r in rows},{('SCIE','PRESENT_IN_SNAPSHOT'),('SSCI','PRESENT_IN_SNAPSHOT')})
    def test_all_four_collection_codes(self):
        self.seed_lists(wos={c:[wos_row()] for c in ['SCIE','SSCI','AHCI','ESCI']}); items=self.imported(); self.assertEqual(items[1]['statistics']['multi_collection_venue_count'],1); self.assertEqual(self.db.execute("SELECT count(*) FROM venue_membership_observation WHERE membership_namespace='wos'").fetchone()[0],4)
    def test_esci_only(self):
        self.seed_lists(wos={'ESCI':[wos_row()]}); result=self.resolution(); entity=self.store.venues.list_venue_entities(result['run']['venue_resolution_run_id'])[0]['venue_entity_id']; q=VenueIntelligence(self.store.venues).query(result['run']['venue_resolution_run_id'],entity); self.assertEqual(q['wos']['collections'],['ESCI'])
    def test_scopus_inactive_from_own_source(self):
        self.seed_lists(scopus=[scopus_row(**{'Active or Inactive':'Inactive'})]); result=self.resolution(); venue=self.store.venues.list_venue_entities(result['run']['venue_resolution_run_id'])[0]; q=VenueIntelligence(self.store.venues).query(result['run']['venue_resolution_run_id'],venue['venue_entity_id']); self.assertEqual(q['scopus']['statuses'],['INACTIVE'])
    def test_active_no_scimago_inference(self):
        self.imported(); self.assertEqual(self.db.execute("SELECT count(*) FROM venue_membership_observation WHERE membership_namespace='scimago'").fetchone()[0],0)
    def test_native_id_not_fabricated_wos(self):
        items=self.imported(); f=Foundation(self.db); self.assertEqual(f.list_provider_records(items[0]['snapshot']['snapshot_id'])[0]['provider_native_id'],'100'); self.assertIsNone(f.list_provider_records(items[1]['snapshot']['snapshot_id'])[0]['provider_native_id'])
    def test_raw_anchors_all_collections(self):
        items=self.imported(); r=Foundation(self.db).list_provider_records(items[1]['snapshot']['snapshot_id'])[0]; meta=json.loads(r['metadata_json']); self.assertEqual({a['collection'] for a in meta['raw_row_anchors']},{'SCIE','SSCI'})
    def test_exact_commit_and_hash_provenance(self):
        items=self.imported()
        for i in items:
            s=i['snapshot']; self.assertEqual(s['mirror_commit_sha'],A); self.assertIn(A,s['source_locator']); self.assertTrue(json.loads(s['metadata_json'])['immutable_distribution_reference']); self.assertTrue(all(len(h)==64 for h in json.loads(s['metadata_json'])['files'].values()))
    def test_atomic_typed_failure_rolls_back_both_providers(self):
        result=self.sync_lists()
        original=VenueIntelligenceStore.add_attribute
        def fail_wos(store,record,provenance,namespace,*args,**kwargs):
            if namespace=='wos': raise ValidationError('injected second-provider failure')
            return original(store,record,provenance,namespace,*args,**kwargs)
        with patch.object(VenueIntelligenceStore,'add_attribute',new=fail_wos):
            with self.assertRaises(ValidationError): import_venue_lists(self.db,self.workspace,result['snapshot_directory'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM provider_snapshot').fetchone()[0],0); self.assertEqual(self.db.execute('SELECT count(*) FROM venue_observation').fetchone()[0],0)
    def test_retry_after_failure_safe(self):
        result=self.sync_lists()
        with patch.object(VenueIntelligenceStore,'add_attribute',side_effect=ValidationError('injected')):
            with self.assertRaises(ValidationError): import_venue_lists(self.db,self.workspace,result['snapshot_directory'])
        items=import_venue_lists(self.db,self.workspace,result['snapshot_directory']); self.assertEqual(len(items),2)
    def test_sealed_snapshot_and_attributes_immutable(self):
        self.imported(); row=self.db.execute('SELECT * FROM venue_provider_attribute_observation LIMIT 1').fetchone()
        for sql in ["UPDATE venue_provider_attribute_observation SET attribute_code='RAW_STATUS' WHERE observation_id=?",'DELETE FROM venue_provider_attribute_observation WHERE observation_id=?']:
            with self.assertRaises(ImmutabilityError): self.db.execute(sql,(row['observation_id'],))
    def test_attribute_replace_external_off(self):
        self.imported(); row=dict(self.db.execute('SELECT * FROM venue_provider_attribute_observation LIMIT 1').fetchone())
        from contextlib import closing
        with closing(sqlite3.connect(self.db.path)) as conn:
            conn.execute('PRAGMA recursive_triggers=OFF')
            with self.assertRaises(sqlite3.IntegrityError): conn.execute('INSERT OR REPLACE INTO venue_provider_attribute_observation ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
    def test_resolution_shared_issn_title_variation(self):
        self.seed_lists(wos={'SCIE':[wos_row(**{'Journal title':'Alpha-Journal revised'})]}); result=self.resolution(); self.assertEqual(result['statistics']['scopus']['matched'],1)
    def test_conflicting_issn_same_title_no_merge(self):
        self.seed_lists(wos={'SCIE':[wos_row(ISSN=issn('7654321'))]}); result=self.resolution(); self.assertEqual(result['statistics']['scopus']['ambiguous'],1); self.assertEqual(len(self.store.venues.list_venue_entities(result['run']['venue_resolution_run_id'])),2)
    def test_multi_collection_query_categories(self):
        result=self.resolution(); rid=result['run']['venue_resolution_run_id']; entity=self.store.venues.list_venue_entities(rid)[0]['venue_entity_id']; query=VenueIntelligence(self.store.venues).query(rid,entity); self.assertEqual(query['wos']['collections'],['SCIE','SSCI']); self.assertFalse('indexed' in query); self.assertEqual(query['scopus']['records'][0]['attributes'][0]['code'] in ['ASJC','COVERAGE','LANGUAGES','RAW_STATUS'],True)
    def test_frozen_attributes_do_not_change_after_append(self):
        result=self.resolution(); rid=result['run']['venue_resolution_run_id']; store=self.store.venues; eid=store.list_venue_entities(rid)[0]['venue_entity_id']; before=VenueIntelligence(store).query(rid,eid); record=self.db.execute("SELECT r.provider_record_id FROM provider_record r JOIN provider_snapshot s ON s.snapshot_id=r.snapshot_id JOIN provider p ON p.provider_id=s.provider_id WHERE p.semantic_namespace='scopus'").fetchone()[0]; self.store.add_attribute(record,NormalizationProvenance('scopus-venue-list','1','1'),'scopus','ASJC',dict(original='9999',codes=['9999']),'csv:ASJC'); self.assertEqual(VenueIntelligence(store).query(rid,eid),before); self.assertTrue(store.verify_venue_resolution_run(rid))
    def test_same_commit_offline_no_network(self):
        first=self.sync_lists(); n=len(self.session.calls); offline=self.sync_lists(offline=True); self.assertEqual(offline['resolved_commit_sha'],A); self.assertEqual(len(self.session.calls),n)
    def test_corrupt_object_offline_rejected(self):
        r=self.sync_lists(); digest=next(iter(r['files'].values())); self.cache.object_path(digest).write_bytes(b'bad')
        with self.assertRaises(MirrorOfflineError): self.sync_lists(offline=True)
    def test_branch_move_new_identity_old_unchanged(self):
        first=self.imported(); old=first[0]['snapshot']['snapshot_id']; self.seed_lists(B); second=self.imported(); self.assertNotEqual(old,second[0]['snapshot']['snapshot_id']); self.assertEqual(Foundation(self.db).get_provider_snapshot(old)['mirror_commit_sha'],A)
    def test_partial_download_no_state_advance(self):
        self.sync_lists(); state={p:p.read_bytes() for p in (self.cache.root/'state').rglob('*.json')}; self.seed_lists(B); self.session.routes[raw(B,'WOS/SSCI.csv')]=Response(status=404)
        with self.assertRaises(MirrorSyncError): self.sync_lists()
        self.assertEqual({p:p.read_bytes() for p in (self.cache.root/'state').rglob('*.json')},state)
        self.assertEqual(self.sync_lists(requested_ref=A,offline=True)['resolved_commit_sha'],A)
    def test_import_statistics_deterministic(self):
        first=self.imported(); second=self.imported(); self.assertEqual([x['statistics'] for x in first],[x['statistics'] for x in second]); self.assertEqual(first[1]['statistics']['rows_per_collection'],{'AHCI':0,'ESCI':0,'SCIE':1,'SSCI':1})
    def test_cli_help_no_credentials(self):
        out=io.StringIO()
        with redirect_stdout(out),self.assertRaises(SystemExit) as cm: main(['--help'])
        self.assertEqual(cm.exception.code,0); self.assertIn('sync',out.getvalue()); self.assertNotIn('api-key',out.getvalue())
    def test_cli_import_stats_query(self):
        result=self.sync_lists(); args=['--workspace',str(self.workspace)]; output=io.StringIO()
        with redirect_stdout(output): self.assertEqual(main(['import',*args,'--snapshot-directory',result['snapshot_directory']]),0)
        imported=json.loads(output.getvalue()); output=io.StringIO()
        with redirect_stdout(output): self.assertEqual(main(['stats',*args,'--snapshot-id',imported[0]['snapshot']['snapshot_id']]),0)
        self.assertIn('rows_read',output.getvalue())
    def test_cli_validation_error_safe(self):
        output=io.StringIO()
        with redirect_stderr(output): self.assertEqual(main(['import','--workspace',str(self.workspace),'--snapshot-directory',str(self.root/'missing')]),2)
        self.assertNotIn('missing',output.getvalue())
    def test_scimago_present_scopus_inactive_coexist(self):
        self.seed_lists(scopus=[scopus_row(**{'Active or Inactive':'Inactive'})],wos={}); items=self.imported(provider='scopus')
        folder=self.root/'scimago'; build_package(folder); imported=import_scimago_source_snapshot(self.db,folder,2023)
        result=resolve_imported_snapshots(self.db,[items[0]['snapshot']['snapshot_id'],imported['snapshot']['snapshot_id']],extra_profiles=[dict(normalizer_name='scimago-local-normalizer',normalizer_version='1',observation_schema_version='1')])
        store=self.store.venues; rid=result['run']['venue_resolution_run_id']; eid=store.list_venue_entities(rid)[0]['venue_entity_id']
        self.assertEqual(ScimagoVenueProjection(store).scimago_presence_status(rid,eid),'SCIMAGO_PRESENT_LATEST'); self.assertEqual(VenueIntelligence(store).query(rid,eid)['scopus']['statuses'],['INACTIVE'])
    def test_active_absent_scimago_query(self):
        result=self.resolution(); store=self.store.venues; rid=result['run']['venue_resolution_run_id']; eid=store.list_venue_entities(rid)[0]['venue_entity_id']; self.assertEqual(VenueIntelligence(store).query(rid,eid)['scopus']['statuses'],['ACTIVE']); self.assertEqual(ScimagoVenueProjection(store).scimago_presence_status(rid,eid),'SCIMAGO_ABSENT')
    def test_upgrade_accepted_stage05_schema(self):
        from reprovane.venue_resolution import VenueResolutionStore
        with Database(self.root/'upgrade') as db:
            VenueResolutionStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5); VenueIntelligenceStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)
    def test_migration6_drift_detected(self):
        import shutil
        from reprovane.core.migrations import MIGRATION_DIR
        folder=self.root/'migrations'; shutil.copytree(MIGRATION_DIR,folder); path=folder/'0006_venue_provider_attributes.sql'; path.write_text(path.read_text()+'\n-- altered\n')
        with self.assertRaises(Exception): apply_migrations(self.db.connection,folder,target_version=6)
    def test_unknown_status_does_not_follow_discontinued(self):
        self.seed_lists(scopus=[scopus_row(**{'Active or Inactive':'','Titles Discontinued by Scopus':'2020','Coverage':'1990-2020'})]); items=self.imported(); self.assertEqual(items[0]['statistics']['unknown_status'],1); self.assertEqual(self.db.execute("SELECT status_code FROM venue_membership_observation WHERE membership_namespace='scopus'").fetchone()[0],'UNKNOWN')
    def test_structural_parse_failure_no_semantic_state(self):
        p=self.folder/'WOS/SSCI.csv'; p.write_text('bad,columns\na,b\n'); self.session.files[raw(A,'WOS/SSCI.csv')]=p.read_bytes()
        with self.assertRaises(ValidationError): self.imported()
        self.assertEqual(self.db.execute('SELECT count(*) FROM provider_snapshot').fetchone()[0],0)
    def test_no_fuzzy_or_ai_imports(self):
        import ast
        folder=Path(__file__).parents[1]/'reprovane/venue_intelligence'
        for path in folder.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node,ast.Import): self.assertFalse({n.name.split('.')[0] for n in node.names}&{'openai','rapidfuzz','fuzzywuzzy','difflib','subprocess'})
    def test_no_duplicate_attribute_semantics(self):
        self.seed_lists(scopus=[scopus_row(),scopus_row()]); self.imported(); self.assertEqual(self.db.execute("SELECT count(*) FROM venue_provider_attribute_observation WHERE attribute_namespace='scopus' AND attribute_code='ASJC'").fetchone()[0],1)
    def test_wos_taxonomy_not_asjc(self):
        self.imported(); self.assertEqual(self.db.execute("SELECT count(*) FROM venue_provider_attribute_observation WHERE attribute_namespace='wos' AND attribute_code='ASJC'").fetchone()[0],0)
    def test_snapshot_sidecar_malformed_rejected(self):
        result=self.sync_lists(); p=Path(result['snapshot_directory'])/'sync_provenance.json'; side=json.loads(p.read_text()); del side['files']; p.write_text(json.dumps(side))
        with self.assertRaises(MirrorSyncError): import_venue_lists(self.db,self.workspace,result['snapshot_directory'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM provider_snapshot').fetchone()[0],0)
    def test_provider_attribute_namespace_validation(self):
        with self.assertRaises(ValidationError): self.store.add_attribute('rec_missing',NormalizationProvenance('wos-venue-list','1','1'),'wos','ASJC',dict(original='1234',codes=['1234']),'csv:ASJC')
    def test_cli_resolve_and_query(self):
        result=self.sync_lists(); output=io.StringIO(); args=['--workspace',str(self.workspace)]
        with redirect_stdout(output): self.assertEqual(main(['import',*args,'--snapshot-directory',result['snapshot_directory']]),0)
        items=json.loads(output.getvalue()); output=io.StringIO()
        with redirect_stdout(output): self.assertEqual(main(['resolve',*args,*[arg for i in items for arg in ['--snapshot-id',i['snapshot']['snapshot_id']]]]),0)
        resolution=json.loads(output.getvalue()); rid=resolution['run']['venue_resolution_run_id']
        with Database(self.workspace) as db: entity=VenueIntelligenceStore(db).venues.list_venue_entities(rid)[0]['venue_entity_id']
        output=io.StringIO()
        with redirect_stdout(output): self.assertEqual(main(['query',*args,'--run-id',rid,'--entity-id',entity]),0)
        self.assertEqual(json.loads(output.getvalue())['wos']['collections'],['SCIE','SSCI'])
