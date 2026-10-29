"""Stage11 membership, frozen provenance, portable integrity and offline contracts."""
import ast,contextlib,csv,io,json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from corpus_release_fixtures import CorpusCase,plan
from openalex_acquisition_fixtures import work
from screening_fixtures import plan as screening_plan
from scholarflow.corpus_release import CorpusReleaseStore,CorpusReleasePlan,verify_export
from scholarflow.corpus_release.model import digest,sha
from scholarflow.corpus_release.portable import render_files
from scholarflow.corpus_release.__main__ import main
from scholarflow.core import FoundationError,ValidationError,ArtifactIntegrityError,MigrationDriftError
from scholarflow.core.db import Database
from scholarflow.core.migrations import MIGRATION_DIR
from scholarflow.screening import ScreeningStore
from scholarflow.observations import NormalizationProvenance
from scholarflow.search_validation import SearchValidationStore


class TestCorpusReleasePlan(unittest.TestCase):
    def test_canonicalization(self):
        p=plan(); q=dict(reversed(list(p.items()))); q['metadata_fields']=list(reversed(p['metadata_fields'])); q['export_formats']=['CSV','JSONL']; self.assertEqual(CorpusReleasePlan.from_value(p).sha256,CorpusReleasePlan.from_value(q).sha256)
    def test_label_changes_plan_hash(self): self.assertNotEqual(CorpusReleasePlan.from_value(plan()).sha256,CorpusReleasePlan.from_value(plan(release_label='another-release')).sha256)
    def test_unsafe_label(self):
        for value in ['../secret','person@example.invalid','x'*65]:
            with self.subTest(value=value),self.assertRaises(ValidationError): CorpusReleasePlan.from_value(plan(release_label=value))
    def test_explicit_plan_required(self):
        p=plan(); del p['identifier_policy']
        with self.assertRaises(ValidationError): CorpusReleasePlan.from_value(p)
    def test_no_membership_rule_override(self):
        with self.assertRaises(ValidationError): CorpusReleasePlan.from_value(plan(year_range=[2020,2025]))
    def test_identity_policy_frozen(self):
        with self.assertRaises(ValidationError): CorpusReleasePlan.from_value(plan(identity_policy='FUZZY'))
    def test_lossless_export_required(self):
        with self.assertRaises(ValidationError): CorpusReleasePlan.from_value(plan(export_formats=['CSV']))
    def test_venue_policy_must_agree_with_fields(self):
        with self.assertRaises(ValidationError): CorpusReleasePlan.from_value(plan(venue_policy='OMIT'))
    def test_no_volatile_ordering(self):
        with self.assertRaises(ValidationError): CorpusReleasePlan.from_value(plan(ordering_policy='CITATION_COUNT'))


