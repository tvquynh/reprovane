"""Offline Stage10 human workflow, provenance, concurrency and separation contracts."""
import ast,contextlib,io,json,sqlite3,socket,threading,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from screening_fixtures import ScreeningCase,plan
from openalex_acquisition_fixtures import work
from scholarflow.screening import ScreeningStore,ScreeningPlan
from scholarflow.screening.__main__ import main
from scholarflow.core import FoundationError,ValidationError,ArtifactIntegrityError,MigrationDriftError
from scholarflow.core.db import Database
from scholarflow.core.migrations import MIGRATION_DIR
from scholarflow.frontier import FrontierStore
from scholarflow.observations import NormalizationProvenance


class TestScreeningPlan(unittest.TestCase):
    def test_canonicalization(self):
        a=plan(); b=dict(reversed(list(a.items()))); b['reviewers']=list(reversed(a['reviewers'])); b['criteria']=list(reversed(a['criteria'])); self.assertEqual(ScreeningPlan.from_value(a).sha256,ScreeningPlan.from_value(b).sha256)
    def test_changed_criterion_changes_hash(self):
        a=plan(); b=plan(); b['criteria'][0]['description']='Changed explicit protocol'; self.assertNotEqual(ScreeningPlan.from_value(a).sha256,ScreeningPlan.from_value(b).sha256)
    def test_full_text_rejected(self):
        with self.assertRaises(ValidationError): ScreeningPlan.from_value(plan(level='FULL_TEXT'))
    def test_ai_actors_rejected(self):
        for actor in ['AI','LLM','AUTOMATED_CLASSIFIER']:
            with self.subTest(actor=actor),self.assertRaises(ValidationError): ScreeningPlan.from_value(plan(reviewers=[dict(label='reviewer_1',actor_type=actor)]))
    def test_duplicate_actor_rejected(self):
        with self.assertRaises(ValidationError): ScreeningPlan.from_value(plan(reviewers=[dict(label='r',actor_type='HUMAN')]*2))
    def test_actor_email_rejected(self):
        with self.assertRaises(ValidationError): ScreeningPlan.from_value(plan(adjudicators=[dict(label='person@example.invalid',actor_type='HUMAN')]))
    def test_operational_reason_codes_rejected(self):
        for code in ['MAX_DEPTH','ROUND_SEED_LIMIT','GLOBAL_BUDGET_LIMIT','STRUCTURAL_SATURATION','max_depth']:
            p=plan(); p['criteria'][1]['code']=code
            with self.subTest(code=code),self.assertRaises(ValidationError): ScreeningPlan.from_value(p)
    def test_all_reviewers_requires_exact_count(self):
        with self.assertRaises(ValidationError): ScreeningPlan.from_value(plan(required_reviewers=1))
    def test_missing_plan_key_rejected(self):
        p=plan(); del p['consensus_policy']
        with self.assertRaises(ValidationError): ScreeningPlan.from_value(p)
    def test_note_bound_required(self):
        with self.assertRaises(ValidationError): ScreeningPlan.from_value(plan(note_policy=dict(allow=True,max_length=4001)))
    def test_explicit_human_adjudicator_required(self):
        with self.assertRaises(ValidationError): ScreeningPlan.from_value(plan(adjudicators=[]))


