"""Offline discovery contracts; accepted Stage01–07 tests remain independent."""
import ast,contextlib,hashlib,io,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scholarflow.core import FoundationError,ValidationError,MigrationDriftError
from scholarflow.core.db import Database
from scholarflow.core.migrations import MIGRATION_DIR
from scholarflow.discovery import DiscoveryStore
from scholarflow.discovery.expansion import FIELDS
from scholarflow.discovery.__main__ import main
from scholarflow.openalex import OpenAlexClient
from scholarflow.openalex_acquisition import OpenAlexAcquisitionStore
from openalex_acquisition_fixtures import work,page,Session,Response,SYNTHETIC_KEY


class DiscoveryCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.db=Database(self.root/'research'); self.store=DiscoveryStore(self.db)
        self.session=Session(); self.client=OpenAlexClient(SYNTHETIC_KEY,session=self.session,max_attempts=1,sleeper=lambda _:None)
        f=self.store.foundation; self.project=f.create_project('synthetic-discovery','Synthetic discovery')
        self.revision=f.create_project_revision(self.project['project_id'],1,'json','{"topic":"synthetic"}')['revision_id']; self.counter=0
        self.rid=self.store.create(self.revision)['discovery_run_id']
    def tearDown(self): self.client.close(); self.db.close(); self.temp.cleanup()
    def acquire(self,works,*,search=None,revision=None,select=None):
        self.counter+=1; self.session.responses=[Response(page(works))]
        return self.store.acquisitions.acquire(dict(search=search or 'synthetic '+str(self.counter),max_records=max(1,len(works)),page_size=min(100,max(1,len(works))),select=select or FIELDS),self.client,project_revision_id=revision or self.revision)
    def primary(self,works):
        acquisition=self.acquire(works); self.store.register_primary(self.rid,acquisition['acquisition_id']); return acquisition
    def seeds(self,depth=0,only=None):
        result=self.store.resolve_seeds(self.rid)
        return [dict(resolution_run_id=result['resolution_run_id'],publication_entity_id=r['publication_entity_id'],provider_record_id=r['provider_record_id'],depth=depth) for r in result['provider_records'] if r['publication_entity_id'] and (only is None or self.store._work(r['provider_record_id'])['record']['provider_native_id'] in only)]
    def backward(self,seeds=None,responses=(),**kwargs):
        seeds=seeds or self.seeds(); self.session.responses=list(responses)
        return self.store.expand_backward(self.rid,seeds,self.client,max_results=kwargs.pop('max_results',10),**kwargs)
    def forward(self,seeds=None,responses=(),**kwargs):
        seeds=seeds or self.seeds(); self.session.responses=list(responses)
        return self.store.expand_forward(self.rid,seeds,self.client,max_results=kwargs.pop('max_results',1),**kwargs)
    def events(self,kind): return [e for e in self.store.events(self.rid) if e['event_type']==kind]
    def edges(self): return [dict(r) for r in self.db.execute('SELECT * FROM citation_edge_observation ORDER BY observation_id')]
    def seal(self): return self.store.seal(self.rid)