class TestCorpusCreation(CorpusCase):
    def test_schema11_idempotent(self):
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],11); CorpusReleaseStore(self.db); self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],11)
    def test_schema10_upgrade(self):
        with Database(self.root/'upgrade') as db:
            ScreeningStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10); CorpusReleaseStore(db); self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],11)
    def test_migration_drift(self):
        folder=self.root/'migrations'; folder.mkdir()
        for p in MIGRATION_DIR.glob('*.sql'): (folder/p.name).write_bytes(p.read_bytes())
        f=folder/'0011_immutable_corpus_release.sql'; f.write_bytes(b'-- drift\n'+f.read_bytes())
        with self.assertRaises(MigrationDriftError):
            with Database(self.db.workspace,migration_dir=folder,target_schema_version=11): pass
    def test_sealed_screening_accepted(self): self.assertEqual(self.release()['release']['state'],'SEALED')
    def test_open_screening_rejected(self):
        self.screening()
        with self.assertRaises(ValidationError): self.create_release()
    def test_review_complete_rejected(self):
        self.screening(); self.conflict()
        with self.assertRaises(ValidationError): self.create_release()
    def test_resolved_unsealed_rejected(self):
        self.screening(); self.unanimous()
        with self.assertRaises(ValidationError): self.create_release()
    def test_cross_revision_rejected(self):
        self.prepare(); revision=self.foundation_revision()
        with self.assertRaises(ValidationError): self.releases.create(revision,self.sid,plan())
    def foundation_revision(self): return self.store.foundation.create_project_revision(self.project['project_id'],2,'json','{}')['revision_id']
    def test_tampered_screening_rejected(self):
        self.prepare(); self.db.connection.execute('DROP TRIGGER screening_decision_update'); self.db.connection.execute("UPDATE screening_decision SET note='tampered'")
        with self.assertRaises(ArtifactIntegrityError): self.create_release()
        self.assertEqual(self.db.execute('SELECT count(*) FROM corpus_release').fetchone()[0],0)
    def test_zero_inclusion_valid(self):
        result=self.release(include_count=0); self.assertEqual(result['statistics']['corpus_item_count'],0); self.assertEqual(result['statistics']['screen_exclude_count'],3); self.assertEqual(self.releases.handoff(self.crid)['items'],[]); self.assertTrue(self.releases.export(self.crid,self.root/'empty')['valid'])
    def test_same_plan_reuses_same_release(self):
        first=self.release(); second=self.releases.create(self.revision,self.sid,plan()); self.assertEqual(first,second); self.assertEqual(self.db.execute('SELECT count(*) FROM corpus_release').fetchone()[0],1)
    def test_changed_label_new_immutable_release(self):
        self.release(); first=self.crid; other=self.releases.create(self.revision,self.sid,plan(release_label='corpus-v2')); self.assertNotEqual(other['release']['release_id'],first); self.releases.finalize(other['release']['release_id']); self.assertEqual(self.releases.get(first)['state'],'SEALED')
    def test_multiple_plan_projections_same_membership(self):
        self.release(); ids={r['publication_entity_id'] for r in self.releases.items(self.crid)}; other=self.releases.create(self.revision,self.sid,plan(oa_policy='OMIT')); rid=other['release']['release_id']; self.releases.finalize(rid); self.assertEqual(ids,{r['publication_entity_id'] for r in self.releases.items(rid)})


class TestCorpusMembership(CorpusCase):
    def test_exact_include_membership(self):
        result=self.release(); source=json.loads(result['release']['source_json']); self.assertEqual({i['publication_entity_id'] for i in self.releases.items(self.crid)},{r['publication_entity_id'] for r in source['screen_include_refs']})
    def test_exclusions_are_nonmembership_audit(self):
        result=self.release(); source=json.loads(result['release']['source_json']); excluded={r['publication_entity_id'] for r in source['screen_exclude_refs']}; self.assertTrue(all(r['kind']=='SCREENING_EXCLUSION_AUDIT' for r in source['screen_exclude_refs'])); self.assertFalse(excluded&{r['publication_entity_id'] for r in self.releases.items(self.crid)})
    def test_multiple_discovery_routes_not_duplicate(self):
        self.primary([work(1)]); self.primary([work(1)]); self.discovery.seal(self.rid); self.screening(); self.unanimous(); self.store.finalize(self.sid); self.create_release(); result=self.releases.finalize(self.crid); self.assertEqual(result['statistics']['corpus_item_count'],1); self.assertGreater(len(json.loads(self.releases.items(self.crid)[0]['discovery_provenance_json'])['membership_basis']['event_ids']),1)
    def test_item_count_reconciles(self):
        result=self.release(); self.assertEqual(result['statistics']['corpus_item_count'],result['statistics']['screen_include_count'])
    def test_membership_independent_of_metadata(self):
        result=self.release([work(1,doi=None,open_access=None,is_retracted=True,primary_location=None),work(2,doi=None,publication_year=1999,type='book',open_access=None,cited_by_count=0)],include_count=2); self.assertEqual(result['statistics']['corpus_item_count'],2); self.assertEqual(result['statistics']['items_without_doi'],2)
    def test_member_view_only_authoritative_after_seal(self):
        self.release(finalize=False); self.releases.materialize(self.crid); self.assertEqual(self.db.execute('SELECT count(*) FROM corpus_release_membership').fetchone()[0],0); self.releases.finalize(self.crid); self.assertEqual(self.db.execute('SELECT count(*) FROM corpus_release_membership').fetchone()[0],2)
    def test_unknown_entity_sql_insert_blocked(self):
        self.release(finalize=False); run=self.releases.get(self.crid); p,s=self.releases._context(run); item,refs=self.releases._expected_items(run,p,s)[0]; item['publication_entity_id']='unknown'; row=self.releases._row(item,refs)
        with self.releases.auth(self.crid,'MATERIALIZE'),self.assertRaises(FoundationError): self.store.foundation._insert('corpus_release_item',row)
    def test_duplicate_entity_sql_insert_blocked(self):
        self.release(finalize=False); run=self.releases.get(self.crid); p,s=self.releases._context(run); item,refs=self.releases._expected_items(run,p,s)[0]; row=self.releases._row(item,refs)
        with self.db.transaction(),self.releases.auth(self.crid,'MATERIALIZE'):
            self.store.foundation._insert('corpus_release_item',row)
            with self.assertRaises(FoundationError): self.store.foundation._insert('corpus_release_item',dict(row,corpus_item_id='different'))
    def test_excluded_item_sql_insert_blocked(self):
        self.release(finalize=False); run=self.releases.get(self.crid); p,s=self.releases._context(run); item,refs=self.releases._expected_items(run,p,s)[0]; exclusion=next(r for r in self.store.outcomes(self.sid) if r['outcome']=='SCREEN_EXCLUDE'); item.update(publication_entity_id=exclusion['publication_entity_id'],screening_candidate_id=exclusion['candidate_id']); row=self.releases._row(item,refs)
        with self.releases.auth(self.crid,'MATERIALIZE'),self.assertRaises(FoundationError): self.store.foundation._insert('corpus_release_item',row)