class TestScreeningCreation(ScreeningCase):
    def test_schema10_idempotent(self):
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],10); ScreeningStore(self.db); self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],10)
    def test_clean9_to10_upgrade(self):
        with Database(self.root/'upgrade') as db:
            FrontierStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9); ScreeningStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
    def test_migration_drift(self):
        folder=self.root/'migrations'; folder.mkdir()
        for p in MIGRATION_DIR.glob('*.sql'): (folder/p.name).write_bytes(p.read_bytes())
        f=folder/'0010_screening_separation.sql'; f.write_bytes(b'-- drift\n'+f.read_bytes())
        with self.assertRaises(MigrationDriftError):
            with Database(self.db.workspace,migration_dir=folder,target_schema_version=10): pass
    def test_open_discovery_rejected(self):
        with self.assertRaises(ValidationError): self.store.create(self.revision,self.rid,plan())
    def test_materialized_unsealed_discovery_rejected(self):
        self.primary([work(1)]); self.discovery.materialize(self.rid)
        with self.assertRaises(ValidationError): self.store.create(self.revision,self.rid,plan())
    def test_same_revision_required(self):
        self.primary([work(1)]); self.discovery.seal(self.rid); rev=self.store.foundation.create_project_revision(self.project['project_id'],2,'json','{}')['revision_id']
        with self.assertRaises(ValidationError): self.store.create(rev,self.rid,plan())
    def test_unsealed_frontier_rejected(self):
        from test_frontier_search_validation import policy
        self.primary([work(1,referenced_works=[])]); frontier=FrontierStore(self.db); fid=frontier.create(self.revision,self.rid,policy(max_rounds=1))['frontier_run_id']; frontier.run_to_policy_stop(fid,self.client); self.discovery.seal(self.rid)
        with self.assertRaises(ValidationError): self.store.create(self.revision,self.rid,plan())
    def test_sealed_frontier_provenance(self):
        from test_frontier_search_validation import policy
        self.primary([work(1,referenced_works=[])]); frontier=FrontierStore(self.db); fid=frontier.create(self.revision,self.rid,policy(max_rounds=1))['frontier_run_id']; frontier.run_to_policy_stop(fid,self.client); frontier.finalize(fid); result=self.screening(); self.assertEqual(result['run']['frontier_run_id'],fid); self.assertTrue(self.store.verify(self.sid))
    def test_multi_route_one_candidate(self):
        self.primary([work(1)]); self.primary([work(1)]); self.discovery.seal(self.rid); result=self.screening(); self.assertEqual(result['candidate_count'],1)
    def test_unresolved_not_candidates_but_reported(self):
        self.screening([work(1),work(2,doi=None,title='Ambiguous synthetic'),work(3,doi=None,title='Ambiguous synthetic')]); result=self.store.inspect(self.sid); self.assertEqual(result['candidate_count'],1); self.assertEqual(result['unresolved_discovery']['provider_level_unresolved_count'],2)
    def test_candidate_identity_order_and_hash(self):
        self.screening([work(1),work(2)]); self.assertEqual([c['publication_entity_id'] for c in self.cs],sorted(c['publication_entity_id'] for c in self.cs)); self.assertEqual(self.store._candidate_hash(self.sid,self.cs),self.store.get(self.sid)['candidate_set_sha256']); self.assertTrue(self.store.verify(self.sid))
    def test_assignments_exact_required_count(self):
        self.screening([work(1),work(2)]); self.assertEqual(len(self.store.assignments(self.sid)),4)
    def test_explicit_assignment(self):
        self.primary([work(1)]); self.discovery.seal(self.rid); entity=self.discovery.memberships(self.rid)[0]['publication_entity_id']; self.screening(protocol=plan(required_reviewers=1,assignment_policy='EXPLICIT_ASSIGNMENT',assignments={entity:['reviewer_2']})); self.assertEqual([a['reviewer_label'] for a in self.store.assignments(self.sid)],['reviewer_2'])
    def test_explicit_assignment_wrong_coverage(self):
        self.primary([work(1)]); self.discovery.seal(self.rid)
        with self.assertRaises(ValidationError): self.store.create(self.revision,self.rid,plan(required_reviewers=1,assignment_policy='EXPLICIT_ASSIGNMENT',assignments={}))
    def test_explicit_invalid_reviewer_rejected(self):
        with self.assertRaises(ValidationError): ScreeningPlan.from_value(plan(required_reviewers=1,assignment_policy='EXPLICIT_ASSIGNMENT',assignments={'entity':['other']}))
    def test_creation_atomic_on_assignment_failure(self):
        self.primary([work(1)]); self.discovery.seal(self.rid); original=self.store.insert
        def broken(table,row):
            if table=='screening_assignment': raise RuntimeError('synthetic freeze fault')
            return original(table,row)
        with patch.object(self.store,'insert',side_effect=broken),self.assertRaises(RuntimeError): self.store.create(self.revision,self.rid,plan())
        for table in ['screening_run','screening_reviewer','screening_candidate','screening_assignment']: self.assertEqual(self.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
    def test_empty_screening_explicitly_retains_zero_candidates(self):
        self.screening([]); self.store.finalize(self.sid); self.assertEqual(self.store.handoff(self.sid)['screen_include_entity_ids'],[])


class TestScreeningDecisions(ScreeningCase):
    def setUp(self): super().setUp(); self.screening()
    def test_include_accepted(self): self.assertEqual(self.vote()['decision'],'SCREEN_INCLUDE')
    def test_exclude_requires_reason(self):
        with self.assertRaises(ValidationError): self.vote(decision='SCREEN_EXCLUDE')
    def test_exclude_valid_reason(self): self.assertEqual(json.loads(self.vote(decision='SCREEN_EXCLUDE',reason_codes=['X'])['reason_codes_json']),['X'])
    def test_unknown_reason_rejected(self):
        with self.assertRaises(ValidationError): self.vote(decision='SCREEN_EXCLUDE',reason_codes=['UNKNOWN'])
    def test_guidance_not_exclusion_reason(self):
        with self.assertRaises(ValidationError): self.vote(decision='SCREEN_EXCLUDE',reason_codes=['G'])
    def test_include_rejects_reason(self):
        with self.assertRaises(ValidationError): self.vote(reason_codes=['X'])
    def test_unsure_rejects_reason(self):
        with self.assertRaises(ValidationError): self.vote(decision='SCREEN_UNSURE',reason_codes=['X'])
    def test_unsure_accepted(self): self.assertEqual(self.vote(decision='SCREEN_UNSURE')['decision'],'SCREEN_UNSURE')
    def test_invalid_decision(self):
        with self.assertRaises(ValidationError): self.vote(decision='INCLUDE')
    def test_unassigned_reviewer_rejected(self):
        with self.assertRaises(ValidationError): self.vote(reviewer='unknown')
    def test_adjudicator_not_reviewer(self):
        with self.assertRaises(ValidationError): self.vote(reviewer='adjudicator_1')
    def test_wrong_candidate_rejected(self):
        with self.assertRaises(ValidationError): self.store.submit_decision(self.sid,'wrong','reviewer_1','SCREEN_INCLUDE')
    def test_exact_replay_idempotent(self):
        a=self.vote(); b=self.vote(); self.assertEqual(a,b); self.assertEqual(len(self.store.decisions(self.sid)),1)
    def test_changed_decision_requires_supersession(self):
        self.vote()
        with self.assertRaises(ValidationError): self.vote(decision='SCREEN_UNSURE')
    def test_supersession_preserves_both(self):
        old=self.vote(); new=self.vote(decision='SCREEN_EXCLUDE',reason_codes=['X'],supersedes_decision_id=old['decision_id']); self.assertEqual(new['supersedes_decision_id'],old['decision_id']); self.assertEqual(len(self.store.decisions(self.sid)),2); self.assertEqual(self.store.inspect(self.sid)['statistics']['decision_distribution'],{'SCREEN_EXCLUDE':1})
    def test_cannot_supersede_other_reviewer(self):
        a=self.vote()
        with self.assertRaises(ValidationError): self.vote('reviewer_2',supersedes_decision_id=a['decision_id'])
    def test_stale_supersession_rejected(self):
        a=self.vote(); self.vote(decision='SCREEN_UNSURE',supersedes_decision_id=a['decision_id'])
        with self.assertRaises(ValidationError): self.vote(decision='SCREEN_EXCLUDE',reason_codes=['X'],supersedes_decision_id=a['decision_id'])
    def test_old_batch_replay_does_not_restore_old_vote(self):
        a=self.vote(); self.vote(decision='SCREEN_UNSURE',supersedes_decision_id=a['decision_id']); self.vote(); self.assertEqual(self.store.inspect(self.sid)['statistics']['decision_distribution'],{'SCREEN_UNSURE':1})
    def test_bounded_note(self):
        with self.assertRaises(ValidationError): self.vote(note='x'*501)
    def test_sensitive_notes_rejected(self):
        for note in ['password=synthetic','Bearer SYNTHETIC_VALUE','api_key:synthetic','private@example.invalid','cookie=synthetic']:
            with self.subTest(note=note),self.assertRaises(ValidationError): self.vote(note=note)
    def test_optional_known_private_guard(self):
        self.store.forbidden_values=('SYNTHETIC_PRIVATE_VALUE',)
        with self.assertRaises(ValidationError): self.vote(note='SYNTHETIC_PRIVATE_VALUE')
    def test_multiple_reasons_require_plan(self):
        with self.assertRaises(ValidationError): self.vote(decision='SCREEN_EXCLUDE',reason_codes=['X','Y'])


class TestScreeningConsensus(ScreeningCase):
    def test_single_reviewer_include(self):
        self.screening(protocol=plan(reviewers=[dict(label='reviewer_1',actor_type='HUMAN')],required_reviewers=1)); self.vote(); self.assertEqual(self.store.outcomes(self.sid)[0]['outcome'],'SCREEN_INCLUDE')
    def test_single_reviewer_exclude(self):
        self.screening(protocol=plan(reviewers=[dict(label='reviewer_1',actor_type='HUMAN')],required_reviewers=1)); self.vote(decision='SCREEN_EXCLUDE',reason_codes=['X']); self.assertEqual(self.store.outcomes(self.sid)[0]['outcome'],'SCREEN_EXCLUDE')
    def test_unanimous_include(self):
        self.screening(); self.unanimous(); self.assertEqual(self.store.outcomes(self.sid)[0]['resolution_basis'],'AUTO_CONSENSUS')
    def test_unanimous_exclude_union_reasons(self):
        self.screening(); self.vote(decision='SCREEN_EXCLUDE',reason_codes=['X']); self.vote('reviewer_2','SCREEN_EXCLUDE',reason_codes=['Y']); self.assertEqual(json.loads(self.store.outcomes(self.sid)[0]['reason_codes_json']),['X','Y'])
    def test_include_exclude_conflict(self):
        self.screening(); self.conflict(); self.assertEqual(self.store.outcomes(self.sid)[0]['status'],'CONFLICT')
    def test_include_unsure_conflict(self):
        self.screening(); self.vote(); self.vote('reviewer_2','SCREEN_UNSURE'); self.assertEqual(self.store.outcomes(self.sid)[0]['status'],'CONFLICT')
    def test_exclude_unsure_conflict(self):
        self.screening(); self.vote(decision='SCREEN_EXCLUDE',reason_codes=['X']); self.vote('reviewer_2','SCREEN_UNSURE'); self.assertEqual(self.store.outcomes(self.sid)[0]['status'],'CONFLICT')
    def test_missing_reviewer_pending(self):
        self.screening(); self.vote(); self.assertEqual(self.store.outcomes(self.sid)[0]['status'],'PENDING'); self.assertEqual(self.store.get(self.sid)['state'],'OPEN')
    def test_all_unsure_never_final(self):
        self.screening(); self.unanimous(decision='SCREEN_UNSURE'); self.assertEqual(self.store.get(self.sid)['state'],'REVIEW_COMPLETE'); self.assertEqual(self.store.outcomes(self.sid)[0]['outcome'],'UNRESOLVED')
    def test_consensus_disabled_requires_adjudication(self):
        self.screening(protocol=plan(consensus_policy='DISABLED')); self.unanimous(); self.assertEqual(self.store.outcomes(self.sid)[0]['status'],'CONFLICT'); self.adjudicate('SCREEN_INCLUDE'); self.assertEqual(self.store.outcomes(self.sid)[0]['resolution_basis'],'ADJUDICATED')
    def test_no_majority_resolution(self):
        self.screening(protocol=plan(required_reviewers=3,reviewers=[dict(label='reviewer_'+str(i),actor_type='HUMAN') for i in [1,2,3]])); self.vote(); self.vote('reviewer_2'); self.vote('reviewer_3','SCREEN_EXCLUDE',reason_codes=['X']); self.assertEqual(self.store.outcomes(self.sid)[0]['outcome'],'UNRESOLVED')


class TestScreeningAdjudication(ScreeningCase):
    def setUp(self): super().setUp(); self.screening(); self.conflict()
    def test_human_resolves_exclude(self):
        self.adjudicate(); self.assertEqual(self.store.outcomes(self.sid)[0]['outcome'],'SCREEN_EXCLUDE'); self.assertTrue(self.store.verify(self.sid))
    def test_human_resolves_include(self):
        self.adjudicate('SCREEN_INCLUDE'); self.assertEqual(self.store.outcomes(self.sid)[0]['outcome'],'SCREEN_INCLUDE')
    def test_non_adjudicator_rejected(self):
        with self.assertRaises(ValidationError): self.store.submit_adjudication(self.sid,self.cs[0]['candidate_id'],'reviewer_1','SCREEN_INCLUDE',input_decision_ids=[r['decision_id'] for r in self.store.decisions(self.sid)])
    def test_adjudication_requires_effective_inputs(self):
        with self.assertRaises(ValidationError): self.store.submit_adjudication(self.sid,self.cs[0]['candidate_id'],'adjudicator_1','SCREEN_INCLUDE',input_decision_ids=[])
    def test_adjudication_unsure_rejected(self):
        with self.assertRaises(ValidationError): self.adjudicate('SCREEN_UNSURE')
    def test_original_votes_preserved(self):
        before=self.store.decisions(self.sid); self.adjudicate(); self.assertEqual(before,self.store.decisions(self.sid))
    def test_adjudication_replay(self):
        conflict=self.store.export_conflicts(self.sid,'adjudicator_1')[0]; a=self.adjudicate(); b=self.store.submit_adjudication(self.sid,conflict['candidate_id'],'adjudicator_1','SCREEN_EXCLUDE',input_decision_ids=conflict['input_decision_ids'],reason_codes=['X']); self.assertEqual(a,b)
    def test_adjudication_correction(self):
        old=self.adjudicate(); ids=json.loads(old['input_decision_ids_json']); self.store.submit_adjudication(self.sid,self.cs[0]['candidate_id'],'adjudicator_1','SCREEN_INCLUDE',input_decision_ids=ids,supersedes_adjudication_id=old['adjudication_id']); self.assertEqual(len(self.store.adjudications(self.sid)),2); self.assertEqual(self.store.outcomes(self.sid)[0]['outcome'],'SCREEN_INCLUDE')
    def test_review_correction_invalidates_stale_adjudication(self):
        self.adjudicate(); old=next(r for r in self.store.decisions(self.sid) if r['reviewer_label']=='reviewer_1'); self.vote(decision='SCREEN_UNSURE',supersedes_decision_id=old['decision_id']); self.assertEqual(self.store.outcomes(self.sid)[0]['status'],'CONFLICT'); self.assertEqual(self.store.get(self.sid)['state'],'REVIEW_COMPLETE')
    def test_adjudication_atomic_on_materialization_failure(self):
        with patch.object(self.store,'_materialize',side_effect=RuntimeError('synthetic')),self.assertRaises(RuntimeError): self.adjudicate()
        self.assertEqual(self.store.adjudications(self.sid),[]); self.assertEqual(self.store.get(self.sid)['state'],'REVIEW_COMPLETE')


class TestScreeningPackets(ScreeningCase):
    def setUp(self): super().setUp(); self.screening([work(1),work(2)])
    def test_packet_hides_peer_votes(self):
        before=self.store.review_packet(self.sid,'reviewer_1'); self.vote('reviewer_2',note='Synthetic peer annotation'); after=self.store.review_packet(self.sid,'reviewer_1'); self.assertEqual(before,after); self.assertNotIn('Synthetic peer annotation',json.dumps(after))
    def test_template_no_prefill(self): self.assertTrue(all(r['decision'] is None for r in self.store.decision_template(self.sid,'reviewer_1')))
    def test_export_deterministic(self):
        a=self.store.export_review_packet(self.sid,'reviewer_1',self.root/'packets'); before=Path(a['packet_path']).read_bytes(); b=self.store.export_review_packet(self.sid,'reviewer_1',self.root/'packets'); self.assertEqual(a,b); self.assertEqual(before,Path(b['packet_path']).read_bytes())
    def test_export_preserves_completed_human_file(self):
        a=self.store.export_review_packet(self.sid,'reviewer_1',self.root/'packets'); p=Path(a['template_path']); p.write_text('completed human work',encoding='utf-8')
        with self.assertRaises(ValidationError): self.store.export_review_packet(self.sid,'reviewer_1',self.root/'packets')
        self.assertEqual(p.read_text(encoding='utf-8'),'completed human work')
    def rows(self):
        rows=self.store.decision_template(self.sid,'reviewer_1')
        for r in rows: r['decision']='SCREEN_INCLUDE'
        return rows
    def test_batch_replay_idempotent(self):
        rows=self.rows(); a=self.store.import_decisions(self.sid,'reviewer_1',rows); b=self.store.import_decisions(self.sid,'reviewer_1',rows); self.assertEqual(a,b); self.assertEqual(len(self.store.decisions(self.sid)),2)
    def test_all_packet_identity_fields_validated(self):
        for key in ['screening_run_id','plan_sha256','candidate_set_sha256','assignment_sha256','reviewer_label','candidate_id','evidence_sha256','packet_sha256']:
            rows=self.rows(); rows[0][key]='wrong'
            with self.subTest(key=key),self.assertRaises(ValidationError): self.store.import_decisions(self.sid,'reviewer_1',rows)
        self.assertEqual(self.store.decisions(self.sid),[])
    def test_invalid_batch_atomic(self):
        rows=self.rows(); rows[-1]['decision']='SCREEN_EXCLUDE'
        with self.assertRaises(ValidationError): self.store.import_decisions(self.sid,'reviewer_1',rows)
        self.assertEqual(self.store.decisions(self.sid),[])
    def test_mixed_reviewer_rejected(self):
        rows=self.rows(); rows[-1]['reviewer_label']='reviewer_2'
        with self.assertRaises(ValidationError): self.store.import_decisions(self.sid,'reviewer_1',rows)
    def test_packet_includes_frozen_evidence_hash(self):
        packet=self.store.review_packet(self.sid,'reviewer_1'); self.assertEqual([r['evidence_sha256'] for r in packet['items']],[c['evidence_sha256'] for c in self.cs])
    def test_conflicts_require_adjudicator(self):
        with self.assertRaises(ValidationError): self.store.export_conflicts(self.sid,'reviewer_1')


class TestScreeningEvidence(ScreeningCase):
    def test_title_and_abstract(self):
        self.screening([work(1,abstract_inverted_index={'Synthetic':[0],'abstract':[1]})]); fields=json.loads(self.cs[0]['evidence_json'])['records'][0]['fields']; self.assertEqual(fields['title'][0]['value'],'Synthetic Work 1'); self.assertEqual(fields['abstract'][0]['value'],'Synthetic abstract')
    def test_missing_abstract_preserved(self):
        self.screening(); self.assertIsNone(json.loads(self.cs[0]['evidence_json'])['records'][0]['fields']['abstract'])
    def test_venue_hidden_when_not_allowed(self):
        self.screening(protocol=plan(allowed_evidence_fields=['title'])); fields=json.loads(self.cs[0]['evidence_json'])['records'][0]['fields']; self.assertEqual(list(fields),['title'])
    def test_venue_only_provider_evidence(self):
        self.screening(); fields=json.loads(self.cs[0]['evidence_json'])['records'][0]['fields']; self.assertEqual(fields['venue'][0]['title'],'Synthetic Journal'); self.assertEqual(self.store.outcomes(self.sid)[0]['outcome'],'UNRESOLVED')
    def test_no_missing_doi_or_retraction_exclusion(self):
        self.screening([work(1,doi=None,is_retracted=True)]); fields=json.loads(self.cs[0]['evidence_json'])['records'][0]['fields']; self.assertIsNone(fields['doi']); self.assertEqual(fields['integrity'][0]['event_type'],'RETRACTION'); self.assertEqual(self.store.outcomes(self.sid)[0]['outcome'],'UNRESOLVED')
    def test_new_observations_do_not_change_frozen_packet(self):
        self.screening(); before=self.store.review_packet(self.sid,'reviewer_1'); rid=json.loads(self.cs[0]['evidence_refs_json'])[0]['provider_record_id']; self.discovery.observations.add_publication_metadata_observation(rid,NormalizationProvenance('later-profile','1','1'),title='Later new claim'); self.assertEqual(before,self.store.review_packet(self.sid,'reviewer_1')); self.assertTrue(self.store.verify(self.sid))
    def test_evidence_tampering_detected(self):
        self.screening(); self.db.connection.execute('DROP TRIGGER publication_metadata_observation_no_update'); self.db.connection.execute("UPDATE publication_metadata_observation SET title='tampered'")
        with self.assertRaises((ArtifactIntegrityError,FoundationError)): self.store.verify(self.sid)


class TestScreeningLifecycle(ScreeningCase):
    def test_unresolved_cannot_seal(self):
        self.screening()
        with self.assertRaises(ValidationError): self.store.finalize(self.sid)
        self.assertEqual(self.store.get(self.sid)['state'],'OPEN')
    def test_conflict_cannot_seal(self):
        self.screening(); self.conflict()
        with self.assertRaises(ValidationError): self.store.finalize(self.sid)
    def test_resolve_and_seal(self):
        self.screening(); self.unanimous(); self.store.resolve(self.sid); self.assertEqual(self.store.get(self.sid)['state'],'RESOLVED'); self.store.finalize(self.sid); self.assertTrue(self.store.verify(self.sid))
    def test_post_seal_decision_and_supersession_rejected(self):
        self.screening(); old=self.unanimous()[0]; self.store.finalize(self.sid)
        with self.assertRaises(ValidationError): self.vote()
        with self.assertRaises(ValidationError): self.vote(decision='SCREEN_UNSURE',supersedes_decision_id=old['decision_id'])
    def test_post_seal_adjudication_rejected(self):
        self.screening(); self.conflict(); self.adjudicate(); self.store.finalize(self.sid)
        with self.assertRaises(ValidationError): self.store.submit_adjudication(self.sid,self.cs[0]['candidate_id'],'adjudicator_1','SCREEN_INCLUDE',input_decision_ids=[r['decision_id'] for r in self.store.decisions(self.sid)])
    def test_outcome_registration_atomic(self):
        self.screening(); original=self.store.insert
        def broken(table,row):
            if table=='screening_outcome': raise RuntimeError('synthetic')
            return original(table,row)
        with patch.object(self.store,'insert',side_effect=broken),self.assertRaises(RuntimeError): self.vote()
        self.assertEqual(self.store.decisions(self.sid),[]); self.assertEqual(self.store.get(self.sid)['state'],'OPEN')
    def test_seal_atomic_on_verification_failure(self):
        self.screening(); self.unanimous()
        with patch.object(self.store,'verify',side_effect=RuntimeError('synthetic')),self.assertRaises(RuntimeError): self.store.finalize(self.sid)
        self.assertEqual(self.store.get(self.sid)['state'],'RESOLVED'); self.assertIsNone(self.store.get(self.sid)['finalization_sha256'])
    def test_read_only_handoff(self):
        self.screening(); self.unanimous(); self.store.finalize(self.sid); result=self.store.handoff(self.sid); self.assertEqual(result['screen_include_entity_ids'],[self.cs[0]['publication_entity_id']]); self.assertEqual(len(result['decision_provenance']),2)
    def test_handoff_requires_seal(self):
        self.screening()
        with self.assertRaises(ValidationError): self.store.handoff(self.sid)
    def test_outcome_tampering_detected(self):
        self.screening(); self.unanimous(); self.store.finalize(self.sid); self.db.connection.execute('DROP TRIGGER screening_outcome_update'); self.db.connection.execute("UPDATE screening_outcome SET outcome='SCREEN_EXCLUDE' WHERE resolution_id=?",(self.store.get(self.sid)['current_resolution_id'],))
        with self.assertRaises(ArtifactIntegrityError): self.store.verify(self.sid)
    def test_decision_tampering_detected(self):
        self.screening(); self.unanimous(); self.store.finalize(self.sid); self.db.connection.execute('DROP TRIGGER screening_decision_update'); self.db.connection.execute("UPDATE screening_decision SET note='tampered'")
        with self.assertRaises(ArtifactIntegrityError): self.store.verify(self.sid)
    def test_sql_immutability_all_evidence(self):
        self.screening(); self.conflict(); self.adjudicate(); self.store.finalize(self.sid); connection=sqlite3.connect(self.db.path); connection.create_function('screening_authorized',2,lambda *_:0); connection.create_function('foundation_sha256_text',1,lambda s:__import__('hashlib').sha256(s.encode()).hexdigest()); connection.execute('PRAGMA recursive_triggers=OFF')
        try:
            for table in ['screening_run','screening_reviewer','screening_candidate','screening_assignment','screening_decision','screening_adjudication','screening_resolution','screening_outcome']:
                column=connection.execute('PRAGMA table_info('+table+')').fetchone()[1]
                for sql in ['DELETE FROM '+table,'UPDATE '+table+' SET '+column+'='+column,'INSERT OR REPLACE INTO '+table+' SELECT * FROM '+table]:
                    with self.subTest(sql=sql),self.assertRaises(sqlite3.DatabaseError): connection.execute(sql)
                    connection.rollback()
        finally: connection.close()


class TestScreeningConcurrency(ScreeningCase):
    def race(self,options):
        barrier=threading.Barrier(2); workspace=self.db.workspace; sid=self.sid; cid=self.cs[0]['candidate_id']
        def submit(option):
            with Database(workspace) as db:
                s=ScreeningStore(db); barrier.wait()
                try: return s.submit_decision(sid,cid,'reviewer_1',**option)['decision_id']
                except ValidationError: return 'STALE_REJECTED'
        with ThreadPoolExecutor(max_workers=2) as pool: return list(pool.map(submit,options))
    def test_concurrent_initial_submissions_single_effective(self):
        self.screening(); result=self.race([dict(decision='SCREEN_INCLUDE'),dict(decision='SCREEN_UNSURE')]); self.assertEqual(result.count('STALE_REJECTED'),1); self.assertEqual(len(self.store.decisions(self.sid)),1)
    def test_concurrent_corrections_fail_closed(self):
        self.screening(); old=self.vote(); result=self.race([dict(decision='SCREEN_UNSURE',supersedes_decision_id=old['decision_id']),dict(decision='SCREEN_EXCLUDE',reason_codes=['X'],supersedes_decision_id=old['decision_id'])]); self.assertEqual(result.count('STALE_REJECTED'),1); self.assertEqual(self.store.inspect(self.sid)['statistics']['effective_decision_count'],1)


class TestScreeningSeparation(ScreeningCase):
    def test_offline_no_credentials_or_network(self):
        with patch('socket.create_connection',side_effect=AssertionError('network')),patch('requests.sessions.Session.request',side_effect=AssertionError('HTTP')),patch('scholarflow.openalex_acquisition.credentials.local_client',side_effect=AssertionError('credentials')):
            self.screening(); self.unanimous(); self.store.finalize(self.sid); self.store.verify(self.sid); self.store.review_packet(self.sid,'reviewer_1')
        self.assertFalse(self.client.api_key)
    def test_no_second_engine_or_ai_imports_calls(self):
        folder=Path(__file__).parents[1]/'scholarflow/screening'
        for path in folder.glob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for n in ast.walk(tree):
                if isinstance(n,(ast.Import,ast.ImportFrom)):
                    names=[a.name for a in n.names]+[getattr(n,'module','') or '']; self.assertFalse(any(name.split('.')[0] in ['requests','urllib','httpx','openai','transformers','sentence_transformers','subprocess','fulltext','unpaywall'] for name in names))
                if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute): self.assertNotIn(n.func.attr,['expand_backward','expand_forward','resolve_seeds','generate_candidates','evaluate_candidates_automatically','acquire','resume','request_page','run_to_policy_stop'])
    def test_no_corpus_release_schema(self):
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}; self.assertFalse(tables&{'corpus_release','corpus_membership','corpus_member','final_corpus'})
    def test_cli_help_and_inspect_offline(self):
        with patch('socket.create_connection',side_effect=AssertionError('network')),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as error: main(['--help'])
        self.assertEqual(error.exception.code,0); self.screening()
        with patch('socket.create_connection',side_effect=AssertionError('network')),contextlib.redirect_stdout(io.StringIO()): self.assertEqual(main(['inspect','--workspace',str(self.db.workspace),'--run-id',self.sid]),0)
    def test_search_validation_not_a_prerequisite(self):
        self.screening(); self.assertEqual(self.db.execute('SELECT count(*) FROM search_validation_run').fetchone()[0],0)
