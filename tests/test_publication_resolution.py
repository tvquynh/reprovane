"""Offline scientific/ledger contracts for conservative publication resolution."""
import ast
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from reprovane.core import ValidationError, MigrationDriftError
from reprovane.core.db import Database
from reprovane.core.foundation import Foundation
from reprovane.core.migrations import MIGRATION_DIR
from reprovane.core.provenance import canonical_json
from reprovane.observations import ObservationStore, NormalizationProvenance
from reprovane.resolution import (ResolutionStateError, ResolutionConflictError, CandidateBlockTooLargeError,
                                DecisionSupersessionError)
from reprovane.resolution.store import ResolutionStore
from reprovane.resolution.policy import ResolverPolicy, digest, POLICY_NAME
from reprovane.resolution.normalization import normalize_doi, normalize_title, normalize_author, classify_publication_type
from reprovane.resolution.resolver import entity_identity, automatic

PROFILE={'normalizer_name':'fixture-normalizer','normalizer_version':'1','observation_schema_version':'1'}
SELECTOR={'profiles':[PROFILE]}


class PublicationResolutionCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.db=Database(self.root/'workspace'); self.f=Foundation(self.db)
        self.obs=ObservationStore(self.db); self.store=ResolutionStore(self.db)
        p=self.f.create_project('synthetic_review','Synthetic review')
        self.revision=self.f.create_project_revision(p['project_id'],1,'yaml','topic: synthetic\n')
        self.norm=NormalizationProvenance('fixture-normalizer','1','1')
        self.providers={}; self.counter=0

    def tearDown(self): self.db.close(); self.temp.cleanup()

    def record(self,*,provider=None,native=None,title=None,year=2024,dois=(),ptype='article',author=None,sealed=True,version='1'):
        self.counter+=1; provider=provider or 'synthetic_'+str(self.counter)
        if provider not in self.providers: self.providers[provider]=self.f.register_provider(provider,'Synthetic provider')
        s=self.f.create_provider_snapshot(self.providers[provider]['provider_id'],'synthetic','MANUAL_IMPORT',
            parser_name='fixture',parser_version='1',schema_profile='opaque',schema_version='1')
        a=self.f.artifacts.put_bytes(b'{"synthetic":"opaque payload not parsed by resolver"}')
        self.f.register_raw_artifact(a); self.f.attach_artifact_to_snapshot(s['snapshot_id'],a.artifact_sha256,'file',0)
        r=self.f.add_provider_record(s['snapshot_id'],a.artifact_sha256,'/',provider_native_id=native)
        if not sealed: return r['provider_record_id']
        self.f.seal_provider_snapshot(s['snapshot_id']); rid=r['provider_record_id']
        norm=NormalizationProvenance('fixture-normalizer',version,'1')
        if title is not None: self.obs.add_publication_metadata_observation(rid,norm,title=title,publication_year=year,publication_type=ptype)
        for doi in dois: self.obs.add_publication_identifier_observation(rid,norm,identifier_namespace='doi',identifier_value_original=doi,identifier_value_normalized=doi)
        if author: self.obs.add_publication_authorship_observation(rid,norm,author_position=1,author_display_name=author)
        return rid

    def run_for(self,records=(),**kwargs):
        r=self.store.create_resolution_run(self.revision['revision_id'],kwargs.pop('selector',SELECTOR),**kwargs)
        if records: self.store.add_resolution_inputs(r['resolution_run_id'],list(records))
        return r['resolution_run_id']

    def pair(self,**overrides):
        left=dict(title='Example',dois=['10.1234/synthetic']); left.update(overrides.pop('left',{}))
        right=dict(title='Example',dois=['10.1234/synthetic']); right.update(overrides.pop('right',{}))
        a,b=self.record(**left),self.record(**right); run=self.run_for([a,b],**overrides)
        candidates=self.store.generate_candidates(run)
        return run,a,b,candidates[0] if candidates else None

    def evaluated_pair(self,**kwargs):
        run,a,b,c=self.pair(**kwargs); self.store.evaluate_candidates_automatically(run)
        return run,a,b,c,self.store.get_active_decision(c['candidate_id'])

    def materialized(self,same=True):
        if same:
            run,a,b,c,d=self.evaluated_pair()
        else:
            a,b=self.record(title='Different A'),self.record(title='Different B')
            run=self.run_for([a,b]); self.store.generate_candidates(run)
        self.store.materialize_resolution_run(run)
        return run,a,b

    def raw_insert(self,table,row,replace=False,connection=None):
        sql=('INSERT OR REPLACE' if replace else 'INSERT')+' INTO '+table+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')'
        return (connection or self.db.connection).execute(sql,tuple(row.values()))

    def test_migration_0004_applies(self): self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],4)
    def test_ledger_1234(self): self.assertEqual([r[0] for r in self.db.execute('SELECT version FROM schema_migration ORDER BY version')],[1,2,3,4])
    def test_historical_migration_hashes(self):
        expected=['6f5779b2073b4cdeb4bc8a70b8fbcf3f5892348eeedf74d35fa66430ba0e6b8e','7356fa1ddf9881c53873626b43a8f54132451e1d86aff53b15c4ae556326dd26','6c84d9e10203593caf4a47ee84e3e03bcc9e0454a101f6661abed8e58c722c4b']
        for path,want in zip(sorted(MIGRATION_DIR.glob('*.sql')),expected): self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),want)
    def test_0004_drift(self):
        folder=self.root/'drift'; folder.mkdir()
        for p in MIGRATION_DIR.glob('*.sql'): (folder/p.name).write_bytes(p.read_bytes()+(b'\n-- deliberate drift\n' if p.name.startswith('0004') else b''))
        with self.assertRaises(MigrationDriftError): Database(self.db.workspace,migration_dir=folder,target_schema_version=4)
    def test_observation_store_still_schema3(self):
        with Database(self.root/'obs') as d:
            ObservationStore(d); self.assertEqual(d.execute('PRAGMA user_version').fetchone()[0],3)
    def test_bare_foundation_schema1(self):
        with Database(self.root/'foundation') as d: self.assertEqual(d.execute('PRAGMA user_version').fetchone()[0],1)
    def test_create_run_revision(self):
        run=self.run_for(); self.assertEqual(self.store.get_resolution_run(run)['project_revision_id'],self.revision['revision_id'])
    def test_canonical_policy_hash(self):
        row=self.store.get_resolution_run(self.run_for()); self.assertEqual(row['policy_json'],canonical_json(ResolverPolicy().as_dict())); self.assertEqual(row['policy_sha256'],digest(json.loads(row['policy_json'])))
    def test_selector_hash(self):
        row=self.store.get_resolution_run(self.run_for()); self.assertEqual(row['observation_selector_sha256'],digest(SELECTOR))
    def test_invalid_policy(self):
        for value in ['{','[]',{'allow_fuzzy_auto_merge':True}]:
            with self.subTest(value=value),self.assertRaises(ValidationError): self.run_for(policy=value)
    def test_invalid_selector(self):
        for value in ['{','[]',{'profiles':[]},{'profiles':[dict(PROFILE,normalizer_version='')]}]:
            with self.subTest(value=value),self.assertRaises(ValidationError): self.run_for(selector=value)
    def test_open_state(self):
        r=self.store.get_resolution_run(self.run_for()); self.assertIsNone(r['materialized_at_utc']); self.assertIsNone(r['sealed_at_utc'])
    def test_seal_before_materialization(self):
        with self.assertRaises(ResolutionStateError): self.store.seal_resolution_run(self.run_for())
    def test_empty_materialization(self):
        run=self.run_for(); self.store.generate_candidates(run)
        with self.assertRaises(ResolutionStateError): self.store.materialize_resolution_run(run)
    def test_run_metadata_sql_update(self):
        self.run_for()
        with self.assertRaises(sqlite3.DatabaseError): self.db.connection.execute("UPDATE resolution_run SET resolver_version='changed'")
    def test_input_sealed(self):
        rid=self.record(); run=self.run_for([rid]); self.assertEqual(self.store.list_resolution_inputs(run)[0]['provider_record_id'],rid)
    def test_input_open_rejected(self):
        rid=self.record(sealed=False)
        with self.assertRaises(ValidationError): self.store.add_resolution_input(self.run_for(),rid)
    def test_input_duplicate_rejected(self):
        rid=self.record(); run=self.run_for([rid])
        with self.assertRaises(ValidationError): self.store.add_resolution_input(run,rid)
    def test_input_update_delete(self):
        self.run_for([self.record()])
        for sql in ['UPDATE resolution_run_input SET added_at_utc=added_at_utc','DELETE FROM resolution_run_input']:
            with self.assertRaises(sqlite3.DatabaseError): self.db.connection.execute(sql)
    def test_input_after_materialized(self):
        run,_,_=self.materialized()
        with self.assertRaises(ResolutionStateError): self.store.add_resolution_input(run,self.record())
    def test_input_after_generated_frozen(self):
        run,_,_,_=self.pair()
        with self.assertRaises(ResolutionStateError): self.store.add_resolution_input(run,self.record())
    def test_only_selected_profile(self):
        a=self.record(title='Old title',dois=['10.1234/old']); b=self.record(title='Other',dois=['10.1234/new'])
        n=NormalizationProvenance('fixture-normalizer','2','1')
        self.obs.add_publication_identifier_observation(a,n,identifier_namespace='doi',identifier_value_original='10.1234/new',identifier_value_normalized='10.1234/new')
        run=self.run_for([a,b]); self.assertEqual(self.store.generate_candidates(run),[])
    def test_newer_profile_does_not_replace_old(self):
        a=self.record(title='Example'); self.obs.add_publication_metadata_observation(a,NormalizationProvenance('fixture-normalizer','2','1'),title='Changed',publication_year=2025)
        run=self.run_for([a]); proof=json.loads(self.store.list_resolution_inputs(run)[0]['evidence_json']); self.assertEqual(proof['title'],'Example')
    def test_ambiguous_metadata_blocks_auto(self):
        a=self.record(title='Example',dois=['10.1234/a']); b=self.record(title='Example',dois=['10.1234/a'])
        self.obs.add_publication_metadata_observation(a,self.norm,title='Other',publication_year=2024)
        run=self.run_for([a,b]); self.store.generate_candidates(run); d=self.store.evaluate_candidates_automatically(run)[0]
        self.assertEqual(d['decision_type'],'AMBIGUOUS'); self.assertIn('AMBIGUOUS_SELECTED_METADATA',json.loads(d['hard_conflicts_json']))
    def test_duplicate_author_position_blocks_auto(self):
        a=self.record(title='Example',dois=['10.1234/a'],author='Alpha'); b=self.record(title='Example',dois=['10.1234/a'],author='Alpha')
        self.obs.add_publication_authorship_observation(a,self.norm,author_position=1,author_display_name='Other')
        run=self.run_for([a,b]); self.store.generate_candidates(run); d=self.store.evaluate_candidates_automatically(run)[0]
        self.assertEqual(d['decision_type'],'AMBIGUOUS'); self.assertIn('AMBIGUOUS_AUTHORSHIP_POSITION',json.loads(d['hard_conflicts_json']))
    def test_no_raw_payload_reads(self):
        rid=self.record(title='Typed only'); run=self.run_for()
        seen=[]; self.db.connection.set_trace_callback(seen.append)
        self.store.add_resolution_input(run,rid); self.store.generate_candidates(run)
        self.db.connection.set_trace_callback(None)
        self.assertFalse(any('SELECT' in s.upper() and ('raw_artifact' in s or 'r.metadata_json' in s) for s in seen))
        self.assertEqual(json.loads(self.store.list_resolution_inputs(run)[0]['evidence_json'])['title'],'Typed only')
    def test_doi_prefix_case(self):
        for prefix in ['doi:','https://doi.org/','http://dx.doi.org/']:
            self.assertEqual(normalize_doi(' '+prefix+'10.1234/EXAMPLE '),'10.1234/example')
    def test_invalid_doi(self):
        for value in ['10.example/test','10.123/a','10.1234/a b','not-doi']: self.assertIsNone(normalize_doi(value))
    def test_title_normalization(self): self.assertEqual(normalize_title(' Éxample & CASE: A--B '),'example and case a b')
    def test_author_normalization(self): self.assertEqual(normalize_author('García, ANA.'),'garcia ana')
    def test_notice_classifier(self):
        for token in ['retraction','Correction Notice','erratum','expression-of-concern','withdrawal','removal']: self.assertEqual(classify_publication_type(token),'NOTICE')
    def test_other_classifier(self):
        for token in ['article','preprint',None,'unknown']: self.assertEqual(classify_publication_type(token),'OTHER_OR_UNKNOWN')
    def test_provider_native_candidate(self):
        a=self.record(provider='same_provider',native='Wsynthetic'); b=self.record(provider='same_provider',native='Wsynthetic')
        run=self.run_for([a,b]); self.assertEqual(json.loads(self.store.generate_candidates(run)[0]['candidate_methods_json']),['SAME_PROVIDER_NATIVE_ID'])
    def test_provider_native_namespace_scoped(self):
        a=self.record(provider='provider_a',native='Wsynthetic'); b=self.record(provider='provider_b',native='Wsynthetic')
        self.assertEqual(self.store.generate_candidates(self.run_for([a,b])),[])
    def test_shared_doi_candidate(self):
        run,a,b,c=self.pair(); self.assertIn('SHARED_VALID_DOI',json.loads(c['candidate_methods_json']))
    def test_title_year_candidate(self):
        run,a,b,c=self.pair(left={'dois':[]},right={'dois':[]}); self.assertEqual(json.loads(c['candidate_methods_json']),['EXACT_TITLE_YEAR'])
    def test_title_author_year_candidate(self):
        run,a,b,c=self.pair(left={'dois':[],'author':'Alpha'},right={'dois':[],'author':'Alpha'})
        self.assertIn('EXACT_TITLE_FIRST_AUTHOR_YEAR',json.loads(c['candidate_methods_json']))
    def test_same_pair_methods_merged_sorted(self):
        run,a,b,c=self.pair(left={'author':'Alpha'},right={'author':'Alpha'})
        methods=json.loads(c['candidate_methods_json']); self.assertEqual(methods,sorted(set(methods))); self.assertEqual(len(self.store.list_candidates(run)),1)
    def test_unrelated_no_pairs(self):
        records=[self.record(title='Distinct title '+str(i),year=2020+i) for i in range(12)]
        self.assertEqual(self.store.generate_candidates(self.run_for(records)),[])
    def test_same_authors_only_no_pairs(self):
        a=self.record(title='Paper A',author='Alpha'); b=self.record(title='Paper B',author='Alpha')
        self.assertEqual(self.store.generate_candidates(self.run_for([a,b])),[])
    def test_oversized_block_atomic(self):
        records=[self.record(title='Large block') for _ in range(4)]
        run=self.run_for(records,policy=ResolverPolicy(3))
        with self.assertRaises(CandidateBlockTooLargeError) as caught: self.store.generate_candidates(run)
        self.assertEqual((caught.exception.method,caught.exception.block_size,caught.exception.configured_limit),('EXACT_TITLE_YEAR',4,3))
        self.assertEqual(self.store.list_candidates(run),[]); self.assertIsNone(self.store.get_resolution_run(run)['candidates_generated_at_utc'])
    def test_oversized_does_not_persist_other_blocks(self):
        records=[self.record(title='Small',dois=['10.1234/small']) for _ in range(2)]+[self.record(title='Large') for _ in range(4)]
        run=self.run_for(records,policy=ResolverPolicy(3))
        with self.assertRaises(CandidateBlockTooLargeError): self.store.generate_candidates(run)
        self.assertEqual(self.store.list_candidates(run),[])
    def test_disjoint_doi_conflict(self):
        run,a,b,c=self.pair(left={'dois':['10.1234/a']},right={'dois':['10.1234/b']})
        self.assertIn('DISTINCT_VALID_DOI',json.loads(c['hard_conflicts_json']))
    def test_multiple_doi_conflict(self):
        run,a,b,c=self.pair(left={'dois':['10.1234/synthetic','10.1234/extra']})
        self.assertIn('MULTIPLE_VALID_DOI_CLAIMS',json.loads(c['hard_conflicts_json']))
    def test_notice_conflict(self):
        run,a,b,c=self.pair(left={'ptype':'correction notice'})
        self.assertIn('NOTICE_TYPE_CONFLICT',json.loads(c['hard_conflicts_json']))
    def test_hard_conflict_provider_id_blocks(self):
        a=self.record(provider='shared',native='one',dois=['10.1234/a']); b=self.record(provider='shared',native='one',dois=['10.1234/b'])
        run=self.run_for([a,b]); self.store.generate_candidates(run); self.assertEqual(self.store.evaluate_candidates_automatically(run)[0]['decision_type'],'AMBIGUOUS')
    def test_hard_conflict_auto_function(self):
        proof={'candidate_methods':['SAME_PROVIDER_NATIVE_ID'],'left':{'valid_dois':['10.1234/a']},'right':{'valid_dois':['10.1234/a']}}
        self.assertEqual(automatic(proof,['NOTICE_TYPE_CONFLICT']),('AMBIGUOUS','HARD_CONFLICT'))
    def test_auto_same_provider_id(self):
        a=self.record(provider='shared',native='one'); b=self.record(provider='shared',native='one')
        run=self.run_for([a,b]); self.store.generate_candidates(run); self.assertEqual(self.store.evaluate_candidates_automatically(run)[0]['decision_type'],'SAME')
    def test_auto_same_singleton_doi(self):
        run,a,b,c,d=self.evaluated_pair(); self.assertEqual((d['decision_type'],d['reason_code']),('SAME','SINGLE_SHARED_VALID_DOI'))
    def test_title_only_ambiguous(self):
        run,a,b,c,d=self.evaluated_pair(left={'dois':[]},right={'dois':[]}); self.assertEqual(d['decision_type'],'AMBIGUOUS')
    def test_title_author_only_ambiguous(self):
        run,a,b,c,d=self.evaluated_pair(left={'dois':[],'author':'Alpha'},right={'dois':[],'author':'Alpha'}); self.assertEqual(d['reason_code'],'CANDIDATE_ONLY_TITLE_AUTHOR_YEAR')
    def test_hard_conflict_doi_blocks(self):
        run,a,b,c,d=self.evaluated_pair(left={'ptype':'retraction'}); self.assertEqual(d['decision_type'],'AMBIGUOUS')
    def test_no_automatic_distinct(self):
        run,a,b,c,d=self.evaluated_pair(left={'dois':['10.1234/a']},right={'dois':['10.1234/b']}); self.assertNotEqual(d['decision_type'],'DISTINCT')
    def test_evidence_ids_versions_no_abstract(self):
        run,a,b,c=self.pair(); proof=json.loads(c['evidence_json']); self.assertTrue(proof['observation_ids_used']); self.assertIn('normalization_versions',proof); self.assertNotIn('abstract',c['evidence_json'])
    def test_decision_update_rejected(self):
        self.evaluated_pair()
        with self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute("UPDATE entity_resolution_decision SET note='changed'")
    def test_decision_delete_rejected(self):
        self.evaluated_pair()
        with self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute('DELETE FROM entity_resolution_decision')
    def test_decision_replace_external_off(self):
        run,a,b,c,d=self.evaluated_pair()
        # Reuse an existing row ID on a DIFFERENT undecided candidate: only the ID guard prevents replacement.
        x,y=self.record(title='Other'),self.record(title='Other'); run2=self.run_for([x,y]); c2=self.store.generate_candidates(run2)[0]
        bad=dict(d,resolution_run_id=run2,candidate_id=c2['candidate_id'],decision_type='SAME',decision_mode='MANUAL',reason_code='SYNTHETIC',actor_id='reviewer',evidence_json=c2['evidence_json'],hard_conflicts_json=c2['hard_conflicts_json'])
        with closing(sqlite3.connect(self.db.path,isolation_level=None)) as raw:
            raw.execute('PRAGMA recursive_triggers=OFF')
            with self.assertRaises(sqlite3.IntegrityError): self.raw_insert('entity_resolution_decision',bad,True,raw)
    def test_manual_same_supersedes_ambiguous(self):
        run,a,b,c,d=self.evaluated_pair(left={'dois':[]},right={'dois':[]})
        new=self.store.record_manual_decision(c['candidate_id'],'SAME',actor_id='reviewer',reason_code='REVIEWED',supersedes_decision_id=d['decision_id'])
        self.assertEqual(self.store.get_active_decision(c['candidate_id']),new)
    def test_manual_distinct_supersedes_same(self):
        run,a,b,c,d=self.evaluated_pair(); new=self.store.record_manual_decision(c['candidate_id'],'DISTINCT',actor_id='reviewer',reason_code='REVIEWED',supersedes_decision_id=d['decision_id']); self.assertEqual(new['decision_type'],'DISTINCT')
    def test_supersession_explicit(self):
        run,a,b,c,d=self.evaluated_pair()
        with self.assertRaises(DecisionSupersessionError): self.store.record_manual_decision(c['candidate_id'],'SAME',actor_id='reviewer',reason_code='REVIEWED')
    def test_wrong_pair_supersession(self):
        run,a,b,c,d=self.evaluated_pair(); other,_,_,c2=self.pair()
        with self.assertRaises(DecisionSupersessionError): self.store.record_manual_decision(c2['candidate_id'],'SAME',actor_id='reviewer',reason_code='REVIEWED',supersedes_decision_id=d['decision_id'])
    def test_supersession_no_fork_old_retained(self):
        run,a,b,c,d=self.evaluated_pair(); new=self.store.record_manual_decision(c['candidate_id'],'DISTINCT',actor_id='reviewer',reason_code='REVIEWED',supersedes_decision_id=d['decision_id'])
        with self.assertRaises(DecisionSupersessionError): self.store.record_manual_decision(c['candidate_id'],'SAME',actor_id='reviewer',reason_code='REVIEWED',supersedes_decision_id=d['decision_id'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM entity_resolution_decision WHERE resolution_run_id=?',(run,)).fetchone()[0],2)
        self.assertEqual(self.store.list_active_decisions(run),[new])
    def test_cross_run_supersession_and_old_seal_verifies(self):
        run,a,b,c,d=self.evaluated_pair(); self.store.materialize_resolution_run(run); self.store.seal_resolution_run(run)
        newrun=self.run_for([a,b]); nc=self.store.generate_candidates(newrun)[0]
        self.store.record_manual_decision(nc['candidate_id'],'DISTINCT',actor_id='reviewer',reason_code='CORRECTION',supersedes_decision_id=d['decision_id'])
        self.assertIsNone(self.store.get_active_decision(c['candidate_id'])); self.assertTrue(self.store.verify_resolution_run(run))
    def test_decision_post_materialization_service(self):
        run,a,b,c,d=self.evaluated_pair(); self.store.materialize_resolution_run(run)
        with self.assertRaises(ResolutionStateError): self.store.record_manual_decision(c['candidate_id'],'DISTINCT',actor_id='reviewer',reason_code='REVIEW',supersedes_decision_id=d['decision_id'])
    def test_decision_post_materialization_direct(self):
        run,a,b,c,d=self.evaluated_pair(); self.store.materialize_resolution_run(run)
        bad=dict(d,decision_id='dec_late',decision_type='DISTINCT',decision_mode='MANUAL',actor_id='reviewer',supersedes_decision_id=d['decision_id'])
        with self.assertRaises(sqlite3.IntegrityError): self.raw_insert('entity_resolution_decision',bad)
    def test_queue_unresolved(self):
        run,a,b,c=self.pair(); self.assertEqual(self.store.list_review_queue(run)[0]['review_status'],'UNRESOLVED')
    def test_queue_auto_same(self):
        run,a,b,c,d=self.evaluated_pair(); self.assertEqual(self.store.list_review_queue(run)[0]['review_status'],'AUTO_MATCHED')
    def test_queue_needs_review(self):
        run,a,b,c,d=self.evaluated_pair(left={'dois':[]},right={'dois':[]}); self.assertEqual(self.store.list_review_queue(run)[0]['review_status'],'NEEDS_REVIEW')
    def test_queue_manual_types(self):
        for outcome,status in [('SAME','MANUAL_MATCHED'),('DISTINCT','MANUAL_DISTINCT'),('AMBIGUOUS','NEEDS_REVIEW')]:
            run,a,b,c=self.pair(); self.store.record_manual_decision(c['candidate_id'],outcome,actor_id='reviewer',reason_code='REVIEWED'); self.assertEqual(self.store.list_review_queue(run)[0]['review_status'],status)
    def test_queue_is_view(self): self.assertEqual(self.db.execute("SELECT type FROM sqlite_master WHERE name='resolution_review_queue'").fetchone()[0],'view')
    def test_complete_memberships(self):
        run,a,b=self.materialized(); self.assertEqual({m['provider_record_id'] for m in self.store.list_entity_members(run)},{a,b})
    def test_singleton_entity(self):
        a=self.record(); run=self.run_for([a]); self.store.generate_candidates(run); entities=self.store.materialize_resolution_run(run); self.assertEqual(entities[0]['member_count'],1)
    def test_same_two_member_entity(self):
        run,a,b=self.materialized(); self.assertEqual([e['member_count'] for e in self.store.list_publication_entities(run)],[2])
    def test_entity_identity_hash(self):
        entity,h=entity_identity(['rec_b','rec_a']); self.assertEqual(h,digest(['rec_a','rec_b'])); self.assertEqual(entity,'pub_'+h[:32])
    def test_entity_identity_member_order(self): self.assertEqual(entity_identity(['b','a']),entity_identity(['a','b']))
    def test_entity_identity_same_across_runs(self):
        run,a,b=self.materialized(); other=self.run_for([b,a]); self.store.generate_candidates(other); self.store.evaluate_candidates_automatically(other); self.store.materialize_resolution_run(other)
        self.assertEqual(self.store.list_publication_entities(run)[0]['entity_id'],self.store.list_publication_entities(other)[0]['entity_id'])
    def test_single_membership_sql_constraint(self):
        rid=self.record(); run=self.run_for([rid]); self.store.generate_candidates(run)
        with self.db.transaction(),self.store._authorize(run,'MATERIALIZE'):
            for entity in ['pub_one','pub_two']:
                self.raw_insert('publication_entity',dict(resolution_run_id=run,entity_id=entity,membership_sha256='a'*64,member_count=1,created_at_utc='2026-01-01T00:00:00Z'))
            row=dict(resolution_run_id=run,entity_id='pub_one',provider_record_id=rid,membership_basis_json='{}',created_at_utc='2026-01-01T00:00:00Z'); self.raw_insert('entity_membership',row)
            with self.assertRaises(sqlite3.IntegrityError): self.raw_insert('entity_membership',dict(row,entity_id='pub_two'))
    def test_membership_replace_external_off(self):
        run,a,b=self.materialized()
        row=self.store.list_entity_members(run)[0]
        with closing(sqlite3.connect(self.db.path,isolation_level=None)) as raw:
            raw.execute('PRAGMA recursive_triggers=OFF')
            with self.assertRaises(sqlite3.DatabaseError): self.raw_insert('entity_membership',row,True,raw)
    def test_materialization_failure_atomic(self):
        run,a,b,c,d=self.evaluated_pair(); original=self.store._insert
        def fail(table,row):
            if table=='entity_membership': raise RuntimeError('synthetic failure')
            return original(table,row)
        with patch.object(self.store,'_insert',side_effect=fail),self.assertRaises(RuntimeError): self.store.materialize_resolution_run(run)
        self.assertEqual(self.store.list_publication_entities(run),[]); self.assertIsNone(self.store.get_resolution_run(run)['materialized_at_utc'])
    def test_nested_materialization_failure_atomic(self):
        run,a,b,c,d=self.evaluated_pair(); original=self.store._insert
        def fail(table,row):
            if table=='entity_membership': raise RuntimeError('synthetic failure')
            return original(table,row)
        with self.db.transaction():
            with patch.object(self.store,'_insert',side_effect=fail),self.assertRaises(RuntimeError): self.store.materialize_resolution_run(run)
        self.assertEqual(self.store.list_publication_entities(run),[])
    def test_input_manifest_order_independent(self):
        a,b=self.record(),self.record(); hashes=[]
        for records in [[a,b],[b,a]]:
            run=self.run_for(records); self.store.generate_candidates(run); self.store.materialize_resolution_run(run); hashes.append(self.store.get_resolution_run(run)['input_manifest_sha256'])
        self.assertEqual(hashes[0],hashes[1])
    def test_material_hash_excludes_timestamps(self):
        run,a,b,c,d=self.evaluated_pair(); row=self.store.get_resolution_run(run)
        x=self.store._projection(row,[d])[2]; changed=dict(d,created_at_utc='2000-01-01T00:00:00Z'); self.assertEqual(x,self.store._projection(row,[changed])[2])
    def _transitive(self,last):
        ids=[self.record(title='Same candidate title') for _ in range(3)]; run=self.run_for(ids); cs=self.store.generate_candidates(run)
        pair_last=tuple(sorted((ids[0],ids[2])))
        for c in cs:
            pair=(c['left_provider_record_id'],c['right_provider_record_id']); outcome=last if pair==pair_last else 'SAME'
            self.store.record_manual_decision(c['candidate_id'],outcome,actor_id='reviewer',reason_code='SYNTHETIC')
        return run
    def test_transitive_same(self):
        run=self._transitive('SAME'); self.assertEqual(self.store.materialize_resolution_run(run)[0]['member_count'],3)
    def test_transitive_distinct_conflict(self):
        run=self._transitive('DISTINCT')
        with self.assertRaises(ResolutionConflictError): self.store.materialize_resolution_run(run)
        self.assertEqual(self.store.list_publication_entities(run),[])
    def test_transitive_ambiguous_conflict(self):
        run=self._transitive('AMBIGUOUS')
        with self.assertRaises(ResolutionConflictError): self.store.materialize_resolution_run(run)
        self.assertEqual(self.store.list_entity_members(run),[])
    def test_materialization_requires_generation(self):
        with self.assertRaises(ResolutionConflictError): self.store.materialize_resolution_run(self.run_for([self.record()]))
    def test_materialization_requires_decisions(self):
        run,a,b,c=self.pair()
        with self.assertRaises(ResolutionConflictError): self.store.materialize_resolution_run(run)
    def test_relation_before_materialization(self):
        with self.assertRaises(ResolutionStateError): self.store.add_publication_relation(self.run_for(),'pub_a','pub_b','PREPRINT_OF',actor_id='reviewer',reason_code='REVIEW')
    def test_relation_after_seal_rejected(self):
        run,a,b=self.materialized(False); self.store.seal_resolution_run(run); es=self.store.list_publication_entities(run)
        with self.assertRaises(ResolutionStateError): self.store.add_publication_relation(run,es[0]['entity_id'],es[1]['entity_id'],'PREPRINT_OF',actor_id='reviewer',reason_code='REVIEW')
    def test_relation_same_entity_rejected(self):
        run,a,b=self.materialized()
        with self.assertRaises(ResolutionConflictError): self.store.add_publication_relation(run,'pub_a','pub_a','PREPRINT_OF',actor_id='reviewer',reason_code='REVIEW')
    def test_relation_cross_run_entities_rejected(self):
        run,a,b=self.materialized(False); run2,x,y=self.materialized(False)
        e1=self.store.list_publication_entities(run)[0]['entity_id']; e2=self.store.list_publication_entities(run2)[0]['entity_id']
        with self.assertRaises(ValidationError): self.store.add_publication_relation(run,e1,e2,'PREPRINT_OF',actor_id='reviewer',reason_code='REVIEW')
    def test_same_publication_relation_forbidden(self):
        run,a,b=self.materialized(False)
        with self.assertRaises(ValidationError): self.store.add_publication_relation(run,'a','b','SAME_PUBLICATION',actor_id='reviewer',reason_code='REVIEW')
    def relation(self,kind='PREPRINT_OF'):
        run,a,b=self.materialized(False); es=self.store.list_publication_entities(run)
        r=self.store.add_publication_relation(run,es[0]['entity_id'],es[1]['entity_id'],kind,actor_id='reviewer',reason_code='SYNTHETIC')
        return run,r
    def test_relation_preprint_accepted(self):
        run,r=self.relation(); self.assertEqual(self.store.get_active_relation(r['relation_id']),r)
    def test_relation_extension_accepted(self):
        run,r=self.relation('EXTENDED_VERSION_OF'); self.assertEqual(r['relation_type'],'EXTENDED_VERSION_OF')
    def test_relation_no_update_delete(self):
        self.relation()
        for sql in ["UPDATE publication_relation SET note='changed'",'DELETE FROM publication_relation']:
            with self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute(sql)
    def test_relation_no_replace_external_off(self):
        run,row=self.relation(); row['note']='Changed'
        with closing(sqlite3.connect(self.db.path,isolation_level=None)) as raw:
            raw.execute('PRAGMA recursive_triggers=OFF')
            with self.assertRaises(sqlite3.IntegrityError): self.raw_insert('publication_relation',row,True,raw)
    def test_relation_supersession(self):
        run,old=self.relation(); new=self.store.add_publication_relation(run,old['source_entity_id'],old['target_entity_id'],'POSSIBLY_RELATED_VERSION',actor_id='reviewer',reason_code='REVIEWED',supersedes_relation_id=old['relation_id'])
        self.assertIsNone(self.store.get_active_relation(old['relation_id'])); self.assertEqual(self.store.list_active_relations(run),[new])
    def test_relation_no_automatic_inference(self):
        run,a,b=self.materialized(False); self.assertEqual(self.store.list_active_relations(run),[])
        with self.assertRaises(ValidationError): self.store.add_publication_relation(run,'a','b','PREPRINT_OF',decision_mode='AUTOMATIC',reason_code='FORBIDDEN')
    def test_materialized_run_seals(self):
        run,a,b=self.materialized(); self.assertIsNotNone(self.store.seal_resolution_run(run)['sealed_at_utc'])
    def test_sealed_manifest_verifies(self):
        run,r=self.relation(); self.store.seal_resolution_run(run); self.assertTrue(self.store.verify_resolution_run(run))
    def test_tampered_materialization_fails_verification(self):
        run,a,b=self.materialized(); self.db.connection.execute('DROP TRIGGER resolution_run_transition')
        self.db.connection.execute('UPDATE resolution_run SET materialization_manifest_sha256=? WHERE resolution_run_id=?',('0'*64,run))
        with self.assertRaises(ResolutionConflictError): self.store.verify_resolution_run(run)
    def test_second_seal_rejected(self):
        run,a,b=self.materialized(); self.store.seal_resolution_run(run)
        with self.assertRaises(ResolutionStateError): self.store.seal_resolution_run(run)
    def test_repeated_auto_and_generate_idempotent(self):
        run,a,b,c,d=self.evaluated_pair(); self.assertEqual(self.store.generate_candidates(run),[c]); self.assertEqual(self.store.evaluate_candidates_automatically(run),[d])
    def test_scope_no_deferred_tables(self):
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse({'venue_entity','author_entity','work_family','discovery_run','discovery_edge','screening_run'} & tables)
    def test_scope_no_network_ai_imports(self):
        for p in (Path(__file__).parents[1]/'reprovane/resolution').glob('*.py'):
            for node in ast.walk(ast.parse(p.read_text(encoding='utf-8'))):
                if isinstance(node,ast.Import): self.assertFalse({n.name.split('.')[0] for n in node.names}&{'requests','openai','sqlalchemy','numpy'})
    def test_errors_no_raw_metadata(self):
        records=[self.record(title='synthetic-sensitive-title') for _ in range(3)]; run=self.run_for(records,policy=ResolverPolicy(2))
        with self.assertRaises(CandidateBlockTooLargeError) as caught: self.store.generate_candidates(run)
        self.assertNotIn('synthetic-sensitive-title',str(caught.exception))
    def test_no_destructive_observation_changes(self):
        run,a,b,c,d=self.evaluated_pair(); before=self.db.execute('SELECT count(*) FROM publication_metadata_observation').fetchone()[0]
        self.store.materialize_resolution_run(run); self.assertEqual(self.db.execute('SELECT count(*) FROM provider_record').fetchone()[0],2); self.assertEqual(self.db.execute('SELECT count(*) FROM publication_metadata_observation').fetchone()[0],before)

    def test_candidate_pair_canonical_order(self):
        run,a,b,c=self.pair(); self.assertEqual((c['left_provider_record_id'],c['right_provider_record_id']),tuple(sorted([a,b])))
    def test_blocking_never_expands_global_record_set(self):
        from itertools import combinations
        records=[self.record(title='Unique '+str(i),year=2000+i) for i in range(15)]; sizes=[]
        def tracked(values,count):
            sizes.append(len(values)); return combinations(values,count)
        with patch('reprovane.resolution.resolver.combinations',side_effect=tracked): self.store.generate_candidates(self.run_for(records))
        self.assertTrue(sizes); self.assertEqual(max(sizes),1)
    def test_invalid_doi_observation_ids_retained(self):
        rid=self.record(dois=['10.example/invalid']); run=self.run_for([rid]); proof=json.loads(self.store.list_resolution_inputs(run)[0]['evidence_json'])
        self.assertEqual(proof['valid_dois'],[]); self.assertEqual(len(proof['invalid_or_unusable_doi_observation_ids']),1)
    def test_blank_native_id_is_not_strong_evidence(self):
        a=self.record(provider='shared',native='  '); b=self.record(provider='shared',native='  ')
        self.assertEqual(self.store.generate_candidates(self.run_for([a,b])),[])
    def test_frozen_input_does_not_adopt_later_observations(self):
        a=self.record(title='Original'); run=self.run_for([a]); before=self.store.list_resolution_inputs(run)
        self.obs.add_publication_metadata_observation(a,self.norm,title='Later',publication_year=2025)
        self.assertEqual(self.store.list_resolution_inputs(run),before)
    def test_decision_replace_recursive_on(self):
        run,a,b,c,d=self.evaluated_pair()
        with self.assertRaises(sqlite3.IntegrityError): self.raw_insert('entity_resolution_decision',d,True)
    def test_candidate_update_delete_rejected(self):
        self.pair()
        for sql in ["UPDATE resolution_candidate SET created_at_utc=created_at_utc",'DELETE FROM resolution_candidate']:
            with self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute(sql)
    def test_entities_memberships_immutable(self):
        run,a,b=self.materialized()
        for table in ['publication_entity','entity_membership']:
            for sql in ['UPDATE '+table+' SET created_at_utc=created_at_utc','DELETE FROM '+table]:
                with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute(sql)
    def test_run_delete_and_replace_rejected(self):
        run=self.run_for(); row=self.store.get_resolution_run(run)
        with self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute('DELETE FROM resolution_run')
        with self.assertRaises(sqlite3.IntegrityError): self.raw_insert('resolution_run',row,True)
    def test_manual_actor_reason_required(self):
        run,a,b,c=self.pair()
        for actor,reason in [('', 'REVIEW'),('reviewer','')]:
            with self.subTest(actor=actor,reason=reason),self.assertRaises(ValidationError): self.store.record_manual_decision(c['candidate_id'],'SAME',actor_id=actor,reason_code=reason)
    def test_cross_run_supersession_requires_prior_seal(self):
        run,a,b,c,d=self.evaluated_pair(); other=self.run_for([a,b]); nc=self.store.generate_candidates(other)[0]
        with self.assertRaises(DecisionSupersessionError): self.store.record_manual_decision(nc['candidate_id'],'SAME',actor_id='reviewer',reason_code='REVIEW',supersedes_decision_id=d['decision_id'])
    def test_relation_supersession_cannot_fork(self):
        run,old=self.relation(); args=(run,old['source_entity_id'],old['target_entity_id'],'EXTENDED_VERSION_OF')
        self.store.add_publication_relation(*args,actor_id='reviewer',reason_code='ONE',supersedes_relation_id=old['relation_id'])
        with self.assertRaises(ValidationError): self.store.add_publication_relation(*args,actor_id='reviewer',reason_code='TWO',supersedes_relation_id=old['relation_id'])
    def test_tampered_membership_basis_fails_verification(self):
        run,a,b=self.materialized(); self.db.connection.execute('DROP TRIGGER entity_membership_no_update')
        self.db.connection.execute("UPDATE entity_membership SET membership_basis_json='{}'")
        with self.assertRaises(ResolutionConflictError): self.store.verify_resolution_run(run)
    def test_tampered_sealed_relation_fails_verification(self):
        run,row=self.relation(); self.store.seal_resolution_run(run); self.db.connection.execute('DROP TRIGGER publication_relation_no_update')
        self.db.connection.execute("UPDATE publication_relation SET note='changed'")
        with self.assertRaises(ResolutionConflictError): self.store.verify_resolution_run(run)
    def test_sealed_run_lifecycle_locked(self):
        run,a,b=self.materialized(); self.store.seal_resolution_run(run)
        with self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute("UPDATE resolution_run SET sealed_at_utc='2026-01-01T00:00:00Z' WHERE resolution_run_id=?",(run,))
    def test_requirements_unchanged(self):
        self.assertEqual((Path(__file__).parents[1]/'requirements.txt').read_text().strip(),'requests>=2.31,<3')


if __name__=='__main__': unittest.main()