class TestCorpusSnapshots(CorpusCase):
    def test_identity_snapshot_context(self):
        self.release(); row=self.releases.items(self.crid)[0]; identity=json.loads(row['identity_json']); self.assertEqual(identity['publication_entity_id'],row['publication_entity_id']); self.assertEqual(identity['resolution_run_id'],row['resolution_run_id']); self.assertTrue(identity['members'][0]['membership_basis']); self.assertTrue(identity['members'][0]['provider_record_id'])
    def test_doi_and_native_ids_preserved(self):
        self.release(); identity=json.loads(self.releases.items(self.crid)[0]['identity_json']); self.assertTrue(identity['normalized_dois']); self.assertTrue(identity['openalex_work_ids']); self.assertTrue(identity['provider_identifiers'])
    def test_conflicting_doi_evidence_preserved_without_selection(self):
        a=self.primary([work(1)]); record=self.store.foundation.list_provider_records(a['snapshot_id'])[0]; self.discovery.observations.add_publication_identifier_observation(record['provider_record_id'],NormalizationProvenance('openalex-work','1','1'),identifier_namespace='doi',identifier_value_original='10.1234/second',identifier_value_normalized='10.1234/second'); self.discovery.seal(self.rid); self.screening(); self.unanimous(); self.store.finalize(self.sid); self.create_release(); self.releases.finalize(self.crid); identity=json.loads(self.releases.items(self.crid)[0]['identity_json']); self.assertEqual(identity['normalized_dois'],['10.1234/second','10.1234/test1'])
    def test_title_year_date_type_and_authors(self):
        self.release(); metadata=json.loads(self.releases.items(self.crid)[0]['metadata_json']); fields=metadata['records'][0]['fields']; self.assertTrue(fields['title']); self.assertEqual(fields['publication_year'][0]['value'],2024); self.assertEqual(fields['publication_date'][0]['value'],'2024-01-02'); self.assertEqual(fields['work_type'][0]['value'],'article'); self.assertEqual([r['author_position'] for r in fields['authors']],[1,2])
    def test_venue_provider_specific_description(self):
        self.release(); fields=json.loads(self.releases.items(self.crid)[0]['metadata_json'])['records'][0]['fields']; self.assertEqual(fields['venue'][0]['title'],'Synthetic Journal'); self.assertNotIn('indexed',fields)
    def test_missing_fields_preserved(self):
        self.release([work(1,title=None,doi=None,publication_date=None,publication_year=None,primary_location=None)],include_count=1); fields=json.loads(self.releases.items(self.crid)[0]['metadata_json'])['records'][0]['fields']; self.assertIsNone(fields['title']); self.assertIsNone(fields['venue']); self.assertIsNone(fields['doi'])
    def test_later_observation_never_mutates_sealed_item(self):
        self.release(); before=self.releases.items(self.crid); rid=json.loads(before[0]['identity_json'])['members'][0]['provider_record_id']; self.discovery.observations.add_venue_observation(rid,NormalizationProvenance('openalex-work','1','1'),title='Later description'); self.assertTrue(self.releases.verify(self.crid)); self.assertEqual(before,self.releases.items(self.crid))
    def test_later_integrity_observation_does_not_drop_member(self):
        self.release(); row=self.releases.items(self.crid)[0]; identity=json.loads(row['identity_json']); rid=identity['members'][0]['provider_record_id']; self.discovery.observations.add_integrity_event_observation(rid,NormalizationProvenance('openalex-work','1','1'),event_type='RETRACTION',subject_identifier_namespace='openalex_work_id',subject_identifier_value=identity['openalex_work_ids'][0]); self.assertTrue(self.releases.verify(self.crid)); self.assertEqual(len(self.releases.items(self.crid)),2)
    def test_release_specific_fields_from_final_resolution(self):
        self.screening([work(1)],protocol=screening_plan(allowed_evidence_fields=['title'])); self.unanimous(); self.store.finalize(self.sid); self.create_release(); self.releases.finalize(self.crid); item=self.releases.items(self.crid)[0]; fields=json.loads(item['metadata_json'])['records'][0]['fields']; self.assertTrue(fields['authors']); self.assertIsNone(fields['venue']); self.assertTrue(json.loads(item['identity_json'])['normalized_dois'])
    def test_auto_consensus_provenance_no_notes(self):
        self.release(); row=self.releases.items(self.crid)[0]; provenance=json.loads(row['screening_provenance_json']); self.assertEqual(provenance['resolution_basis'],'AUTO_CONSENSUS'); self.assertEqual(len(provenance['effective_decision_ids']),2); self.assertEqual(provenance['reason_codes'],[]); self.assertNotIn('note',provenance)
    def test_adjudicated_provenance(self):
        self.release(adjudicated=True); snapshots=[json.loads(r['screening_provenance_json']) for r in self.releases.items(self.crid)]; adjudicated=next(p for p in snapshots if p['resolution_basis']=='ADJUDICATED'); self.assertTrue(adjudicated['effective_adjudication_id']); self.assertEqual(adjudicated['screening_manifest_sha256'],self.store.get(self.sid)['finalization_sha256'])
    def test_screening_not_modified(self):
        self.prepare(); before=self.store.handoff(self.sid); self.create_release(); self.releases.finalize(self.crid); self.assertEqual(before,self.store.handoff(self.sid))
    def test_unresolved_discovery_carried_forward(self):
        self.release([work(1),work(2,doi=None,title='Ambiguous synthetic'),work(3,doi=None,title='Ambiguous synthetic')],include_count=1); source=json.loads(self.releases.get(self.crid)['source_json']); self.assertEqual(source['unresolved_discovery_summary']['provider_level_unresolved_count'],2); self.assertEqual(self.releases.inspect(self.crid)['statistics']['unresolved_discovery_evidence_count'],2)
    def test_explicit_search_audit_reference(self):
        self.prepare(); a=self.primary([work(9)],register=False); v=SearchValidationStore(self.db); vid=v.create(self.revision,[dict(label='A',acquisition_id=a['acquisition_id'])])['validation_run_id']; v.execute(vid); v.finalize(vid); self.create_release(plan(search_validation_refs=[vid])); self.releases.finalize(self.crid); source=json.loads(self.releases.get(self.crid)['source_json']); self.assertEqual(source['search_validation_audits'][0]['validation_run_id'],vid); self.assertEqual(len(self.releases.items(self.crid)),2)


