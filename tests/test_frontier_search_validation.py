"""Stage09 policy, provenance, failure and separation contracts; offline only."""
import ast,contextlib,io,json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from scholarflow.core import FoundationError,ValidationError,ArtifactIntegrityError,MigrationDriftError
from scholarflow.core.db import Database
from scholarflow.core.migrations import MIGRATION_DIR
from scholarflow.frontier import FrontierPolicy,FrontierStore
from scholarflow.search_validation import SearchValidationStore
from scholarflow.search_validation.store import sentinel
from scholarflow.frontier.__main__ import main as frontier_cli
from scholarflow.search_validation.__main__ import main as validation_cli
from scholarflow.discovery import DiscoveryStore
from test_discovery import DiscoveryCase
from openalex_acquisition_fixtures import work,page,Response


def policy(**changes):
    p=json.loads((Path(__file__).parent/'fixtures/frontier/policy.json').read_text(encoding='utf-8')); p.update(changes); return p


class Stage09Case(DiscoveryCase):
    def setUp(self):
        super().setUp(); self.frontier=FrontierStore(self.db); self.validation=SearchValidationStore(self.db); self.store=self.frontier.discovery
    def frontier_run(self,**changes): return self.frontier.create(self.revision,self.rid,policy(**changes))['frontier_run_id']
    def next(self,fid,*responses): self.session.responses=list(responses); return self.frontier.run_next(fid,self.client)
    def to_stop(self,fid,*responses): self.session.responses=list(responses); return self.frontier.run_to_policy_stop(fid,self.client)
    def validation_run(self,works=None,sentinels=(),thresholds=None,variants=None):
        if variants is None:
            a=self.acquire([work(1)] if works is None else works); variants=[dict(label='A',acquisition_id=a['acquisition_id'])]
        return self.validation.create(self.revision,variants,list(sentinels),thresholds=thresholds)['validation_run_id']
    def metrics(self,vid): return self.validation.execute(vid)['result']
    def one_metric(self,vid): return self.metrics(vid)['variants'][0]


class TestFrontierPolicy(unittest.TestCase):
    def test_canonicalization(self):
        a=policy(directions=['FORWARD_CITATION','BACKWARD_CITATION']); b=dict(reversed(list(a.items()))); b['directions']=['BACKWARD_CITATION','FORWARD_CITATION','BACKWARD_CITATION']; self.assertEqual(FrontierPolicy.from_value(a),FrontierPolicy.from_value(b))
    def test_changed_depth_changes_hash(self): self.assertNotEqual(FrontierPolicy.from_value(policy()).sha256,FrontierPolicy.from_value(policy(max_depth=3)).sha256)
    def test_changed_direction_changes_hash(self): self.assertNotEqual(FrontierPolicy.from_value(policy()).sha256,FrontierPolicy.from_value(policy(directions=['FORWARD_CITATION'])).sha256)
    def test_changed_budgets_change_hash(self):
        for key in ['global_new_work_budget','global_child_acquisition_budget']: self.assertNotEqual(FrontierPolicy.from_value(policy()).sha256,FrontierPolicy.from_value(policy(**{key:21})).sha256)
    def test_all_hard_bounds_required(self):
        for key in ['max_depth','max_rounds','max_seeds_per_round','max_results_per_seed','global_new_work_budget','global_child_acquisition_budget']:
            p=policy(); del p[key]
            with self.subTest(key=key),self.assertRaises(ValidationError): FrontierPolicy.from_value(p)
    def test_invalid_unlimited_values(self):
        for value in [None,0,-1,True,float('inf'),'unlimited']:
            with self.subTest(value=value),self.assertRaises(ValidationError): FrontierPolicy.from_value(policy(max_rounds=value))
    def test_accepted_stage08_caps(self):
        for key,value in [('batch_or_page_size',101),('max_results_per_seed',10001),('max_seeds_per_round',101),('max_depth',10001)]:
            with self.subTest(key=key),self.assertRaises(ValidationError): FrontierPolicy.from_value(policy(**{key:value}))
    def test_structural_order_only(self):
        with self.assertRaises(ValidationError): FrontierPolicy.from_value(policy(ordering='AI_RELEVANCE'))
    def test_explicit_saturation_required(self):
        with self.assertRaises(ValidationError): FrontierPolicy.from_value(policy(saturation={}))
    def test_worst_case_computable(self):
        w=FrontierPolicy.from_value(policy()).worst_case(); self.assertEqual(w['actions'],3); self.assertEqual(w['returned_work_upper_bound'],6)


