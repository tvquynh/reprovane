import json,hashlib,sqlite3,ast
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from venue_fixtures import VenueCase,issn
from scholarflow.core import ValidationError,MigrationDriftError
from scholarflow.core.db import Database
from scholarflow.core.migrations import MIGRATION_DIR
from scholarflow.observations import ObservationStore
from scholarflow.resolution import ResolutionStore
from scholarflow.venue_resolution import VenueCandidateBlockTooLargeError,ResolutionConflictError,ResolutionStateError,DecisionSupersessionError
from scholarflow.venue_resolution.normalization import normalize_issn,title_exact,title_normalized,title_status_normalized
from scholarflow.venue_resolution.policy import VenuePolicy
from scholarflow.venue_resolution.resolver import node_identity,entity_identity


class TestVenueResolution(VenueCase):
    def test_schema5(self): self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],5)
    def test_ledger(self): self.assertEqual([r[0] for r in self.db.execute('SELECT version FROM schema_migration ORDER BY version')],[1,2,3,4,5])
    def test_prior_hashes(self):
        expected=['6f5779b2073b4cdeb4bc8a70b8fbcf3f5892348eeedf74d35fa66430ba0e6b8e','7356fa1ddf9881c53873626b43a8f54132451e1d86aff53b15c4ae556326dd26','6c84d9e10203593caf4a47ee84e3e03bcc9e0454a101f6661abed8e58c722c4b','7db707be3591ffc61c868d18356e27a68836f5d6ff723a495b3716ff08f430d7']
        for p,want in zip(sorted(MIGRATION_DIR.glob('*.sql')),expected): self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),want)
    def test_old_feature_targets(self):
        with Database(self.root/'old') as d:
            self.assertEqual(d.execute('PRAGMA user_version').fetchone()[0],1); ObservationStore(d); self.assertEqual(d.execute('PRAGMA user_version').fetchone()[0],3); ResolutionStore(d); self.assertEqual(d.execute('PRAGMA user_version').fetchone()[0],4)
    def test_migration5_drift(self):
        folder=self.root/'drift'; folder.mkdir()
        for p in MIGRATION_DIR.glob('*.sql'): (folder/p.name).write_bytes(p.read_bytes()+(b'\n-- drift' if p.name.startswith('0005') else b''))
        with self.assertRaises(MigrationDriftError): Database(self.db.workspace,migration_dir=folder,target_schema_version=5)
    def test_open_input_rejected(self):
        with self.assertRaises(ValidationError): self.store.add_venue_resolution_input(self.run_for(),self.record(sealed=False))
    def test_provider_grouping(self):
        a=self.record(provider='scimago',native='100',title='Old'); b=self.record(provider='scimago',native='100',title='New')
        rid=self.run_for([b,a]); nodes=self.store.generate_provider_nodes(rid); self.assertEqual(len(nodes),1); self.assertEqual(nodes[0]['member_count'],2)
    def test_native_namespace_scoped(self):
        rid=self.run_for([self.record(provider='a',native='1'),self.record(provider='b',native='1')]); self.assertEqual(len(self.store.generate_provider_nodes(rid)),2)
    def test_different_native_nodes(self):
        rid=self.run_for([self.record(provider='a',native='1'),self.record(provider='a',native='2')]); self.assertEqual(len(self.store.generate_provider_nodes(rid)),2)
    def test_blank_native_singletons(self):
        rid=self.run_for([self.record(provider='a',native=' '),self.record(provider='a',native=' ')]); self.assertEqual(len(self.store.generate_provider_nodes(rid)),2)
    def test_node_membership_complete(self):
        records=[self.record(native=str(i)) for i in range(3)]; rid=self.run_for(records); self.store.generate_provider_nodes(rid); self.assertEqual({r['provider_record_id'] for r in self.store.list_provider_node_members(rid)},set(records))
    def test_node_identity_order(self): self.assertEqual(node_identity('p','1',['a','b']),node_identity('p','1',['b','a']))
    def test_input_lock_after_nodes(self):
        rid=self.run_for([self.record()]); self.store.generate_provider_nodes(rid)
        with self.assertRaises(ResolutionStateError): self.store.add_venue_resolution_input(rid,self.record())
    def test_node_update_delete_replace(self):
        rid=self.run_for([self.record()]); row=self.store.generate_provider_nodes(rid)[0]
        for sql in ["UPDATE venue_provider_node SET member_count=3",'DELETE FROM venue_provider_node']:
            with self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute(sql)
        with self.assertRaises(sqlite3.DatabaseError): self.raw_insert('venue_provider_node',row,True)
    def test_issn_normalization(self):
        self.assertEqual(normalize_issn(' 0378-5955 '),'03785955'); self.assertIsNone(normalize_issn('1234-5678')); self.assertIsNone(normalize_issn('malformed'))
    def test_x_and_punctuation(self):
        value=next(issn(str(i).zfill(7)) for i in range(100) if issn(str(i).zfill(7)).endswith('X'))
        self.assertEqual(normalize_issn(value.lower()[:4]+'-'+value.lower()[4:]),value)
    def test_title_methods(self):
        self.assertNotEqual(title_exact('A & B'),title_exact('A and B')); self.assertEqual(title_normalized('Á & B!'),'a and b')
        self.assertEqual(title_status_normalized('Alpha (Discontinued)'),'alpha'); self.assertEqual(title_status_normalized('Alpha (Series B)'),'alpha series b')
    def pair(self,**kwargs):
        a=self.record(**kwargs.pop('left',{})); b=self.record(**kwargs.pop('right',{})); rid=self.ready([a,b]); return rid,self.store.list_candidates(rid)[0]
    def test_identifier_precedence(self):
        rid,c=self.pair(left={'identifier':issn()},right={'identifier':issn()}); self.assertEqual(c['primary_method'],'ISSN_EXACT'); self.assertEqual(self.store.list_active_decisions(rid)[0]['decision_type'],'SAME')
    def test_linking_issn(self):
        rid,c=self.pair(left={'identifier':issn(),'namespace':'issn_l','title':'Different'},right={'identifier':issn()}); self.assertEqual(c['primary_method'],'ISSNL_EXACT')
    def test_title_fallback_variants(self):
        for left,right,method in [('Alpha',' ALPHA ','TITLE_EXACT'),('A & B','A and B','TITLE_NORMALIZED'),('Alpha','Alpha (ceased)','TITLE_STATUS_NORMALIZED')]:
            rid,c=self.pair(left={'title':left},right={'title':right}); self.assertEqual(c['primary_method'],method); self.assertEqual(self.store.list_active_decisions(rid)[0]['decision_type'],'SAME')
    def test_title_issn_conflict(self):
        rid,c=self.pair(left={'identifier':issn()},right={'identifier':issn('7654321')}); self.assertIn('TITLE_MATCH_ISSN_CONFLICT',json.loads(c['hard_conflicts_json'])); self.assertEqual(self.store.list_active_decisions(rid)[0]['decision_type'],'AMBIGUOUS')
    def test_missing_issn_title_eligible(self):
        rid,c=self.pair(left={'identifier':issn()}); self.assertEqual(self.store.list_active_decisions(rid)[0]['decision_type'],'SAME')
    def test_issn_beats_different_title(self):
        rid,c=self.pair(left={'identifier':issn(),'title':'Old name'},right={'identifier':issn(),'title':'New name'}); self.assertEqual(self.store.list_active_decisions(rid)[0]['decision_type'],'SAME')
    def test_same_provider_distinct_blocks(self):
        rid,c=self.pair(left={'provider':'a','native':'1'},right={'provider':'a','native':'2'}); self.assertIn('DISTINCT_PROVIDER_NATIVE_ID_SAME_NAMESPACE',json.loads(c['hard_conflicts_json'])); self.assertEqual(self.store.list_active_decisions(rid)[0]['decision_type'],'AMBIGUOUS')
    def test_block_ambiguity(self):
        rid=self.ready([self.record() for _ in range(3)]); self.assertEqual(len(self.store.list_candidates(rid)),3); self.assertTrue(all(d['decision_type']=='AMBIGUOUS' for d in self.store.list_active_decisions(rid)))
    def test_large_block_atomic(self):
        rid=self.run_for([self.record() for _ in range(4)],policy=VenuePolicy(3)); self.store.generate_provider_nodes(rid)
        with self.assertRaises(VenueCandidateBlockTooLargeError): self.store.generate_candidates(rid)
        self.assertEqual(self.store.list_candidates(rid),[])
    def test_invalid_issn_retained(self):
        rid=self.run_for([self.record(identifier='1234-5678')]); proof=json.loads(self.store.list_venue_resolution_inputs(rid)[0]['evidence_json']); self.assertTrue(proof['invalid_issn_observation_ids']); self.assertEqual(proof['valid_issn_family_union'],[])
    def test_methods_sorted_unique(self):
        rid,c=self.pair(left={'identifier':issn()},right={'identifier':issn()}); methods=json.loads(c['candidate_methods_json']); self.assertEqual(methods,sorted(set(methods))); self.assertEqual(len(self.store.list_candidates(rid)),1)
    def test_decision_immutability(self):
        rid,c=self.pair(); d=self.store.list_active_decisions(rid)[0]
        for sql in ["UPDATE venue_resolution_decision SET note='changed'",'DELETE FROM venue_resolution_decision']:
            with self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute(sql)
        with self.assertRaises(sqlite3.IntegrityError): self.raw_insert('venue_resolution_decision',d,True)
    def test_decision_replace_external(self):
        rid,c=self.pair(); d=self.store.list_active_decisions(rid)[0]; other,c2=self.pair()
        # Undecided second candidate allows testing the replacement identity guard itself.
        rawrun=self.run_for([self.record(title='Other'),self.record(title='Other')]); self.store.generate_provider_nodes(rawrun); c2=self.store.generate_candidates(rawrun)[0]
        bad=dict(d,venue_resolution_run_id=rawrun,candidate_id=c2['candidate_id'],evidence_json=c2['evidence_json'],hard_conflicts_json=c2['hard_conflicts_json'],decision_mode='MANUAL',actor_id='reviewer')
        with closing(sqlite3.connect(self.db.path,isolation_level=None)) as raw:
            raw.execute('PRAGMA recursive_triggers=OFF')
            with self.assertRaises(sqlite3.IntegrityError): self.raw_insert('venue_resolution_decision',bad,True,raw)
    def test_manual_supersession_queue(self):
        rid,c=self.pair(); old=self.store.list_active_decisions(rid)[0]
        new=self.store.record_manual_decision(c['candidate_id'],'DISTINCT',actor_id='reviewer',reason_code='REVIEW',supersedes_decision_id=old['decision_id']); self.assertEqual(self.store.list_review_queue(rid)[0]['review_status'],'MANUAL_DISTINCT')
        self.assertEqual(self.store.get_active_decision(c['candidate_id']),new)
        with self.assertRaises(DecisionSupersessionError): self.store.record_manual_decision(c['candidate_id'],'SAME',actor_id='reviewer',reason_code='REVIEW',supersedes_decision_id=old['decision_id'])
    def test_materialization_complete_seals(self):
        rid=self.ready([self.record(title='A'),self.record(title='B')],True); self.assertEqual(len(self.store.list_entity_members(rid)),2); self.store.seal_venue_resolution_run(rid); self.assertTrue(self.store.verify_venue_resolution_run(rid))
    def test_same_pair_entity(self):
        rid,c=self.pair(); self.assertEqual(self.store.materialize_venue_resolution_run(rid)[0]['provider_node_count'],2)
    def test_entity_identity_order(self): self.assertEqual(entity_identity(['b','a']),entity_identity(['a','b']))
    def test_same_entity_across_runs(self):
        records=[self.record(title='A'),self.record(title='B')]; a=self.ready(records,True); b=self.ready(list(reversed(records)),True); self.assertEqual([r['venue_entity_id'] for r in self.store.list_venue_entities(a)],[r['venue_entity_id'] for r in self.store.list_venue_entities(b)])
    def test_transitive_conflicts(self):
        for last in ['SAME','DISTINCT','AMBIGUOUS']:
            rid=self.ready([self.record() for _ in range(3)]); cs=self.store.list_candidates(rid)
            for i,c in enumerate(cs):
                d=self.store.get_active_decision(c['candidate_id']); self.store.record_manual_decision(c['candidate_id'],last if i==2 else 'SAME',actor_id='reviewer',reason_code='REVIEW',supersedes_decision_id=d['decision_id'])
            if last=='SAME': self.assertEqual(len(self.store.materialize_venue_resolution_run(rid)),1)
            else:
                with self.assertRaises(ResolutionConflictError): self.store.materialize_venue_resolution_run(rid)
                self.assertEqual(self.store.list_venue_entities(rid),[])
    def test_post_materialization_decision_locked(self):
        rid,c=self.pair(); self.store.materialize_venue_resolution_run(rid)
        with self.assertRaises(ResolutionStateError): self.store.record_manual_decision(c['candidate_id'],'SAME',actor_id='reviewer',reason_code='REVIEW')
    def test_relation_lifecycle_types(self):
        rid=self.ready([self.record(title='A'),self.record(title='B')],True); entities=self.store.list_venue_entities(rid); a,b=[e['venue_entity_id'] for e in entities]
        for kind in ['PREDECESSOR_OF','CONTINUATION_OF','MERGED_INTO','SPLIT_INTO','POSSIBLY_RELATED_VENUE']:
            relation=self.store.add_venue_relation(rid,a,b,kind,actor_id='reviewer',reason_code='REVIEW'); self.assertIsNotNone(self.store.get_active_relation(relation['relation_id']))
        self.store.seal_venue_resolution_run(rid)
        with self.assertRaises(ResolutionStateError): self.store.add_venue_relation(rid,a,b,'PREDECESSOR_OF',actor_id='reviewer',reason_code='REVIEW')
    def test_relation_supersession_immutability(self):
        rid=self.ready([self.record(title='A'),self.record(title='B')],True); a,b=[e['venue_entity_id'] for e in self.store.list_venue_entities(rid)]
        old=self.store.add_venue_relation(rid,a,b,'PREDECESSOR_OF',actor_id='reviewer',reason_code='REVIEW'); new=self.store.add_venue_relation(rid,a,b,'CONTINUATION_OF',actor_id='reviewer',reason_code='REVIEW',supersedes_relation_id=old['relation_id']); self.assertEqual(self.store.list_active_relations(rid),[new])
        for sql in ["UPDATE venue_relation SET note='changed'",'DELETE FROM venue_relation']:
            with self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute(sql)
    def test_single_membership_constraint(self):
        rid=self.run_for([self.record()]); node=self.store.generate_provider_nodes(rid)[0]; self.store.generate_candidates(rid)
        with self.db.transaction(),self.store._authorize(rid,'MATERIALIZE'):
            for e in ['ven_a','ven_b']: self.raw_insert('venue_entity',dict(venue_resolution_run_id=rid,venue_entity_id=e,membership_sha256='a'*64,provider_node_count=1,provider_record_count=1,created_at_utc='2026-01-01T00:00:00Z'))
            row=dict(venue_resolution_run_id=rid,venue_entity_id='ven_a',provider_node_id=node['provider_node_id'],membership_basis_json='{}',created_at_utc='2026-01-01T00:00:00Z'); self.raw_insert('venue_entity_membership',row)
            with self.assertRaises(sqlite3.IntegrityError): self.raw_insert('venue_entity_membership',dict(row,venue_entity_id='ven_b'))
    def test_publication_store_not_clobbered(self):
        from scholarflow.venue_resolution import VenueResolutionStore
        publication=ResolutionStore(self.db); VenueResolutionStore(self.db)
        run=publication.create_resolution_run(self.revision['revision_id'],{'profiles':[{'normalizer_name':'venue-fixture','normalizer_version':'1','observation_schema_version':'1'}]})
        publication.add_resolution_input(run['resolution_run_id'],self.record()); publication.generate_candidates(run['resolution_run_id'])
        self.assertEqual(len(publication.materialize_resolution_run(run['resolution_run_id'])),1)
    def test_materialization_rollback(self):
        rid,c=self.pair(); original=self.store._insert
        def fail(table,row):
            if table=='venue_entity_membership': raise RuntimeError('synthetic failure')
            return original(table,row)
        with patch.object(self.store,'_insert',side_effect=fail),self.assertRaises(RuntimeError): self.store.materialize_venue_resolution_run(rid)
        self.assertEqual(self.store.list_venue_entities(rid),[])
    def test_manual_same_overrides_ambiguity(self):
        rid,c=self.pair(left={'identifier':issn()},right={'identifier':issn('7654321')}); old=self.store.get_active_decision(c['candidate_id'])
        d=self.store.record_manual_decision(c['candidate_id'],'SAME',actor_id='reviewer',reason_code='REVIEWED',supersedes_decision_id=old['decision_id']); self.assertEqual(d['decision_type'],'SAME')
    def test_relation_before_materialization_same_entity(self):
        rid=self.run_for()
        with self.assertRaises(ResolutionStateError): self.store.add_venue_relation(rid,'a','b','PREDECESSOR_OF',actor_id='reviewer',reason_code='REVIEW')
        with self.assertRaises(ResolutionConflictError): self.store.add_venue_relation(rid,'a','a','PREDECESSOR_OF',actor_id='reviewer',reason_code='REVIEW')