class TestCorpusLifecycle(CorpusCase):
    def test_open_materialized_sealed(self):
        self.release(finalize=False); self.assertEqual(self.releases.get(self.crid)['state'],'OPEN'); self.releases.materialize(self.crid); self.assertEqual(self.releases.get(self.crid)['state'],'MATERIALIZED'); self.releases.finalize(self.crid); self.assertEqual(self.releases.get(self.crid)['state'],'SEALED')
    def test_materialization_idempotent(self):
        self.release(finalize=False); a=self.releases.materialize(self.crid); self.assertEqual(a,self.releases.materialize(self.crid)); self.assertEqual(len(self.releases.items(self.crid)),2)
    def test_finalize_idempotent(self):
        a=self.release(); self.assertEqual(a,self.releases.finalize(self.crid)); self.assertTrue(self.releases.verify(self.crid))
    def test_materialization_failure_atomic(self):
        self.release(finalize=False); original=self.releases.foundation._insert; count=[0]
        def broken(table,row):
            if table=='corpus_release_item':
                count[0]+=1
                if count[0]==2: raise RuntimeError('synthetic item fault')
            return original(table,row)
        with patch.object(self.releases.foundation,'_insert',side_effect=broken),self.assertRaises(RuntimeError): self.releases.materialize(self.crid)
        self.assertEqual(self.releases.items(self.crid),[]); self.assertEqual(self.releases.get(self.crid)['state'],'OPEN'); self.assertEqual(self.db.execute('SELECT count(*) FROM corpus_release_export').fetchone()[0],0)
    def test_export_manifest_registration_failure_atomic(self):
        self.release(finalize=False); original=self.releases.foundation._insert
        def broken(table,row):
            if table=='corpus_release_export': raise RuntimeError('synthetic export snapshot fault')
            return original(table,row)
        with patch.object(self.releases.foundation,'_insert',side_effect=broken),self.assertRaises(RuntimeError): self.releases.materialize(self.crid)
        self.assertEqual(self.releases.items(self.crid),[]); self.assertEqual(self.releases.get(self.crid)['state'],'OPEN')
    def test_failed_finalize_rolls_back_automatic_materialization(self):
        self.release(finalize=False)
        with patch.object(self.releases,'verify',side_effect=RuntimeError('synthetic verification fault')),self.assertRaises(RuntimeError): self.releases.finalize(self.crid)
        self.assertEqual(self.releases.get(self.crid)['state'],'OPEN'); self.assertEqual(self.releases.items(self.crid),[])
    def test_verify_requires_materialized(self):
        self.release(finalize=False)
        with self.assertRaises(ValidationError): self.releases.verify(self.crid)
    def test_sql_immutability_all_release_evidence(self):
        self.release(); connection=sqlite3.connect(self.db.path); connection.create_function('corpus_release_authorized',2,lambda *_:0); connection.create_function('foundation_sha256_text',1,lambda s:sha(s.encode())); connection.execute('PRAGMA recursive_triggers=OFF')
        try:
            for table in ['corpus_release','corpus_release_item','corpus_release_export']:
                column=connection.execute('PRAGMA table_info('+table+')').fetchone()[1]
                for sql in ['DELETE FROM '+table,'UPDATE '+table+' SET '+column+'='+column,'INSERT OR REPLACE INTO '+table+' SELECT * FROM '+table]:
                    with self.subTest(sql=sql),self.assertRaises(sqlite3.DatabaseError): connection.execute(sql)
                    connection.rollback()
        finally: connection.close()
    def test_post_seal_insert_rejected(self):
        self.release(); row=dict(self.releases.items(self.crid)[0],corpus_item_id='new',ordinal=20)
        with self.releases.auth(self.crid,'MATERIALIZE'),self.assertRaises(FoundationError): self.store.foundation._insert('corpus_release_item',row)