class TestDiscoveryPersistence(DiscoveryCase):
    def test_schema8_startup_idempotent(self):
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],8); DiscoveryStore(self.db); self.assertEqual(self.db.execute('SELECT max(version) FROM schema_migration').fetchone()[0],8)
    def test_schema7_upgrade(self):
        with Database(self.root/'upgrade') as db:
            OpenAlexAcquisitionStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7); DiscoveryStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8)
    def test_migration_drift_rejected(self):
        copy=self.root/'migrations'; copy.mkdir()
        for f in MIGRATION_DIR.glob('*.sql'): (copy/f.name).write_bytes(f.read_bytes())
        (copy/'0008_discovery_provenance.sql').write_bytes(b'-- changed\n'+(copy/'0008_discovery_provenance.sql').read_bytes())
        with self.assertRaises(MigrationDriftError):
            with Database(self.db.workspace,migration_dir=copy,target_schema_version=8): pass
    def test_explicit_revision_required(self):
        with self.assertRaises((FoundationError,TypeError)): self.store.create(None)
        self.assertEqual(self.db.execute('SELECT count(*) FROM review_project').fetchone()[0],1)
    def test_primary_no_network_and_exact_request_provenance(self):
        a=self.acquire([work(1)]); calls=len(self.session.calls); self.store.register_primary(self.rid,a['acquisition_id'])
        e=self.events('PRIMARY_QUERY')[0]; self.assertEqual(len(self.session.calls),calls); self.assertEqual(json.loads(e['evidence_json'])['request_spec_sha256'],a['request_spec_sha256']); self.assertEqual(e['depth'],0)
    def test_primary_replay_no_event_duplication(self):
        a=self.primary([work(1)]); self.store.register_primary(self.rid,a['acquisition_id']); self.assertEqual(len(self.store.events(self.rid)),1); self.assertEqual(self.store.inspect(self.rid)['statistics']['duplicate_exact_events_suppressed'],1)
    def test_primary_wrong_revision_rejected(self):
        rev=self.store.foundation.create_project_revision(self.project['project_id'],2,'json','{}')['revision_id']; a=self.acquire([work(1)],revision=rev)
        with self.assertRaises(ValidationError): self.store.register_primary(self.rid,a['acquisition_id'])
    def test_incomplete_acquisition_rejected(self):
        a=self.store.acquisitions.create(self.revision,dict(search='unfinished'))
        with self.assertRaises(ValidationError): self.store.register_primary(self.rid,a['acquisition_id'])
    def test_empty_primary_acquisition_retained(self):
        a=self.primary([]); result=self.seal(); self.assertEqual(result['primary_acquisition_ids'],[a['acquisition_id']]); self.assertEqual(result['statistics']['canonical_discovered_publications'],0); self.assertTrue(self.store.verify(self.rid))
    def test_multiple_primary_queries_one_identity(self):
        self.primary([work(1)]); self.primary([work(1)]); result=self.seal(); self.assertEqual(len(result['events']),2); self.assertEqual(len(result['memberships']),1); self.assertEqual(result['statistics']['publications_with_multiple_routes'],1)
    def test_equal_doi_different_provider_ids_use_stage03(self):
        self.primary([work(1),work(2,doi='https://doi.org/10.1234/test1')]); self.assertEqual(len(self.seal()['memberships']),1)
    def test_ambiguous_title_not_promoted(self):
        self.primary([work(1,doi=None,title='Identical synthetic title'),work(2,doi=None,title='Identical synthetic title')]); checkpoint=self.store.resolve_seeds(self.rid)
        self.assertTrue(all(r['state']=='AMBIGUOUS' for r in checkpoint['provider_records'])); result=self.seal(); self.assertEqual(result['memberships'],[]); self.assertEqual(result['statistics']['provider_level_unresolved_discoveries'],2)
    def test_conflicting_dois_do_not_merge(self):
        self.primary([work(1,title='Identical title'),work(2,title='Identical title')]); result=self.seal(); self.assertLessEqual(len(result['memberships']),2)
        self.assertTrue(all(len(json.loads(m['membership_basis_json'])['provider_record_ids'])==1 for m in result['memberships']))
    def test_ambiguous_seed_rejected(self):
        self.primary([work(1,doi=None,title='Same title'),work(2,doi=None,title='Same title')]); result=self.store.resolve_seeds(self.rid); row=result['provider_records'][0]
        with self.assertRaises(ValidationError): self.backward([dict(resolution_run_id=result['resolution_run_id'],publication_entity_id='invented',provider_record_id=row['provider_record_id'],depth=0)])
    def test_checkpoint_idempotent(self):
        self.primary([work(1)]); self.assertEqual(self.store.resolve_seeds(self.rid),self.store.resolve_seeds(self.rid))
    def test_materialized_run_immutable(self):
        a=self.primary([work(1)]); self.store.materialize(self.rid)
        with self.assertRaises(ValidationError): self.store.register_primary(self.rid,a['acquisition_id'])
    def test_sealed_run_immutable(self):
        self.primary([work(1)]); seeds=self.seeds(); self.seal()
        with self.assertRaises(ValidationError): self.backward(seeds)
        with self.assertRaises(ValidationError): self.seal()
    def test_linked_new_run_after_seal(self):
        self.primary([work(1)]); self.seal(); child=self.store.create(self.revision,parent_run_id=self.rid); self.assertEqual(child['parent_run_id'],self.rid); self.assertEqual(child['state'],'OPEN')
    def test_parent_must_be_sealed(self):
        with self.assertRaises(ValidationError): self.store.create(self.revision,parent_run_id=self.rid)
    def test_parent_same_project(self):
        self.seal(); f=self.store.foundation; p=f.create_project('other','Other'); rev=f.create_project_revision(p['project_id'],1,'json','{}')
        with self.assertRaises(ValidationError): self.store.create(rev['revision_id'],parent_run_id=self.rid)
    def test_register_atomic_on_event_failure(self):
        a=self.acquire([work(1),work(2)]); original=self.store._event; count=[0]
        def broken(*args,**kwargs):
            count[0]+=1
            if count[0]==2: raise RuntimeError('synthetic insert fault')
            return original(*args,**kwargs)
        with patch.object(self.store,'_event',side_effect=broken),self.assertRaises(RuntimeError): self.store.register_primary(self.rid,a['acquisition_id'])
        self.assertEqual(self.store.events(self.rid),[]); self.assertEqual(self.store.operations(self.rid),[]); self.store.register_primary(self.rid,a['acquisition_id']); self.assertEqual(len(self.store.events(self.rid)),2)
    def test_materialization_atomic(self):
        self.primary([work(1)]); original=self.store.foundation._insert
        def broken(table,row):
            if table=='discovery_membership': raise RuntimeError('synthetic membership fault')
            return original(table,row)
        before=self.db.execute('SELECT count(*) FROM resolution_run').fetchone()[0]
        with patch.object(self.store.foundation,'_insert',side_effect=broken),self.assertRaises(RuntimeError): self.store.materialize(self.rid)
        self.assertEqual(self.store.get(self.rid)['state'],'OPEN'); self.assertEqual(self.store.memberships(self.rid),[]); self.assertEqual(self.db.execute('SELECT count(*) FROM resolution_run').fetchone()[0],before)
    def test_seal_failure_rolls_back_materialization(self):
        self.primary([work(1)])
        with patch.object(self.store,'verify',side_effect=RuntimeError('synthetic verification fault')),self.assertRaises(RuntimeError): self.seal()
        self.assertEqual(self.store.get(self.rid)['state'],'OPEN'); self.assertEqual(self.store.memberships(self.rid),[])
    def test_inspect_deterministic(self):
        self.primary([work(1)]); self.seal(); self.assertEqual(self.store.inspect(self.rid),self.store.inspect(self.rid)); self.assertTrue(self.store.verify(self.rid))
    def test_raw_corruption_detected(self):
        self.primary([work(1)]); self.seal(); info=self.store._work(self.store.events(self.rid)[0]['provider_record_id']); path=self.store.foundation.artifacts.path_for_hash(info['record']['artifact_sha256']); path.write_bytes(b'corrupt')
        with self.assertRaises(FoundationError): self.store.verify(self.rid)