class TestFrontierSelection(Stage09Case):
    def test_schema9_startup(self):
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],9); FrontierStore(self.db); self.assertEqual(self.db.execute('SELECT max(version) FROM schema_migration').fetchone()[0],9)
    def test_schema8_upgrade(self):
        with Database(self.root/'upgrade') as db:
            DiscoveryStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8); FrontierStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9)
    def test_schema_drift(self):
        folder=self.root/'migrations'; folder.mkdir()
        for p in MIGRATION_DIR.glob('*.sql'): (folder/p.name).write_bytes(p.read_bytes())
        f=folder/'0009_frontier_search_validation.sql'; f.write_bytes(b'-- drift\n'+f.read_bytes())
        with self.assertRaises(MigrationDriftError):
            with Database(self.db.workspace,migration_dir=folder,target_schema_version=9): pass
    def test_discovery_must_be_open(self):
        self.seal()
        with self.assertRaises(ValidationError): self.frontier_run()
    def test_revision_mismatch(self):
        revision=self.store.foundation.create_project_revision(self.project['project_id'],2,'json','{}')['revision_id']
        with self.assertRaises(ValidationError): self.frontier.create(revision,self.rid,policy())
    def test_one_owner_per_discovery(self):
        self.frontier_run()
        with self.assertRaises(FoundationError): self.frontier_run()
    def test_policy_resume_same(self):
        self.primary([work(1,referenced_works=[])]); fid=self.frontier_run(); self.frontier.plan_next(fid,policy=policy(),discovery_run_id=self.rid)
    def test_policy_resume_mismatch(self):
        fid=self.frontier_run()
        with self.assertRaises(ValidationError): self.frontier.plan_next(fid,policy=policy(max_depth=3))
    def test_discovery_resume_mismatch(self):
        fid=self.frontier_run()
        with self.assertRaises(ValidationError): self.frontier.plan_next(fid,discovery_run_id='different')
    def test_frozen_plan_no_network(self):
        self.primary([work(1)]); fid=self.frontier_run(); calls=len(self.session.calls); self.frontier.plan_next(fid); before=self.frontier.rounds(fid)[0]; self.frontier.plan_next(fid); self.assertEqual(before,self.frontier.rounds(fid)[0]); self.assertEqual(len(self.session.calls),calls); self.assertIn('expected_operation_spec',json.loads(before['plan_json'])['actions'][0])
    def test_round_cap_records_all_candidates(self):
        self.primary([work(1),work(2),work(3)]); fid=self.frontier_run(); self.frontier.plan_next(fid); plan=json.loads(self.frontier.rounds(fid)[0]['plan_json']); self.assertEqual(plan['candidate_count'],3); self.assertEqual(plan['selected_seed_count'],1); self.assertEqual(sum(c['directions']['BACKWARD_CITATION']=='ROUND_SEED_LIMIT' for c in plan['candidates']),2)
    def test_stable_event_tiebreak(self):
        self.primary([work(2),work(1)]); fid=self.frontier_run(); self.frontier.plan_next(fid); plan=json.loads(self.frontier.rounds(fid)[0]['plan_json']); self.assertEqual([c['ordering_basis'] for c in plan['candidates']],sorted(c['ordering_basis'] for c in plan['candidates']))
    def test_multi_route_deduplicates_seed(self):
        self.primary([work(1)]); self.primary([work(1)]); fid=self.frontier_run(); self.frontier.plan_next(fid); p=json.loads(self.frontier.rounds(fid)[0]['plan_json']); self.assertEqual(p['candidate_count'],1); self.assertEqual(len(p['actions']),1)
    def test_component_provider_tiebreak(self):
        self.primary([work(2,doi='https://doi.org/10.1234/test1'),work(1)]); fid=self.frontier_run(); self.frontier.plan_next(fid); p=json.loads(self.frontier.rounds(fid)[0]['plan_json']); self.assertEqual(p['candidates'][0]['work_id'],'https://openalex.org/W1')
    def test_ambiguous_not_selected(self):
        self.primary([work(1,doi=None,title='Same title'),work(2,doi=None,title='Same title')]); fid=self.frontier_run(); result=self.to_stop(fid); p=json.loads(result['rounds'][0]['plan_json']); self.assertEqual(p['actions'],[]); self.assertEqual(len(p['unresolved']),2); self.assertEqual(result['run']['stop_reason'],'NO_ELIGIBLE_FRONTIER')
    def test_direction_disabled_recorded(self):
        self.primary([work(1)]); fid=self.frontier_run(); self.frontier.plan_next(fid); p=json.loads(self.frontier.rounds(fid)[0]['plan_json']); self.assertEqual(p['candidates'][0]['directions']['FORWARD_CITATION'],'DIRECTION_DISABLED')
    def test_outside_discovery_mutation_rejected(self):
        self.primary([work(1)]); fid=self.frontier_run(); self.primary([work(2)])
        with self.assertRaises(ValidationError): self.frontier.plan_next(fid)