class TestCorpusTampering(CorpusCase):
    def setUp(self): super().setUp(); self.release()
    def test_identity_metadata_screening_tampering(self):
        self.db.connection.execute('DROP TRIGGER corpus_release_item_update')
        for field in ['identity','metadata','screening_provenance','discovery_provenance','oa_locations']:
            row=self.releases.items(self.crid)[0]; old=row[field+'_json']; changed='{}'
            self.db.connection.execute('UPDATE corpus_release_item SET '+field+'_json=?, '+field+'_sha256=? WHERE corpus_item_id=?',(changed,digest({}),row['corpus_item_id']))
            with self.subTest(field=field),self.assertRaises(ArtifactIntegrityError): self.releases.verify(self.crid)
            self.db.connection.execute('UPDATE corpus_release_item SET '+field+'_json=?, '+field+'_sha256=? WHERE corpus_item_id=?',(old,row[field+'_sha256'],row['corpus_item_id']))
    def test_deleted_item_detected(self):
        self.db.connection.execute('DROP TRIGGER corpus_release_item_delete'); self.db.connection.execute('DELETE FROM corpus_release_item WHERE corpus_item_id=?',(self.releases.items(self.crid)[0]['corpus_item_id'],))
        with self.assertRaises(ArtifactIntegrityError): self.releases.verify(self.crid)
    def test_inserted_exclusion_after_guard_bypass_detected(self):
        self.db.connection.execute('DROP TRIGGER corpus_release_item_insert'); excluded=next(r for r in self.store.outcomes(self.sid) if r['outcome']=='SCREEN_EXCLUDE'); row=dict(self.releases.items(self.crid)[0],corpus_item_id='injected',publication_entity_id=excluded['publication_entity_id'],screening_candidate_id=excluded['candidate_id'],ordinal=2); self.store.foundation._insert('corpus_release_item',row)
        with self.assertRaises(ArtifactIntegrityError): self.releases.verify(self.crid)
    def test_reordered_item_detected(self):
        self.db.connection.execute('DROP TRIGGER corpus_release_item_update'); row=self.releases.items(self.crid)[0]; self.db.connection.execute('UPDATE corpus_release_item SET ordinal=99 WHERE corpus_item_id=?',(row['corpus_item_id'],))
        with self.assertRaises(ArtifactIntegrityError): self.releases.verify(self.crid)
    def test_export_manifest_tampering(self):
        self.db.connection.execute('DROP TRIGGER corpus_release_export_update'); self.db.connection.execute("UPDATE corpus_release_export SET file_inventory_json='[]'")
        with self.assertRaises(ArtifactIntegrityError): self.releases.verify(self.crid)