class TestDiscoveryBackward(DiscoveryCase):
    def test_deterministic_fixture_reconciles_statistics(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/discovery/neighborhoods.json').read_text(encoding='utf-8'))
        self.primary([work(n,referenced_works=[f'https://openalex.org/W{x}' for x in fixture['references'][str(n)]]) for n in fixture['primary']]); self.backward(); stats=self.seal()['statistics']; expected=fixture['expected']
        self.assertEqual(stats['event_counts'],dict(PRIMARY_QUERY=expected['primary_events'],BACKWARD_CITATION=expected['backward_events'],FORWARD_CITATION=0)); self.assertEqual(stats['canonical_discovered_publications'],expected['memberships']); self.assertEqual(stats['unique_citation_edges'],expected['citation_edges']); self.assertEqual(stats['publications_with_multiple_routes'],expected['multi_route_publications']); self.assertEqual(stats['duplicate_exact_events_suppressed'],expected['duplicate_reference_ids'])
    def test_raw_direction_and_stage02_profile(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); self.backward(responses=[Response(page([work(2)]))]); edge=self.edges()[0]
        self.assertEqual((edge['source_identifier_value'],edge['target_identifier_value']),('https://openalex.org/W1','https://openalex.org/W2')); self.assertEqual(edge['normalizer_name'],'openalex-citation'); self.assertEqual(edge['source_field_path'],'/results/0/referenced_works/0')
        e=self.events('BACKWARD_CITATION')[0]; self.assertEqual(e['depth'],1); self.assertEqual(e['seed_depth'],0); self.assertEqual(e['metadata_state'],'ACQUIRED'); self.assertEqual(len(self.seal()['memberships']),2)
    def test_local_target_requires_no_fetch(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2']),work(2,referenced_works=[])]); seeds=self.seeds(only={'https://openalex.org/W1'}); calls=len(self.session.calls); self.backward(seeds); self.assertEqual(len(self.session.calls),calls); self.assertEqual(self.events('BACKWARD_CITATION')[0]['metadata_state'],'LOCAL')
    def test_two_seeds_primary_target_all_routes(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W3']),work(2,referenced_works=['https://openalex.org/W3']),work(3,referenced_works=[])]); self.backward(self.seeds(only={'https://openalex.org/W1','https://openalex.org/W2'})); result=self.seal()
        self.assertEqual(len(result['events']),5); self.assertEqual(len(result['memberships']),3); self.assertEqual(result['statistics']['publications_with_multiple_routes'],1); self.assertEqual(result['statistics']['unique_seeds'],2)
    def test_duplicate_reference_anchors_preserved(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2','https://openalex.org/W2'])]); self.backward(responses=[Response(page([work(2)]))]); events=self.events('BACKWARD_CITATION'); self.assertEqual(len(events),1); self.assertEqual(json.loads(events[0]['evidence_json'])['reference_indices'],[0,1]); self.assertEqual(self.store.inspect(self.rid)['statistics']['duplicate_exact_events_suppressed'],1)
    def test_exact_expansion_replay_no_http(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); seeds=self.seeds(); self.backward(seeds,[Response(page([work(2)]))]); calls=len(self.session.calls); self.backward(seeds); self.assertEqual(len(self.session.calls),calls); self.assertEqual(len(self.events('BACKWARD_CITATION')),1)
    def test_missing_target_retains_edge_not_entity(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); self.backward(responses=[Response(page([]))]); result=self.seal(); self.assertEqual(len(result['memberships']),1); self.assertEqual(result['statistics']['missing_unresolved_targets'],1); self.assertEqual(len(self.edges()),1); self.assertEqual(self.events('BACKWARD_CITATION')[0]['metadata_state'],'MISSING')
    def test_malformed_target_no_invented_edge(self):
        self.primary([work(1,referenced_works=[None,'not-a-work'])]); self.backward(); self.assertEqual(self.edges(),[]); self.assertEqual({e['metadata_state'] for e in self.events('BACKWARD_CITATION')},{'MALFORMED'}); self.assertEqual(len(self.seal()['memberships']),1)
    def test_single_merged_id_preserves_both_ids(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); self.backward(responses=[Response(page([work(3)]))]); e=self.events('BACKWARD_CITATION')[0]; self.assertEqual(e['requested_target_id'],'https://openalex.org/W2'); self.assertEqual(e['returned_work_id'],'https://openalex.org/W3'); self.assertEqual(self.edges()[0]['target_identifier_value'],'https://openalex.org/W2'); self.assertEqual(len(self.seal()['memberships']),2)
    def test_unattributable_merged_batch_no_guess(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2','https://openalex.org/W3'])]); self.backward(responses=[Response(page([work(4)]))]); events=self.events('BACKWARD_CITATION'); self.assertTrue(all(e['provider_record_id'] is None and e['metadata_state']=='UNATTRIBUTED_ID_CHANGE' for e in events)); self.assertEqual(len(self.seal()['memberships']),1)
    def test_related_works_not_citations(self):
        self.primary([work(1,referenced_works=[],related_works=['https://openalex.org/W2'])]); self.backward(); self.assertEqual(self.events('BACKWARD_CITATION'),[]); self.assertEqual(self.edges(),[])
    def test_omitted_references_supplemental_acquisition(self):
        raw=work(1); del raw['referenced_works']; a=self.primary([raw]); original=self.store.foundation.list_provider_records(a['snapshot_id'])[0]; original_hash=original['artifact_sha256']
        self.backward(responses=[Response(page([work(1,referenced_works=['https://openalex.org/W2'])])),Response(page([work(2)]))]); e=self.events('BACKWARD_CITATION')[0]; self.assertNotEqual(json.loads(e['evidence_json'])['seed_evidence_record_id'],e['seed_record_id']); self.assertEqual(self.store.foundation.list_provider_records(a['snapshot_id'])[0]['artifact_sha256'],original_hash); self.assertEqual(self.store.inspect(self.rid)['statistics']['child_acquisition_count'],2)
    def test_supplemental_changed_seed_fails_closed(self):
        raw=work(1); del raw['referenced_works']; self.primary([raw])
        with self.assertRaises(ValidationError): self.backward(responses=[Response(page([work(2)]))])
        self.assertEqual(self.events('BACKWARD_CITATION'),[])
    def test_malformed_reference_array_fails_closed(self):
        self.primary([work(1,referenced_works=None)])
        with self.assertRaises(ValidationError): self.backward()
        with self.assertRaises(ValidationError): self.seal()
    def test_100_or_boundary_and_101_split(self):
        ids=list(range(2,103)); self.primary([work(1,referenced_works=[f'https://openalex.org/W{i}' for i in ids])]); self.backward(responses=[Response(page([work(i) for i in sorted(ids,key=lambda i:f'https://openalex.org/W{i}')[:100]])),Response(page([work(sorted(ids,key=lambda i:f'https://openalex.org/W{i}')[100])]))],max_results=101,batch_size=100)
        calls=self.session.calls[1:]; self.assertEqual(len(calls),2); self.assertEqual([len(c['params']['filter'].split('|')) for c in calls],[100,1]); self.assertEqual(len(self.events('BACKWARD_CITATION')),101)
    def test_explicit_result_bound(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2','https://openalex.org/W3'])]); self.backward(responses=[Response(page([work(2)]))],max_results=1); self.assertEqual(len(self.events('BACKWARD_CITATION')),1); op=next(o for o in self.store.operations(self.rid) if o['operation_kind']=='BACKWARD_CITATION'); self.assertEqual(json.loads(op['statistics_json'])['omitted_due_to_explicit_bound'],1)
    def test_no_automatic_recursion(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); self.backward(responses=[Response(page([work(2,referenced_works=['https://openalex.org/W3'])]))]); self.assertEqual(len(self.session.calls),2); self.assertEqual(len(self.events('BACKWARD_CITATION')),1)
    def test_cycle_and_explicit_depth(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); self.backward(responses=[Response(page([work(2,referenced_works=['https://openalex.org/W1'])]))]); self.backward(self.seeds(depth=1,only={'https://openalex.org/W2'})); result=self.seal(); self.assertEqual(len(result['memberships']),2); self.assertEqual(result['statistics']['depth_distribution'],{'0':1,'1':1,'2':1}); self.assertEqual(len(self.session.calls),2)
    def test_self_loop_raw_evidence_no_new_membership(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W1'])]); self.backward(); self.assertEqual(self.events('BACKWARD_CITATION')[0]['self_loop'],1); self.assertEqual(len(self.edges()),1); self.assertEqual(len(self.seal()['memberships']),1)


class TestDiscoveryForward(DiscoveryCase):
    def test_cites_filter_and_direction(self):
        self.primary([work(1)]); self.forward(responses=[Response(page([work(2,referenced_works=['https://openalex.org/W1'])]))]); self.assertEqual(self.session.calls[-1]['params']['filter'],'cites:W1'); edge=self.edges()[0]; self.assertEqual((edge['source_identifier_value'],edge['target_identifier_value']),('https://openalex.org/W2','https://openalex.org/W1')); self.assertEqual(self.events('FORWARD_CITATION')[0]['depth'],1)
    def test_query_filter_evidence_when_array_omitted(self):
        self.primary([work(1)]); raw=work(2); del raw['referenced_works']; self.forward(responses=[Response(page([raw]))]); e=self.events('FORWARD_CITATION')[0]; self.assertEqual(json.loads(e['evidence_json'])['basis'],'CITES_FILTER'); self.assertTrue(self.edges()[0]['source_field_path'].startswith('acquisition:')); self.assertEqual(len(self.seal()['memberships']),2)
    def test_explicit_array_mismatch_atomic_fail(self):
        self.primary([work(1)])
        with self.assertRaises(ValidationError): self.forward(responses=[Response(page([work(2,referenced_works=[])]))])
        self.assertEqual(self.events('FORWARD_CITATION'),[]); self.assertEqual(self.edges(),[])
        with self.assertRaises(ValidationError): self.seal()
    def test_malformed_array_fails_closed(self):
        self.primary([work(1)])
        with self.assertRaises(ValidationError): self.forward(responses=[Response(page([work(2,referenced_works=None)]))])
    def test_cursor_delegated_and_bounded(self):
        self.primary([work(1)]); self.forward(responses=[Response(page([work(2,referenced_works=['https://openalex.org/W1'])],'next')),Response(page([work(3,referenced_works=['https://openalex.org/W1'])],'more'))],max_results=2,page_size=1)
        self.assertEqual([c['params']['cursor'] for c in self.session.calls[1:]],['*','next']); self.assertEqual(len(self.events('FORWARD_CITATION')),2)
    def test_replay_no_http(self):
        self.primary([work(1)]); seeds=self.seeds(); self.forward(seeds,[Response(page([work(2,referenced_works=['https://openalex.org/W1'])]))]); calls=len(self.session.calls); self.forward(seeds); self.assertEqual(len(self.session.calls),calls); self.assertEqual(len(self.events('FORWARD_CITATION')),1)
    def test_multiple_seeds_same_citer_routes_retained(self):
        self.primary([work(1),work(2)]); citing=work(3,referenced_works=['https://openalex.org/W1','https://openalex.org/W2']); self.forward(responses=[Response(page([citing])),Response(page([citing]))]); result=self.seal(); self.assertEqual(len(result['memberships']),3); self.assertEqual(len(self.events('FORWARD_CITATION')),2); self.assertEqual(result['statistics']['publications_with_multiple_routes'],1)
    def test_three_route_types_same_publication(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W3']),work(2,referenced_works=[]),work(3,referenced_works=['https://openalex.org/W2'])]); self.backward(self.seeds(only={'https://openalex.org/W1'})); self.forward(self.seeds(only={'https://openalex.org/W2'}),[Response(page([work(3,referenced_works=['https://openalex.org/W2'])]))]); result=self.seal(); self.assertEqual(len(result['memberships']),3); self.assertEqual(result['statistics']['publications_with_multiple_routes'],1); self.assertEqual({e['event_type'] for e in result['events']},{'PRIMARY_QUERY','BACKWARD_CITATION','FORWARD_CITATION'})
    def test_forward_self_loop(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W1'])]); self.forward(responses=[Response(page([work(1,referenced_works=['https://openalex.org/W1'])]))]); self.assertEqual(self.events('FORWARD_CITATION')[0]['self_loop'],1); self.assertEqual(len(self.seal()['memberships']),1)
    def test_empty_forward_child_receipt(self):
        self.primary([work(1)]); self.forward(responses=[Response(page([]))]); result=self.seal(); self.assertEqual(result['statistics']['child_acquisition_count'],1); self.assertEqual(self.events('FORWARD_CITATION'),[])


class TestDiscoveryFailureAndGuards(DiscoveryCase):
    def test_http_failure_child_retained_retry_same_acquisition(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); seeds=self.seeds()
        with self.assertRaises(Exception): self.backward(seeds,[Response(status=503)])
        before=self.store.inspect(self.rid)['child_acquisition_ids']; self.assertEqual(len(before),1); self.assertEqual(self.events('BACKWARD_CITATION'),[])
        with self.assertRaises(ValidationError): self.seal()
        self.backward(seeds,[Response(page([work(2)]))]); self.assertEqual(self.store.inspect(self.rid)['child_acquisition_ids'],before); self.assertEqual(len(self.seal()['memberships']),2)
    def test_partial_cursor_failure_resumes_stage07(self):
        self.primary([work(1)]); seeds=self.seeds()
        with self.assertRaises(Exception): self.forward(seeds,[Response(page([work(2,referenced_works=['https://openalex.org/W1'])],'next')),Response(status=503)],max_results=2,page_size=1)
        self.assertEqual(self.events('FORWARD_CITATION'),[]); self.forward(seeds,[Response(page([work(3,referenced_works=['https://openalex.org/W1'])]))],max_results=2,page_size=1); self.assertEqual(self.session.calls[-1]['params']['cursor'],'next'); self.assertEqual(len(self.events('FORWARD_CITATION')),2)
    def test_event_failure_rolls_back_citation_observations(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); seeds=self.seeds()
        with patch.object(self.store,'_event',side_effect=RuntimeError('synthetic route fault')),self.assertRaises(RuntimeError): self.backward(seeds,[Response(page([work(2)]))])
        self.assertEqual(self.edges(),[]); self.assertEqual(self.events('BACKWARD_CITATION'),[]); calls=len(self.session.calls); self.backward(seeds); self.assertEqual(len(self.session.calls),calls); self.assertEqual(len(self.edges()),1)
    def test_frozen_plan_retry_unaffected_by_new_local_records(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); seeds=self.seeds()
        with self.assertRaises(Exception): self.backward(seeds,[Response(status=503)])
        self.acquire([work(2)]); self.backward(seeds,[Response(page([work(2)]))]); self.assertEqual(self.events('BACKWARD_CITATION')[0]['metadata_state'],'ACQUIRED')
    def test_invalid_bounds_rejected_without_operation(self):
        self.primary([work(1)]); seeds=self.seeds(); count=len(self.store.operations(self.rid))
        for kwargs in [dict(max_results=0),dict(max_results=True),dict(batch_size=101),dict(batch_size=0)]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValidationError): self.backward(seeds,**kwargs)
        self.assertEqual(len(self.store.operations(self.rid)),count)
    def test_seed_descriptor_must_be_exact_and_resolved(self):
        self.primary([work(1)]); seed=self.seeds()[0]
        for bad in [dict(seed,depth=-1),dict(seed,depth=True),dict(seed,publication_entity_id='invented'),dict(seed,extra=True)]:
            with self.subTest(bad=bad),self.assertRaises(ValidationError): self.backward([bad])
    def test_raw_sql_updates_deletes_replaces_rejected(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W1'])]); self.backward(); self.seal()
        connection=sqlite3.connect(self.db.path); connection.create_function('discovery_authorized',2,lambda *_:0); connection.create_function('foundation_sha256_text',1,lambda s:hashlib.sha256(s.encode()).hexdigest()); connection.execute('PRAGMA recursive_triggers=OFF')
        try:
            for table in ['discovery_run','discovery_operation','discovery_event','discovery_membership','discovery_operation_acquisition','discovery_attempt']:
                columns=[r[1] for r in connection.execute('PRAGMA table_info('+table+')')]
                for sql in ['DELETE FROM '+table,'UPDATE '+table+' SET '+columns[0]+'='+columns[0],'INSERT OR REPLACE INTO '+table+' SELECT * FROM '+table]:
                    with self.subTest(sql=sql),self.assertRaises(sqlite3.DatabaseError): connection.execute(sql)
                    connection.rollback()
        finally: connection.close()
    def test_alternate_unique_replace_guard(self):
        self.primary([work(1)]); op=self.store.operations(self.rid)[0]; row=dict(op,operation_id='different-id',state='OPEN',completed_at_utc=None)
        with self.store._authorize(self.rid),self.assertRaises(FoundationError): self.db.execute('INSERT OR REPLACE INTO discovery_operation ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
    def test_no_screening_or_frontier_schema(self):
        tables=list(self.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name LIKE 'discovery_%'")); schema=' '.join(r[0].lower() for r in tables)
        for name in ['included','excluded','eligible','screening','frontier','saturation','corpus_membership']: self.assertNotIn(name,schema)
    def test_discovery_has_no_second_transport(self):
        for path in (Path(__file__).parents[1]/'scholarflow/discovery').glob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node,(ast.Import,ast.ImportFrom)):
                    names=[a.name for a in node.names]+[getattr(node,'module','') or '']; self.assertFalse(any(n.split('.')[0] in ['requests','httpx','urllib','subprocess'] for n in names))
    def test_cli_help_no_credentials(self):
        with patch('scholarflow.discovery.__main__.local_client',side_effect=AssertionError('credentials accessed')),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as error: main(['--help'])
        self.assertEqual(error.exception.code,0)
    def test_cli_create_requires_project_revision(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error: main(['create','--workspace',str(self.root/'cli')])
        self.assertEqual(error.exception.code,2)
    def test_cli_inspect_no_credentials(self):
        with patch('scholarflow.discovery.__main__.local_client',side_effect=AssertionError('credentials accessed')),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['inspect','--workspace',str(self.db.workspace),'--run-id',self.rid]),0)
        self.assertEqual(json.loads(output.getvalue())['run']['discovery_run_id'],self.rid)