class TestFrontierRounds(Stage09Case):
    def test_other_direction_remains_available(self):
        self.primary([work(1,referenced_works=[])]); fid=self.frontier_run(directions=['BACKWARD_CITATION','FORWARD_CITATION'],global_child_acquisition_budget=1); self.next(fid); self.frontier.plan_next(fid); plan=json.loads(self.frontier.rounds(fid)[-1]['plan_json']); self.assertEqual([a['direction'] for a in plan['actions']],['FORWARD_CITATION']); self.next(fid,Response(page([])))
    def test_component_minimum_work_changes_without_reexpansion(self):
        self.primary([work(9,referenced_works=['https://openalex.org/W1'])]); fid=self.frontier_run(); self.next(fid,Response(page([work(1,doi='https://doi.org/10.1234/test9')]))); self.frontier.plan_next(fid); plan=json.loads(self.frontier.rounds(fid)[-1]['plan_json']); self.assertEqual(plan['candidates'][0]['work_id'],'https://openalex.org/W1'); self.assertEqual(plan['actions'],[])
    def test_saturation_streak_resets_after_new_yield(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W98']),work(2,referenced_works=['https://openalex.org/W99'])]); fid=self.frontier_run(saturation=dict(minimum_new_items=0,consecutive_rounds=2)); self.next(fid,Response(page([]))); self.frontier.plan_next(fid); plan=json.loads(self.frontier.rounds(fid)[-1]['plan_json']); info=self.store._work(plan['actions'][0]['seed']['provider_record_id']); target=int(info['raw']['referenced_works'][0].rsplit('W',1)[-1]); result=self.next(fid,Response(page([work(target,referenced_works=[])]))); self.assertEqual(result['saturation_history'],[1,0])
    def test_unassigned_child_ids_still_consume_work_budget(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W98','https://openalex.org/W99'])]); fid=self.frontier_run(max_results_per_seed=2,global_new_work_budget=2); result=self.next(fid,Response(page([work(7),work(8)]))); self.assertEqual(result['usage']['new_work_ids'],['https://openalex.org/W7','https://openalex.org/W8']); self.assertEqual(result['run']['stop_reason'],'GLOBAL_NEW_WORK_BUDGET'); self.assertTrue(all(e['provider_record_id'] is None for e in self.events('BACKWARD_CITATION')))
    def test_backward_round_links_stage08_stage07(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); fid=self.frontier_run(); result=self.next(fid,Response(page([work(2)]))); self.assertEqual(len(result['actions']),1); receipt=json.loads(result['actions'][0]['receipt_json']); self.assertEqual(len(receipt['child_acquisition_ids']),1); self.assertEqual(self.frontier.one('discovery_operation','operation_id',receipt['operation_id'])['state'],'COMPLETE')
    def test_forward_only(self):
        self.primary([work(1)]); fid=self.frontier_run(directions=['FORWARD_CITATION']); self.next(fid,Response(page([work(2,referenced_works=['https://openalex.org/W1'])]))); self.assertEqual(self.frontier.actions(fid)[0]['operation_id'],self.events('FORWARD_CITATION')[0]['operation_id'])
    def test_both_direction_order(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); fid=self.frontier_run(directions=['FORWARD_CITATION','BACKWARD_CITATION']); self.next(fid,Response(page([work(2)])),Response(page([work(3,referenced_works=['https://openalex.org/W1'])]))); self.assertEqual([json.loads(a['receipt_json'])['direction'] for a in self.frontier.actions(fid)],['BACKWARD_CITATION','FORWARD_CITATION'])
    def test_depth_zero_one_two_and_stop(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); fid=self.frontier_run(); result=self.to_stop(fid,Response(page([work(2,referenced_works=['https://openalex.org/W3'])])),Response(page([work(3,referenced_works=[])]))); self.assertEqual(result['run']['stop_reason'],'MAX_DEPTH'); self.assertEqual(sorted(e['depth'] for e in self.store.events(self.rid)),[0,1,2])
    def test_lowest_depth_first(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W3']),work(2,referenced_works=[])]); fid=self.frontier_run(); self.frontier.plan_next(fid); p=json.loads(self.frontier.rounds(fid)[0]['plan_json']); seed_id=p['actions'][0]['seed']['provider_record_id']; seed=self.store._work(seed_id)['record']['provider_native_id']; responses=[Response(page([work(3,referenced_works=[])]))] if seed.endswith('/W1') else []
        self.next(fid,*responses); self.frontier.plan_next(fid); p=json.loads(self.frontier.rounds(fid)[-1]['plan_json']); self.assertEqual(p['actions'][0]['seed']['depth'],0)
    def test_already_expanded_direction_skipped(self):
        self.primary([work(1,referenced_works=[])]); fid=self.frontier_run(); self.next(fid); self.frontier.plan_next(fid); p=json.loads(self.frontier.rounds(fid)[-1]['plan_json']); self.assertEqual(p['candidates'][0]['directions']['BACKWARD_CITATION'],'ALREADY_EXPANDED')
    def test_max_rounds(self):
        self.primary([work(1,referenced_works=[])]); fid=self.frontier_run(max_rounds=1); self.assertEqual(self.next(fid)['run']['stop_reason'],'MAX_ROUNDS')
    def test_max_depth(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); fid=self.frontier_run(max_depth=1); result=self.to_stop(fid,Response(page([work(2)]))); self.assertEqual(result['run']['stop_reason'],'MAX_DEPTH'); self.assertEqual(len(self.session.calls),2)
    def test_empty_frontier(self):
        fid=self.frontier_run(); self.assertEqual(self.to_stop(fid)['run']['stop_reason'],'NO_ELIGIBLE_FRONTIER')
    def test_work_budget_clipping(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2','https://openalex.org/W3'])]); fid=self.frontier_run(global_new_work_budget=1,max_results_per_seed=5); self.frontier.plan_next(fid); p=json.loads(self.frontier.rounds(fid)[0]['plan_json']); self.assertEqual(p['actions'][0]['requested_limit'],5); self.assertEqual(p['actions'][0]['effective_limit'],1); result=self.next(fid,Response(page([work(2)]))); self.assertEqual(result['run']['stop_reason'],'GLOBAL_NEW_WORK_BUDGET'); self.assertEqual(len(result['usage']['new_work_ids']),1)
    def test_child_budget_stop(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); fid=self.frontier_run(global_child_acquisition_budget=1); result=self.next(fid,Response(page([work(2)]))); self.assertEqual(result['run']['stop_reason'],'GLOBAL_CHILD_ACQUISITION_BUDGET'); self.assertEqual(len(result['usage']['child_acquisition_ids']),1)
    def test_supplemental_budget_reserved(self):
        raw=work(1); del raw['referenced_works']; self.primary([raw]); fid=self.frontier_run(global_child_acquisition_budget=1); result=self.to_stop(fid); self.assertEqual(result['run']['stop_reason'],'GLOBAL_CHILD_ACQUISITION_BUDGET'); self.assertEqual(len(self.session.calls),1)
    def test_supplemental_work_budget_reserved(self):
        raw=work(1); del raw['referenced_works']; self.primary([raw]); fid=self.frontier_run(global_new_work_budget=1); self.assertEqual(self.to_stop(fid)['run']['stop_reason'],'GLOBAL_NEW_WORK_BUDGET')
    def test_zero_new_structural_saturation(self):
        self.primary([work(1,referenced_works=[])]); fid=self.frontier_run(saturation=dict(minimum_new_items=0,consecutive_rounds=1)); result=self.next(fid); self.assertEqual(result['run']['stop_reason'],'STRUCTURAL_SATURATION'); self.assertEqual(result['saturation_history'],[1])
    def test_positive_yield_resets_streak(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); fid=self.frontier_run(saturation=dict(minimum_new_items=0,consecutive_rounds=2)); result=self.next(fid,Response(page([work(2,referenced_works=[])]))); self.assertEqual(result['saturation_history'],[0])
    def test_consecutive_saturation(self):
        self.primary([work(1,referenced_works=[]),work(2,referenced_works=[])]); fid=self.frontier_run(saturation=dict(minimum_new_items=0,consecutive_rounds=2)); result=self.to_stop(fid); self.assertEqual(result['run']['stop_reason'],'STRUCTURAL_SATURATION'); self.assertEqual(result['saturation_history'],[1,2])
    def test_hard_cap_precedes_saturation(self):
        self.primary([work(1,referenced_works=[])]); fid=self.frontier_run(max_rounds=1,saturation=dict(minimum_new_items=0,consecutive_rounds=1)); self.assertEqual(self.next(fid)['run']['stop_reason'],'MAX_ROUNDS')
    def test_stable_ids_not_entity_count(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); fid=self.frontier_run(); result=self.next(fid,Response(page([work(2,doi='https://doi.org/10.1234/test1')]))); rr=json.loads(result['rounds'][0]['result_json']); self.assertEqual(rr['new_work_ids'],['https://openalex.org/W2']); self.assertEqual(rr['safe_resolved_provider_records'],2); self.frontier.plan_next(fid); plan=json.loads(self.frontier.rounds(fid)[-1]['plan_json']); self.assertEqual(plan['actions'],[])
    def test_cycle_terminates(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); fid=self.frontier_run(max_depth=3,max_rounds=5); result=self.to_stop(fid,Response(page([work(2,referenced_works=['https://openalex.org/W1'])]))); self.assertEqual(result['run']['stop_reason'],'NO_ELIGIBLE_FRONTIER'); self.assertEqual(len(result['usage']['new_work_ids']),1)
    def test_finalize_and_http_free_replay(self):
        self.primary([work(1,referenced_works=[])]); fid=self.frontier_run(max_rounds=1); self.to_stop(fid); self.frontier.finalize(fid); self.assertTrue(self.frontier.verify(fid)); calls=len(self.session.calls); self.frontier.run_to_policy_stop(fid,self.client); self.frontier.finalize(fid); self.assertEqual(len(self.session.calls),calls); self.assertEqual(self.store.get(self.rid)['state'],'SEALED')


class TestFrontierFailure(Stage09Case):
    def test_failure_before_expansion_retry(self):
        self.primary([work(1,referenced_works=[])]); fid=self.frontier_run()
        with patch.object(self.store,'expand_backward',side_effect=RuntimeError('synthetic')),self.assertRaises(RuntimeError): self.next(fid)
        frozen=self.frontier.rounds(fid)[0]['plan_json']; self.assertEqual(self.frontier.get(fid)['state'],'FAILED'); self.next(fid); self.assertEqual(self.frontier.rounds(fid)[0]['plan_json'],frozen)
    def test_failure_after_first_direction_no_replay_http(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); fid=self.frontier_run(directions=['BACKWARD_CITATION','FORWARD_CITATION'])
        with self.assertRaises(Exception): self.next(fid,Response(page([work(2)])),Response(status=503))
        self.assertEqual(len(self.frontier.actions(fid)),1); frozen=self.frontier.rounds(fid)[0]['plan_json']; self.next(fid,Response(page([work(3,referenced_works=['https://openalex.org/W1'])]))); self.assertEqual(len(self.frontier.actions(fid)),2); self.assertEqual(len(self.session.calls),4); self.assertEqual(self.frontier.rounds(fid)[0]['plan_json'],frozen)
    def test_http_failure_frozen_plan_unrelated_local_data(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W2'])]); fid=self.frontier_run()
        with self.assertRaises(Exception): self.next(fid,Response(status=503))
        before=self.frontier.rounds(fid)[0]['plan_json']; self.acquire([work(3)]); self.next(fid,Response(page([work(2)]))); self.assertEqual(self.frontier.rounds(fid)[0]['plan_json'],before)
    def test_finalization_blocked_by_pending_round(self):
        self.primary([work(1)]); fid=self.frontier_run(); self.frontier.plan_next(fid)
        with self.assertRaises(ValidationError): self.frontier.finalize(fid)
    def test_finalization_discovery_failure_atomic(self):
        self.primary([work(1,referenced_works=[])]); fid=self.frontier_run(max_rounds=1); self.to_stop(fid)
        with patch.object(self.store,'seal',side_effect=RuntimeError('synthetic')),self.assertRaises(RuntimeError): self.frontier.finalize(fid)
        self.assertEqual(self.frontier.get(fid)['state'],'STOPPED'); self.assertEqual(self.store.get(self.rid)['state'],'OPEN'); self.frontier.finalize(fid)
    def test_completed_round_immutable(self):
        self.primary([work(1,referenced_works=[])]); fid=self.frontier_run(); self.next(fid)
        with self.frontier.authorized('F',fid),self.assertRaises(FoundationError): self.db.execute("UPDATE frontier_round SET state='FAILED' WHERE frontier_run_id=?",(fid,))
    def test_frozen_policy_immutable(self):
        fid=self.frontier_run()
        with self.frontier.authorized('F',fid),self.assertRaises(FoundationError): self.db.execute("UPDATE frontier_run SET policy_json='{}' WHERE frontier_run_id=?",(fid,))


class TestSearchSentinels(Stage09Case):
    def test_exact_openalex_found_missing(self):
        vid=self.validation_run(sentinels=[dict(namespace='openalex_work_id',value='W1'),dict(namespace='openalex_work_id',value='W2')]); m=self.one_metric(vid); self.assertEqual((m['known_item_retrieved'],m['known_item_missing'],m['known_item_recall']),(1,1,.5))
    def test_exact_normalized_doi_found(self):
        vid=self.validation_run(sentinels=[dict(namespace='doi',value='DOI:10.1234/TEST1')]); self.assertEqual(self.one_metric(vid)['known_item_recall'],1)
    def test_doi_missing_no_title_fallback(self):
        vid=self.validation_run(sentinels=[dict(namespace='doi',value='10.1234/not-found')]); self.assertEqual(self.one_metric(vid)['known_item_missing'],1)
    def test_malformed_sentinel_rejected(self):
        for raw in [dict(namespace='doi',value='bad'),dict(namespace='openalex_work_id',value='bad'),dict(namespace='title',value='Synthetic Work 1')]:
            with self.subTest(raw=raw),self.assertRaises(ValidationError): sentinel(raw)
    def test_duplicate_sentinel_idempotent(self):
        item=dict(namespace='openalex_work_id',value='W1'); a=self.acquire([work(1)]); variants=[dict(label='A',acquisition_id=a['acquisition_id'])]; first=self.validation.create(self.revision,variants,[item]); second=self.validation.create(self.revision,variants,[item,item]); self.assertEqual(first['plan_sha256'],second['plan_sha256']); self.assertEqual(len(self.validation.sentinels(second['validation_run_id'])),1)
    def test_merged_id_preserves_requested_returned_unresolved(self):
        self.session.responses=[Response(page([work(2)]))]; a=self.store.acquisitions.acquire(dict(filters={'ids.openalex':'W1'},max_records=1),self.client,project_revision_id=self.revision); vid=self.validation_run(variants=[dict(label='A',acquisition_id=a['acquisition_id'])],sentinels=[dict(namespace='openalex_work_id',value='W1')]); m=self.one_metric(vid); self.assertEqual(m['known_item_unresolved'],1); self.assertEqual(m['resolvable_expected'],0); self.assertIsNone(m['known_item_recall']); self.assertEqual(m['sentinels'][0]['requested_identifier'],'https://openalex.org/W1'); self.assertEqual(m['sentinels'][0]['observed_id_mapping'][0]['returned_work_id'],'https://openalex.org/W2')
    def test_hard_conflicting_evidence_visible(self):
        a=self.acquire([work(1)]); b=self.acquire([work(1,doi='https://doi.org/10.1234/different')]); vid=self.validation_run(variants=[dict(label='A',acquisition_id=a['acquisition_id']),dict(label='B',acquisition_id=b['acquisition_id'])],sentinels=[dict(namespace='doi',value='10.1234/test1')]); self.assertEqual(self.metrics(vid)['variants'][0]['known_item_unresolved'],1)
    def test_empty_sentinels_no_invented_recall(self): self.assertIsNone(self.one_metric(self.validation_run())['known_item_recall'])
    def test_private_note_rejected(self):
        with self.assertRaises(ValidationError): sentinel(dict(namespace='doi',value='10.1234/test1',source_note='private@example.invalid'))
    def test_fixture_metrics(self):
        items=json.loads((Path(__file__).parent/'fixtures/search_validation/sentinels.json').read_text(encoding='utf-8')); m=self.one_metric(self.validation_run([work(1),work(2)],items)); self.assertEqual(m['known_item_retrieved'],2); self.assertEqual(m['known_item_missing'],1)


class TestSearchVariants(Stage09Case):
    def pair(self,left,right,**kw):
        a=self.acquire(left); b=self.acquire(right); vid=self.validation_run(variants=[dict(label='A',acquisition_id=a['acquisition_id']),dict(label='B',acquisition_id=b['acquisition_id'])],**kw); return self.metrics(vid)
    def test_one_variant(self): self.assertEqual(len(self.metrics(self.validation_run())['variants']),1)
    def test_identical_sets(self): self.assertEqual(self.pair([work(1)],[work(1)])['pairwise'][0]['jaccard'],1)
    def test_partial_overlap_jaccard_exclusive(self):
        m=self.pair([work(1),work(2)],[work(2),work(3)]); p=m['pairwise'][0]; self.assertEqual((p['intersection'],p['union'],p['jaccard']),(1,3,1/3)); self.assertEqual([v['exclusive_count'] for v in m['variants']],[1,1])
    def test_disjoint_sets(self): self.assertEqual(self.pair([work(1)],[work(2)])['pairwise'][0]['jaccard'],0)
    def test_empty_union_jaccard(self): self.assertEqual(self.pair([],[])['pairwise'][0]['jaccard'],1)
    def test_three_variants_pairwise(self):
        variants=[dict(label=str(i),acquisition_id=self.acquire([work(i)])['acquisition_id']) for i in range(1,4)]; m=self.metrics(self.validation_run(variants=variants)); self.assertEqual(len(m['pairwise']),3)
    def test_record_limit_bounded_flag(self):
        self.session.responses=[Response(page([work(1)],'more'))]; a=self.store.acquisitions.acquire(dict(search='bounded',max_records=1),self.client,project_revision_id=self.revision); vid=self.validation_run(variants=[dict(label='A',acquisition_id=a['acquisition_id'])]); m=self.one_metric(vid); self.assertEqual(m['termination'],'RECORD_LIMIT'); self.assertTrue(m['bounded']); self.assertFalse(m['query_exhausted'])
    def test_natural_query_exhaustion(self):
        m=self.one_metric(self.validation_run()); self.assertTrue(m['query_exhausted']); self.assertEqual(m['completeness'],'QUERY_EXHAUSTED')
    def test_unequal_limits_and_sorts_visible(self):
        variants=[]
        for i,sort in [(1,'publication_date:asc'),(2,'publication_date:desc')]:
            self.session.responses=[Response(page([work(1)]))]; a=self.store.acquisitions.acquire(dict(search='literal',sort=sort,max_records=i),self.client,project_revision_id=self.revision); variants.append(dict(label=str(i),acquisition_id=a['acquisition_id']))
        p=self.metrics(self.validation_run(variants=variants))['pairwise'][0]; self.assertIn('max_records',p['changed_request_fields']); self.assertIn('sort',p['changed_request_fields']); self.assertFalse(p['query_terms_differ'])
    def test_reuse_acquisition_no_http(self):
        vid=self.validation_run(); calls=len(self.session.calls); self.validation.execute(vid); self.validation.execute(vid); self.assertEqual(len(self.session.calls),calls)
    def test_new_explicit_variant_stage07_only_no_discovery(self):
        vid=self.validation_run(variants=[dict(label='A',source='PROTOCOL',spec=dict(search='literal protocol AND "phrase"',max_records=1))]); self.session.responses=[Response(page([work(1)]))]; self.validation.execute(vid,self.client); self.assertEqual(self.store.events(self.rid),[]); a=self.validation.variants(vid)[0]; self.assertEqual(self.store.acquisitions.get(a['acquisition_id'])['state'],'COMPLETE'); self.assertEqual(self.session.calls[-1]['params']['search'],'literal protocol AND "phrase"')
    def test_cross_revision_rejected(self):
        rev=self.store.foundation.create_project_revision(self.project['project_id'],2,'json','{}')['revision_id']; a=self.acquire([work(1)],revision=rev)
        with self.assertRaises(ValidationError): self.validation_run(variants=[dict(label='A',acquisition_id=a['acquisition_id'])])
    def test_incomplete_reuse_rejected(self):
        a=self.store.acquisitions.create(self.revision,dict(search='unfinished'))
        with self.assertRaises(ValidationError): self.validation_run(variants=[dict(label='A',acquisition_id=a['acquisition_id'])])
    def test_semantic_new_request_rejected(self):
        with self.assertRaises(ValidationError): self.validation_run(variants=[dict(label='A',spec=dict(filters='search.semantic:topic'))])
    def test_explicit_promotion_separate_and_record_unchanged(self):
        vid=self.validation_run(); self.validation.execute(vid); self.validation.finalize(vid); before=self.validation.inspect(vid); aid=self.validation.variants(vid)[0]['acquisition_id']; self.store.register_primary(self.rid,aid); self.assertEqual(before,self.validation.inspect(vid)); self.assertEqual(len(self.store.events(self.rid)),1)


class TestSearchThresholdsAndFailure(Stage09Case):
    def test_explicit_threshold_met(self):
        item=dict(namespace='openalex_work_id',value='W1'); m=self.one_metric(self.validation_run(sentinels=[item],thresholds=dict(minimum_known_item_recall=1,maximum_unresolved=0,required_sentinels=[item]))); self.assertEqual(m['threshold_status'],'VALIDATION_THRESHOLD_MET')
    def test_explicit_threshold_not_met(self):
        m=self.one_metric(self.validation_run(sentinels=[dict(namespace='openalex_work_id',value='W99')],thresholds=dict(minimum_known_item_recall=1))); self.assertEqual(m['threshold_status'],'VALIDATION_THRESHOLD_NOT_MET')
    def test_no_threshold_informational(self): self.assertEqual(self.one_metric(self.validation_run())['threshold_status'],'INFORMATIONAL_ONLY')
    def test_threshold_changes_plan_identity(self):
        a=self.acquire([work(1)]); v=[dict(label='A',acquisition_id=a['acquisition_id'])]; x=self.validation.create(self.revision,v,thresholds=dict(minimum_known_item_recall=.5)); y=self.validation.create(self.revision,v,thresholds=dict(minimum_known_item_recall=1)); self.assertNotEqual(x['plan_sha256'],y['plan_sha256'])
    def test_invalid_threshold(self):
        for threshold in [dict(minimum_known_item_recall=float('nan')),dict(minimum_known_item_recall=True),dict(maximum_unresolved=-1)]:
            with self.subTest(threshold=threshold),self.assertRaises(ValidationError): self.validation_run(thresholds=threshold)
    def test_validation_acquisition_failure_retry(self):
        vid=self.validation_run(variants=[dict(label='A',spec=dict(search='explicit',max_records=1))]); self.session.responses=[Response(status=503)]
        with self.assertRaises(Exception): self.validation.execute(vid,self.client)
        self.assertEqual(self.validation.get(vid)['state'],'FAILED'); self.assertIsNone(self.validation.result(vid)); self.session.responses=[Response(page([work(1)]))]; self.validation.execute(vid,self.client); self.assertEqual(self.validation.get(vid)['state'],'COMPLETE')
    def test_result_registration_failure_atomic(self):
        vid=self.validation_run(); original=self.validation.insert
        def broken(table,row):
            if table=='search_validation_result': raise RuntimeError('synthetic')
            return original(table,row)
        with patch.object(self.validation,'insert',side_effect=broken),self.assertRaises(RuntimeError): self.validation.execute(vid)
        self.assertIsNone(self.validation.result(vid)); self.assertEqual(self.validation.get(vid)['state'],'FAILED'); self.validation.execute(vid)
    def test_plan_resume_mismatch(self):
        vid=self.validation_run()
        with self.assertRaises(ValidationError): self.validation.execute(vid,plan_sha256='wrong')
    def test_finalize_pending_blocked(self):
        vid=self.validation_run()
        with self.assertRaises(ValidationError): self.validation.finalize(vid)
    def test_final_manifest_verify_and_replay(self):
        vid=self.validation_run(); self.validation.execute(vid); before=self.validation.finalize(vid); self.assertTrue(self.validation.verify(vid)); self.assertEqual(before,self.validation.finalize(vid)); self.assertEqual(before,self.validation.execute(vid))


class TestStage09Separation(Stage09Case):
    def test_tampered_final_frontier_round_detected(self):
        self.primary([work(1,referenced_works=[])]); fid=self.frontier_run(max_rounds=1); self.to_stop(fid); self.frontier.finalize(fid); row=self.frontier.rounds(fid)[0]; result=json.loads(row['result_json']); result['selected_seed_count']=99; self.db.connection.execute('DROP TRIGGER frontier_round_update'); self.db.connection.execute('UPDATE frontier_round SET result_json=? WHERE frontier_run_id=?',(json.dumps(result),fid))
        with self.assertRaises(ArtifactIntegrityError): self.frontier.verify(fid)
    def test_no_network_identity_or_screening_engine_imports(self):
        root=Path(__file__).parents[1]/'scholarflow'
        for path in [root/'stage09.py',*(root/'frontier').glob('*.py'),*(root/'search_validation').glob('*.py')]:
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node,(ast.Import,ast.ImportFrom)):
                    names=[a.name for a in node.names]+[getattr(node,'module','') or '']; self.assertFalse(any(n.split('.')[0] in ['requests','httpx','urllib','openai','transformers','sentence_transformers','subprocess','fulltext','unpaywall'] for n in names))
    def test_no_screening_schema(self):
        schema=' '.join(r[0].lower() for r in self.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND (name LIKE 'frontier_%' OR name LIKE 'search_validation_%')"))
        for name in ['included','excluded','eligibility','reviewer_vote','corpus_membership','relevance_score']: self.assertNotIn(name,schema)
    def test_help_no_credentials(self):
        for cli,module in [(frontier_cli,'scholarflow.frontier.__main__'),(validation_cli,'scholarflow.search_validation.__main__')]:
            with patch(module+'.local_client',side_effect=AssertionError('credential access')),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as error: cli(['--help'])
            self.assertEqual(error.exception.code,0)
    def test_inspect_no_credentials(self):
        fid=self.frontier_run(); vid=self.validation_run()
        for cli,module,rid in [(frontier_cli,'scholarflow.frontier.__main__',fid),(validation_cli,'scholarflow.search_validation.__main__',vid)]:
            with patch(module+'.local_client',side_effect=AssertionError('credential access')),contextlib.redirect_stdout(io.StringIO()): self.assertEqual(cli(['inspect','--workspace',str(self.db.workspace),'--run-id',rid]),0)
    def test_all_stage09_sql_evidence_immutable(self):
        self.primary([work(1,referenced_works=['https://openalex.org/W1'])]); fid=self.frontier_run(max_rounds=1); self.to_stop(fid); self.frontier.finalize(fid); vid=self.validation_run(sentinels=[dict(namespace='openalex_work_id',value='W1')]); self.validation.execute(vid); self.validation.finalize(vid)
        connection=sqlite3.connect(self.db.path); connection.create_function('stage09_authorized',2,lambda *_:0); connection.create_function('foundation_sha256_text',1,lambda s:__import__('hashlib').sha256(s.encode()).hexdigest()); connection.execute('PRAGMA recursive_triggers=OFF')
        try:
            for table in ['frontier_run','frontier_round','frontier_action','search_validation_run','search_validation_variant','search_validation_sentinel','search_validation_result']:
                for sql in ['DELETE FROM '+table,'INSERT OR REPLACE INTO '+table+' SELECT * FROM '+table]:
                    with self.subTest(sql=sql),self.assertRaises(sqlite3.DatabaseError): connection.execute(sql)
                    connection.rollback()
        finally: connection.close()
    def test_tampered_final_metrics_detected(self):
        vid=self.validation_run(); self.validation.execute(vid); self.validation.finalize(vid); self.db.connection.execute('DROP TRIGGER search_validation_result_update'); self.db.connection.execute("UPDATE search_validation_result SET metrics_json='{}',metrics_sha256=foundation_sha256_text('{}')")
        with self.assertRaises(ArtifactIntegrityError): self.validation.verify(vid)