class TestCorpusPortable(CorpusCase):
    def setUp(self): super().setUp(); self.release(); self.directory=self.root/'portable'; self.releases.export(self.crid,self.directory)
    def test_jsonl_count_and_hashes(self):
        rows=[json.loads(line) for line in (self.directory/'corpus.jsonl').read_text(encoding='utf-8').splitlines()]; self.assertEqual(len(rows),2); self.assertEqual(rows,[self.releases._portable(r) for r in self.releases.items(self.crid)])
    def test_csv_multivalue_json_cells(self):
        rows=list(csv.DictReader(io.StringIO((self.directory/'corpus.csv').read_text(encoding='utf-8')))); self.assertEqual(len(rows),2); self.assertIsInstance(json.loads(rows[0]['title']),list); self.assertIsInstance(json.loads(rows[0]['doi']),list)
    def test_same_export_byte_identical(self):
        before={p.name:p.read_bytes() for p in self.directory.iterdir()}; self.releases.export(self.crid,self.directory); self.assertEqual(before,{p.name:p.read_bytes() for p in self.directory.iterdir()})
    def test_other_directory_identical(self):
        other=self.root/'copy'; self.releases.export(self.crid,other); self.assertEqual({p.name:p.read_bytes() for p in self.directory.iterdir()},{p.name:p.read_bytes() for p in other.iterdir()})
    def test_changed_existing_bytes_refused(self):
        path=self.directory/'corpus.csv'; path.write_bytes(b'user-edited')
        with self.assertRaises(ValidationError): self.releases.export(self.crid,self.directory)
        self.assertEqual(path.read_bytes(),b'user-edited')
    def test_extra_file_refused(self):
        (self.directory/'user-notes.txt').write_text('preserve',encoding='utf-8')
        with self.assertRaises(ValidationError): self.releases.export(self.crid,self.directory)
        with self.assertRaises(ArtifactIntegrityError): verify_export(self.directory)
    def test_missing_file_detected(self):
        (self.directory/'corpus.csv').unlink()
        with self.assertRaises(ArtifactIntegrityError): verify_export(self.directory)
    def test_partial_matching_export_can_resume(self):
        (self.directory/'corpus.csv').unlink(); self.assertTrue(self.releases.export(self.crid,self.directory)['valid'])
    def test_changed_file_detected(self):
        with (self.directory/'corpus.jsonl').open('ab') as f: f.write(b'\n')
        with self.assertRaises(ArtifactIntegrityError): verify_export(self.directory)
    def test_manifest_hash_bound_when_requested(self):
        with self.assertRaises(ArtifactIntegrityError): verify_export(self.directory,expected_manifest_sha256='0'*64)
    def test_portable_verification_without_db(self):
        with patch('scholarflow.core.db.Database',side_effect=AssertionError('DB unavailable')): self.assertTrue(verify_export(self.directory)['valid'])
    def test_modified_data_even_rehashed_checksums_detected(self):
        path=self.directory/'corpus.jsonl'; lines=path.read_text(encoding='utf-8').splitlines(); lines.pop(); path.write_text('\n'.join(lines)+'\n',encoding='utf-8'); checks=''.join(sha((self.directory/name).read_bytes())+'  '+name+'\n' for name in ['corpus.csv','corpus.jsonl','manifest.json']); (self.directory/'SHA256SUMS.txt').write_text(checks,encoding='utf-8')
        with self.assertRaises(ArtifactIntegrityError): verify_export(self.directory)
    def test_no_private_paths_in_semantic_files(self):
        text=(self.directory/'corpus.jsonl').read_text(encoding='utf-8')+(self.directory/'manifest.json').read_text(encoding='utf-8'); self.assertNotIn(str(self.root),text); self.assertNotIn('Authorization',text); self.assertNotIn('api_key',text)
    def test_manifest_contains_nonmember_exclusion_counts(self):
        manifest=json.loads((self.directory/'manifest.json').read_text(encoding='utf-8')); self.assertEqual(manifest['screen_include_count'],2); self.assertEqual(manifest['screen_exclude_count'],1); self.assertEqual(manifest['item_count'],2)
    def test_checksum_file_covers_all_other_artifacts(self):
        entries=(self.directory/'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines(); self.assertEqual(len(entries),3); self.assertEqual({line.split('  ')[1] for line in entries},{'corpus.csv','corpus.jsonl','manifest.json'})


class TestCorpusHandoffStatistics(CorpusCase):
    def test_handoff_requires_sealed(self):
        self.release(finalize=False)
        with self.assertRaises(ValidationError): self.releases.handoff(self.crid)
        with self.assertRaises(ValidationError): self.releases.export(self.crid,self.root/'not-sealed')
    def test_only_members_and_context_returned(self):
        self.release(); handoff=self.releases.handoff(self.crid); self.assertEqual(len(handoff['items']),2); self.assertTrue(all(r['release_manifest_sha256']==handoff['release_manifest_sha256'] and r['corpus_item_id'] for r in handoff['items']))
    def test_doi_work_id_location_handoff(self):
        self.release(); item=self.releases.handoff(self.crid)['items'][0]; self.assertTrue(item['dois']); self.assertTrue(item['openalex_work_ids']); self.assertTrue(item['provider_identifiers']); self.assertTrue(item['known_oa_locations']); self.assertTrue(item['titles'])
    def test_missing_doi_handoff(self):
        self.release([work(1,doi=None)],include_count=1); self.assertEqual(self.releases.handoff(self.crid)['items'][0]['dois'],[])
    def test_statistics_reconcile(self):
        result=self.release([work(1,doi=None),work(2,doi=None)],include_count=2); s=result['statistics']; self.assertEqual((s['screening_candidate_count'],s['screen_include_count'],s['screen_exclude_count'],s['corpus_item_count']),(2,2,0,2)); self.assertEqual((s['items_with_doi'],s['items_without_doi'],s['items_with_openalex_id'],s['items_without_openalex_id']),(0,2,2,0)); self.assertEqual(s['publication_year_distribution'],{'2024':2}); self.assertEqual(s['work_type_distribution'],{'article':2})
    def test_oa_policy_omission_does_not_change_membership(self):
        result=self.release(protocol=plan(oa_policy='OMIT')); self.assertEqual(result['statistics']['corpus_item_count'],2); self.assertIsNone(self.releases.handoff(self.crid)['items'][0]['known_oa_locations'])
    def test_alternative_ordering_frozen(self):
        self.release(protocol=plan(ordering_policy='CORPUS_ITEM_ID')); ids=[r['corpus_item_id'] for r in self.releases.items(self.crid)]; self.assertEqual(ids,sorted(ids)); self.assertTrue(self.releases.export(self.crid,self.root/'alternative')['valid'])


class TestCorpusSeparation(CorpusCase):
    def test_no_network_credentials_or_upstream_mutation_invoked(self):
        self.prepare()
        with patch('socket.socket.connect',side_effect=AssertionError('network')),patch('requests.sessions.Session.request',side_effect=AssertionError('HTTP')),patch('scholarflow.openalex_acquisition.credentials.local_client',side_effect=AssertionError('credentials')),patch.object(ScreeningStore,'submit_decision',side_effect=AssertionError('new review')),patch.object(ScreeningStore,'submit_adjudication',side_effect=AssertionError('new adjudication')),patch.object(self.discovery.resolver,'create_resolution_run',side_effect=AssertionError('new resolution')):
            self.create_release(); self.releases.finalize(self.crid); self.releases.export(self.crid,self.root/'offline'); self.releases.handoff(self.crid)
        self.assertFalse(self.client.api_key)
    def test_import_call_graph_no_new_engines(self):
        for path in (Path(__file__).parents[1]/'scholarflow/corpus_release').glob('*.py'):
            for n in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(n,(ast.Import,ast.ImportFrom)):
                    names=[a.name for a in n.names]+[getattr(n,'module','') or '']; self.assertFalse(any(name.split('.')[0] in ['requests','httpx','urllib','openai','transformers','sentence_transformers','subprocess','fulltext','unpaywall'] for name in names))
                if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute):
                    self.assertNotIn(n.func.attr,['expand_backward','expand_forward','resolve_seeds','_resolve','generate_candidates','evaluate_candidates_automatically','acquire','resume','request_page','run_to_policy_stop','submit_decision','submit_adjudication'])
                    if n.func.attr=='execute': self.assertTrue(isinstance(n.func.value,ast.Attribute) and n.func.value.attr=='db','Only local SQL execute is permitted')
    def test_cli_help_and_db_free_verifier(self):
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as error: main(['--help'])
        self.assertEqual(error.exception.code,0); self.release(); self.releases.export(self.crid,self.root/'cli')
        with patch('scholarflow.corpus_release.__main__.Database',side_effect=AssertionError('DB unavailable')),contextlib.redirect_stdout(io.StringIO()): self.assertEqual(main(['verify-export','--directory',str(self.root/'cli')]),0)
    def test_private_metadata_fails_closed_not_membership_filter(self):
        self.prepare([work(1,title='Private contact person@example.invalid')],include_count=1); self.create_release()
        with self.assertRaises(ValidationError): self.releases.materialize(self.crid)
        self.assertEqual(self.releases.get(self.crid)['state'],'OPEN'); self.assertEqual(self.releases.items(self.crid),[])
    def test_private_local_oa_path_refused(self):
        self.prepare([work(1,open_access={'is_oa':True,'oa_url':'file:///home/private/article.pdf'})],include_count=1); self.create_release()
        with self.assertRaises(ValidationError): self.releases.materialize(self.crid)
        self.assertEqual(self.releases.items(self.crid),[])
