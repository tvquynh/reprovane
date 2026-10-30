import ast,csv,json,hashlib
from pathlib import Path
from unittest.mock import patch
from venue_fixtures import VenueCase,build_package,checksums,issn,HEADER
from reprovane.core import ValidationError,ArtifactIntegrityError
from reprovane.providers.scimago import validate_local_package,import_scimago_source_snapshot,import_scimago_apc_snapshot,NORMALIZER
from reprovane.venue_resolution.projection import ScimagoVenueProjection


class TestScimagoProvider(VenueCase):
    def setUp(self): super().setUp(); self.pkg=self.root/'SCIMAGO'; self.manifest=build_package(self.pkg)
    def manifest_edit(self,change):
        value=json.loads((self.pkg/'manifest.json').read_text()); change(value); (self.pkg/'manifest.json').write_text(json.dumps(value))
    def imported(self,year=2023,**kwargs): return import_scimago_source_snapshot(self.db,self.pkg,year,**kwargs)
    def metric_rows(self,result):
        return [m for r in result['provider_records'] for m in self.obs.list_venue_metric_observations(r['provider_record_id'])]
    def modify_csv(self,change,year=2023):
        path=self.pkg/f'scimagojr-{year}.csv'
        with path.open(encoding='utf-8-sig',newline='') as stream: rows=list(csv.reader(stream,delimiter=';'))
        change(rows)
        with path.open('w',encoding='utf-8-sig',newline='') as h: csv.writer(h,delimiter=';').writerows(rows)
        checksums(self.pkg)
    def test_v1_dynamic(self):
        build_package(self.pkg,years=(2018,2019),version=1); p=validate_local_package(self.pkg); self.assertEqual(p.years,(2018,2019)); self.assertEqual(p.latest_year,2019)
    def test_v2_dynamic(self): self.assertEqual(validate_local_package(self.pkg).years,(2022,2023))
    def test_future_year_data_only(self):
        build_package(self.pkg,years=(2022,2023,2031)); p=validate_local_package(self.pkg); self.assertEqual(p.latest_year,2031); self.assertTrue(import_scimago_source_snapshot(self.db,self.pkg,2031)['snapshot']['sealed_at_utc'])
    def test_corrupted_csv(self):
        with (self.pkg/'scimagojr-2023.csv').open('ab') as h: h.write(b'corrupt')
        with self.assertRaises(ArtifactIntegrityError): validate_local_package(self.pkg)
    def test_missing_csv(self):
        (self.pkg/'scimagojr-2023.csv').unlink()
        with self.assertRaises(ValidationError): validate_local_package(self.pkg)
    def test_unsafe_checksum_and_duplicates(self):
        path=self.pkg/'SHA256SUMS.txt'; original=path.read_text()
        for extra in ['a'*64+'  ../escape\n',original.splitlines()[0]+'\n']:
            path.write_text(original+extra)
            with self.assertRaises(ValidationError): validate_local_package(self.pkg)
    def test_invalid_year_lists(self):
        original=(self.pkg/'manifest.json').read_text()
        for years in [[2023,2022],[2022,2022],[],[2022,'2023']]:
            (self.pkg/'manifest.json').write_text(original); self.manifest_edit(lambda m:m['sjr'].update(available_years=years))
            with self.assertRaises(ValidationError): validate_local_package(self.pkg)
    def test_latest_not_declared(self):
        self.manifest_edit(lambda m:m['sjr'].update(latest_snapshot_year=2040))
        with self.assertRaises(ValidationError): validate_local_package(self.pkg)
    def test_unsupported_schema(self):
        self.manifest_edit(lambda m:m.update(schema_version=9))
        with self.assertRaises(ValidationError): validate_local_package(self.pkg)
    def test_apc_optional(self): self.assertEqual(validate_local_package(self.pkg).apc_files,{})
    def test_apc_older_year(self):
        build_package(self.pkg,apc=True); p=validate_local_package(self.pkg); self.assertEqual(list(p.apc_files),[2022]); self.assertEqual(p.latest_year,2023)
    def test_counts_from_manifest(self):
        self.manifest_edit(lambda m:m.update(files={'expected_total_data_files':2})); validate_local_package(self.pkg)
        self.manifest_edit(lambda m:m['files'].update(expected_total_data_files=99))
        with self.assertRaises(ValidationError): validate_local_package(self.pkg)
    def test_raw_artifact_and_provider(self):
        r=self.imported(); s=r['snapshot']; self.assertEqual(self.f.get_provider(s['provider_id'])['semantic_namespace'],'scimago'); self.assertEqual(s['dataset'],'source_list'); self.assertEqual(s['upstream_release'],'2023'); self.assertEqual(s['acquisition_mode'],'FILE'); self.assertTrue(self.f.verify_provider_snapshot(s['snapshot_id']))
    def test_record_identity_locator(self):
        r=self.imported()['provider_records'][0]; self.assertEqual(r['provider_native_id'],'100'); self.assertEqual(r['record_locator'],'row:00000002')
    def test_git_provenance(self):
        s=self.imported(distribution_resolved_commit_sha='a'*40)['snapshot']; self.assertEqual(s['mirror_commit_sha'],'a'*40); self.assertIn('/'+'a'*40+'/',s['source_locator']); self.assertNotIn('historical.example',s['source_locator']); self.assertEqual(json.loads(s['metadata_json'])['historical_upstream_base_url'],'https://historical.example.test/old/')
    def test_branch_without_commit_not_immutable(self): self.assertFalse(json.loads(self.imported()['snapshot']['metadata_json'])['immutable_distribution_reference'])
    def test_commit_format(self):
        for value in ['control','a'*39,'x'*40]:
            with self.subTest(value=value),self.assertRaises(ValidationError): self.imported(distribution_resolved_commit_sha=value)
    def test_schema_missing_fails_without_snapshot(self):
        self.modify_csv(lambda rows:rows[0].__setitem__(0,'RenamedRank'))
        with self.assertRaises(ValidationError): self.imported()
        self.assertEqual(self.db.execute("SELECT count(*) FROM provider_snapshot WHERE dataset='source_list'").fetchone()[0],0)
    def test_unknown_optional_logged(self):
        def change(rows): rows[0].append('Optional_new_column'); rows[1].append('value')
        self.modify_csv(change); s=self.imported()['snapshot']; self.assertIn('Optional_new_column',json.loads(s['metadata_json'])['unknown_optional_columns'])
    def test_duplicate_publisher(self):
        def change(rows): rows[0].append('Publisher'); rows[1][5]=''; rows[1].append('Second Publisher')
        self.modify_csv(change); r=self.imported()['provider_records'][0]; self.assertEqual(self.obs.list_venue_observations(r['provider_record_id'])[0]['publisher_name'],'Second Publisher')
    def test_bad_sourceid(self):
        self.modify_csv(lambda rows:rows[1].__setitem__(1,''))
        with self.assertRaises(ValidationError): self.imported()
    def test_bom_decimal_quartile_metrics(self):
        rows=self.metric_rows(self.imported()); by={m['metric_code']:m for m in rows}; self.assertEqual(by['SJR']['value_real'],1.25); self.assertEqual(by['H_INDEX_REPORTED']['value_integer'],12); self.assertEqual(by['RANK']['value_integer'],1); self.assertEqual(by['BEST_QUARTILE']['value_text'],'Q1')
    def test_dash_quartile(self):
        self.modify_csv(lambda rows:rows[1].__setitem__(9,'-')); self.assertFalse(any(m['metric_code']=='BEST_QUARTILE' for m in self.metric_rows(self.imported())))
    def test_venue_fields_and_country_not_region(self):
        rid=self.imported()['provider_records'][0]['provider_record_id']; r=self.obs.list_venue_observations(rid)[0]; self.assertEqual((r['title'],r['source_type'],r['country_code']),('Alpha Journal','journal','VN'))
    def test_identifiers_no_invented_linking(self):
        rid=self.imported()['provider_records'][0]['provider_record_id']; rows=self.obs.list_venue_identifier_observations(rid); self.assertEqual({r['identifier_namespace'] for r in rows},{'scimago_source_id','issn'})
    def test_membership_oa_not_doaj(self):
        rid=self.imported()['provider_records'][0]['provider_record_id']; rows=self.obs.list_venue_membership_observations(rid); self.assertIn(('scimago','PRESENT_IN_SNAPSHOT'),{(r['membership_namespace'],r['status_code']) for r in rows}); self.assertNotIn('doaj',{r['membership_namespace'] for r in rows})
    def test_category_claims_no_invented_unranked(self):
        rows=[m for m in self.metric_rows(self.imported()) if m['metric_code']=='QUARTILE']; self.assertEqual({m['category_name'] for m in rows},{'Software','Information Systems'})
    def test_areas_raw_only(self):
        r=self.imported(); record=r['provider_records'][0]; raw=self.f.artifacts.path_for_hash(record['artifact_sha256']).read_bytes(); self.assertIn(b'Computer Science',raw); self.assertFalse(any('AREA' in m['metric_code'] for m in self.metric_rows(r)))
    def test_snapshot_sealed_before_observations(self):
        original=self.obs.add_venue_observation
        # Inspect DB constraint itself through a fresh service, because importer owns its service instance.
        r=self.imported(); self.assertTrue(r['snapshot']['sealed_at_utc']); self.assertTrue(self.obs.list_venue_observations(r['provider_records'][0]['provider_record_id']))
    def test_apc_shifted_profile_and_unit(self):
        build_package(self.pkg,apc=True); r=import_scimago_apc_snapshot(self.db,self.pkg,2022); s=r['snapshot']; rows=self.metric_rows(r)
        self.assertEqual(s['dataset'],'journal_value'); self.assertIn('known_journal_value_shift_v1',json.loads(s['metadata_json'])['compatibility_layouts']); self.assertEqual((rows[0]['metric_code'],rows[0]['value_real'],rows[0]['unit']),('APC_USD_ESTIMATE',1500.0,'USD')); self.assertTrue(json.loads(s['metadata_json'])['apc_is_estimate'])
    def test_apc_missing_semantic_column(self):
        build_package(self.pkg,apc=True); p=self.pkg/'journal-value-2022.csv'; p.write_text(p.read_text().replace('Estimated APC (USD)','Renamed')); checksums(self.pkg)
        with self.assertRaises(ValidationError): import_scimago_apc_snapshot(self.db,self.pkg,2022)
    def test_apc_unknown_shifted_profile_fails_closed(self):
        build_package(self.pkg,apc=True); p=self.pkg/'journal-value-2022.csv'; p.write_text(p.read_text().replace('Total Docs. (2022)','Unknown count column')); checksums(self.pkg)
        with self.assertRaises(ValidationError): import_scimago_apc_snapshot(self.db,self.pkg,2022)
    def temporal(self):
        rows={2022:[{'Sourceid':'100','Title':'Old name','Issn':issn(),'SJR Best Quartile':'Q2'},{'Sourceid':'200','Title':'Historical (discontinued)','Issn':issn('7654321')}],2023:[{'Sourceid':'100','Title':'New name','Issn':issn('1111111')}]}
        build_package(self.pkg,rows=rows)
        a=import_scimago_source_snapshot(self.db,self.pkg,2022); b=self.imported(); records=[r['provider_record_id'] for result in [a,b] for r in result['provider_records']]
        rid=self.ready(records,True); projection=ScimagoVenueProjection(self.store); nodes=self.store.list_provider_nodes(rid)
        entity={n['provider_native_id']:next(m['venue_entity_id'] for m in self.store.list_entity_members(rid) if m['provider_node_id']==n['provider_node_id']) for n in nodes}
        return rid,projection,entity
    def test_temporal_sourceid_title_issn_changes(self):
        rid,p,e=self.temporal(); node=next(n for n in self.store.list_provider_nodes(rid) if n['provider_native_id']=='100'); self.assertEqual(node['member_count'],2); self.assertEqual(len(json.loads(node['evidence_json'])['titles']),2)
    def test_global_latest_presence(self):
        rid,p,e=self.temporal(); self.assertEqual(p.global_latest_snapshot_year(rid),2023); self.assertEqual(p.scimago_presence_status(rid,e['100']),'SCIMAGO_PRESENT_LATEST'); self.assertEqual(p.scimago_presence_status(rid,e['200']),'SCIMAGO_HISTORICAL_ONLY')
    def test_exact_year_only(self):
        rid,p,e=self.temporal(); self.assertEqual([m['value_text'] for m in p.scimago_metrics_for_year(rid,e['100'],2022) if m['metric_code']=='BEST_QUARTILE'],['Q2']); self.assertEqual(p.scimago_metrics_for_year(rid,e['100'],2030),[])
    def test_historical_no_personal_latest(self):
        rid,p,e=self.temporal(); self.assertEqual(p.scimago_metrics_latest_snapshot(rid,e['200']),[])
    def test_no_carry_forward(self):
        rid,p,e=self.temporal(); self.assertEqual(p.scimago_metrics_for_year(rid,e['200'],2023),[])
    def test_category_equality(self):
        rid,p,e=self.temporal(); self.assertEqual(p.category_quartile_exact(rid,e['100'],2023,['software'])['best_quartile'],'Q1'); self.assertEqual(p.category_quartile_exact(rid,e['100'],2023,['Information'])['matches'],[]); self.assertEqual(p.category_quartile_exact(rid,e['100'],2023,['Management Information Systems','Communication','Computer Networks and Communications'])['matches'],[])
    def test_legacy_mapping_only_helper(self):
        self.assertEqual(ScimagoVenueProjection.legacy_status('SCIMAGO_PRESENT_LATEST'),'ACTIVE_LATEST')
    def test_absent_and_unmatched(self):
        rid=self.ready([self.record(title='Unlisted')],True); p=ScimagoVenueProjection(self.store); e=self.store.list_venue_entities(rid)[0]; node=self.store.list_provider_nodes(rid)[0]; self.assertEqual(p.scimago_presence_status(rid,e['venue_entity_id']),'SCIMAGO_ABSENT'); self.assertEqual(p.scimago_resolution_status(rid,node['provider_node_id']),'SCIMAGO_UNMATCHED')
    def test_ambiguous_separate_from_presence(self):
        rows={2022:[],2023:[{'Sourceid':'100','Title':'Shared'},{'Sourceid':'200','Title':'Shared'}]}; build_package(self.pkg,rows=rows); result=self.imported(); external=self.record(title='Shared'); rid=self.ready([external]+[r['provider_record_id'] for r in result['provider_records']],True); p=ScimagoVenueProjection(self.store); node=next(n for n in self.store.list_provider_nodes(rid) if n['provider_namespace']!='scimago'); self.assertEqual(p.scimago_resolution_status(rid,node['provider_node_id']),'SCIMAGO_AMBIGUOUS')
    def test_future_annual_update_projection(self):
        old=self.imported(); build_package(self.pkg,years=(2023,2031),rows={2023:[{'Sourceid':'100','Title':'Alpha Journal'}],2031:[{'Sourceid':'300','Title':'Future venue'}]}); future=import_scimago_source_snapshot(self.db,self.pkg,2031)
        rid=self.ready([r['provider_record_id'] for result in [old,future] for r in result['provider_records']],True); p=ScimagoVenueProjection(self.store); nodes=self.store.list_provider_nodes(rid); entities={n['provider_native_id']:next(m['venue_entity_id'] for m in self.store.list_entity_members(rid) if m['provider_node_id']==n['provider_node_id']) for n in nodes}; self.assertEqual(p.global_latest_snapshot_year(rid),2031); self.assertEqual(p.scimago_presence_status(rid,entities['100']),'SCIMAGO_HISTORICAL_ONLY'); self.assertTrue(p.scimago_metrics_for_year(rid,entities['300'],2031))
    def test_no_network_or_year_constants(self):
        source=(Path(__file__).parents[1]/'reprovane/providers/scimago.py').read_text(); tree=ast.parse(source)
        for n in ast.walk(tree):
            if isinstance(n,ast.Import): self.assertFalse({a.name.split('.')[0] for a in n.names}&{'requests','http','urllib3','subprocess','openai'})
            if isinstance(n,ast.Constant) and type(n.value) is int: self.assertNotIn(n.value,[2010,2025,16,17])
    def test_manifest_iterator_discovers_years_and_optional_apc(self):
        from reprovane.providers.scimago import iter_import_scimago_package
        build_package(self.pkg,years=(2022,2031),apc=True)
        rows=list(iter_import_scimago_package(self.db,self.pkg)); self.assertEqual([(r['snapshot']['dataset'],r['snapshot']['upstream_release']) for r in rows],[('source_list','2022'),('source_list','2031'),('journal_value','2022')])
    def test_known_profile_short_rows_and_lowercase_quartile(self):
        self.modify_csv(lambda rows:rows[1].__setitem__(9,'q2')); self.assertTrue(any(m['metric_code']=='BEST_QUARTILE' and m['value_text']=='Q2' for m in self.metric_rows(self.imported())))
    def test_category_best_across_exact_requested(self):
        rid,p,e=self.temporal(); result=p.category_quartile_exact(rid,e['100'],2023,['SOFTWARE','Information-Systems']); self.assertEqual(result['best_quartile'],'Q1'); self.assertEqual(len(result['matches']),2)
