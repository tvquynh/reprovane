"""FIX01: ordered provider authorship claims, without global author identity."""
from contextlib import closing
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from reprovane.core import ImmutabilityError, MigrationDriftError, ValidationError
from reprovane.core.db import Database
from reprovane.core.foundation import Foundation
from reprovane.core.migrations import MIGRATION_DIR
from reprovane.observations import NormalizationProvenance, ObservationStore

TABLE = 'publication_authorship_observation'


class AuthorshipObservationCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root/'workspace')
        self.f = Foundation(self.db)
        self.store = ObservationStore(self.db)
        self.norm = NormalizationProvenance('synthetic-normalizer','1','1')
        self.counter = 0

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def record(self, sealed=True):
        self.counter += 1
        p = self.f.register_provider('synthetic_'+str(self.counter), 'Synthetic provider')
        s = self.f.create_provider_snapshot(p['provider_id'],'synthetic','MANUAL_IMPORT',parser_name='fixture',
            parser_version='1',schema_profile='opaque',schema_version='1')
        a = self.f.artifacts.put_bytes(b'{"authors":["Synthetic Author"]}')
        self.f.register_raw_artifact(a)
        self.f.attach_artifact_to_snapshot(s['snapshot_id'],a.artifact_sha256,'response',0)
        r = self.f.add_provider_record(s['snapshot_id'],a.artifact_sha256,'/')
        if sealed:
            self.f.seal_provider_snapshot(s['snapshot_id'])
        return r['provider_record_id'],s['snapshot_id']

    def add(self, record=None, **kwargs):
        fields = dict(author_position=1,author_display_name='Synthetic Author')
        fields.update(kwargs)
        return self.store.add_publication_authorship_observation(record or self.record()[0],self.norm,**fields)

    def raw_insert(self,row,replace=False,connection=None):
        sql = ('INSERT OR REPLACE' if replace else 'INSERT')+' INTO '+TABLE+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')'
        return (connection or self.db.connection).execute(sql,tuple(row.values()))

    def raw_rejected(self,row):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert(row)

    def table_names(self):
        return {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_migration_0003_applied(self):
        row = self.db.execute('SELECT name FROM schema_migration WHERE version=3').fetchone()
        self.assertEqual(row[0],'0003_publication_authorship_observation.sql')

    def test_ledger_versions(self):
        self.assertEqual([r[0] for r in self.db.execute('SELECT version FROM schema_migration ORDER BY version')],[1,2,3])

    def test_store_schema_version_three(self):
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],3)

    def test_migration_0001_hash_unchanged(self):
        self.assertEqual(hashlib.sha256((MIGRATION_DIR/'0001_persistence_foundation.sql').read_bytes()).hexdigest(),
            '6f5779b2073b4cdeb4bc8a70b8fbcf3f5892348eeedf74d35fa66430ba0e6b8e')

    def test_migration_0002_hash_unchanged(self):
        self.assertEqual(hashlib.sha256((MIGRATION_DIR/'0002_typed_observations.sql').read_bytes()).hexdigest(),
            '7356fa1ddf9881c53873626b43a8f54132451e1d86aff53b15c4ae556326dd26')

    def test_migration_0003_drift(self):
        directory = self.root/'drift'; directory.mkdir()
        for p in MIGRATION_DIR.glob('*.sql'):
            (directory/p.name).write_bytes(p.read_bytes()+(b'\n-- synthetic drift\n' if p.name.startswith('0003') else b''))
        with self.assertRaises(MigrationDriftError):
            Database(self.db.workspace,migration_dir=directory,target_schema_version=3)

    def test_open_service_rejected(self):
        record,_ = self.record(sealed=False)
        with self.assertRaises(ValidationError):
            self.add(record)

    def test_open_direct_sql_rejected(self):
        row = self.add(); record,_ = self.record(sealed=False)
        row.update(observation_id='pao_open',provider_record_id=record)
        self.raw_rejected(row)

    def test_after_seal_allowed(self):
        record,snapshot = self.record(sealed=False)
        self.f.seal_provider_snapshot(snapshot)
        self.assertEqual(self.add(record)['provider_record_id'],record)

    def test_position_one(self):
        self.assertEqual(self.add(author_position=1)['author_position'],1)

    def test_position_zero_rejected(self):
        with self.assertRaises(ValidationError): self.add(author_position=0)

    def test_position_negative_rejected(self):
        with self.assertRaises(ValidationError): self.add(author_position=-1)

    def test_noninteger_position_rejected(self):
        for value in [True,1.0,'1',None,2**63]:
            with self.subTest(value=value),self.assertRaises(ValidationError): self.add(author_position=value)

    def test_display_name_required(self):
        for value in ['', '  ',None]:
            with self.subTest(value=value),self.assertRaises(ValidationError): self.add(author_display_name=value)

    def test_group_author(self):
        row = self.add(author_display_name='The XYZ Study Group')
        self.assertIsNone(row['author_given_name']); self.assertIsNone(row['author_family_name'])
        self.assertIsNone(row['orcid_normalized']); self.assertIsNone(row['provider_author_id_normalized'])

    def test_given_family_optional_not_inferred(self):
        row = self.add(author_display_name='Nguyễn Văn An')
        self.assertIsNone(row['author_given_name']); self.assertIsNone(row['author_family_name'])

    def test_names_preserved_without_casefold_or_accent_removal(self):
        name = '  Nguyễn Văn Ánh  '
        row = self.add(author_display_name=name,author_given_name='Văn Ánh',author_family_name='Nguyễn')
        self.assertEqual(self.store.get_observation(row['observation_id'])['author_display_name'],name)
        self.assertEqual(row['author_family_name'],'Nguyễn')

    def test_provider_id_all_null(self):
        row = self.add()
        self.assertTrue(all(row[k] is None for k in ['provider_author_id_namespace','provider_author_id_original','provider_author_id_normalized']))

    def test_provider_id_complete(self):
        row = self.add(provider_author_id_namespace='openalex_author',provider_author_id_original='https://openalex.org/Asynthetic',provider_author_id_normalized='Asynthetic')
        self.assertEqual(row['provider_author_id_original'],'https://openalex.org/Asynthetic')

    def test_provider_id_partial_service_rejected(self):
        keys=['provider_author_id_namespace','provider_author_id_original','provider_author_id_normalized']
        values=['openalex_author','https://example.test/author/1','Asynthetic']
        for mask in itertools.product([False,True],repeat=3):
            if all(mask) or not any(mask): continue
            fields={k:v for k,v,include in zip(keys,values,mask) if include}
            with self.subTest(mask=mask),self.assertRaises(ValidationError): self.add(**fields)

    def test_provider_id_group_direct_sql(self):
        row = self.add(); row.update(observation_id='pao_partial',provider_author_id_namespace='openalex_author')
        self.raw_rejected(row)

    def test_provider_namespace_validation(self):
        with self.assertRaises(ValidationError):
            self.add(provider_author_id_namespace='OpenAlex Author',provider_author_id_original='x',provider_author_id_normalized='x')

    def test_orcid_all_null(self):
        row = self.add(); self.assertIsNone(row['orcid_original']); self.assertIsNone(row['orcid_normalized'])

    def test_orcid_complete(self):
        row = self.add(orcid_original='https://orcid.org/0000-0000-0000-000X',orcid_normalized='0000-0000-0000-000X')
        self.assertEqual(row['orcid_normalized'],'0000-0000-0000-000X')

    def test_orcid_partial_service_rejected(self):
        for fields in [dict(orcid_original='synthetic'),dict(orcid_normalized='0000-0000-0000-000X')]:
            with self.subTest(fields=fields),self.assertRaises(ValidationError): self.add(**fields)

    def test_orcid_pair_direct_sql(self):
        row=self.add(); row.update(observation_id='pao_orcid_partial',orcid_original='synthetic')
        self.raw_rejected(row)

    def test_malformed_orcid_lexical_rejected(self):
        for value in ['000000000000000X','0000-0000-0000-000x','https://orcid.org/0000-0000-0000-000X','0000-0000-0000-000Y','0000-0000-0000-000Xextra']:
            with self.subTest(value=value),self.assertRaises(ValidationError): self.add(orcid_original='synthetic',orcid_normalized=value)

    def test_orcid_checksum_is_not_validated(self):
        # Canonical lexical shape but deliberately no checksum claim or validation.
        self.assertEqual(self.add(orcid_original='synthetic',orcid_normalized='0000-0000-0000-0000')['orcid_normalized'],'0000-0000-0000-0000')

    def test_same_name_different_records(self):
        a=self.add(author_display_name='John Smith'); b=self.add(author_display_name='John Smith')
        self.assertNotEqual(a['provider_record_id'],b['provider_record_id'])
        self.assertEqual(len(self.store.list_publication_authorship_observations()),2)

    def test_same_orcid_different_records(self):
        for _ in range(2): self.add(orcid_original='synthetic',orcid_normalized='0000-0000-0000-000X')
        self.assertEqual(len(self.store.list_publication_authorship_observations()),2)

    def test_same_provider_id_different_records(self):
        for _ in range(2): self.add(provider_author_id_namespace='openalex_author',provider_author_id_original='synthetic',provider_author_id_normalized='Asynthetic')
        self.assertEqual(len(self.store.list_publication_authorship_observations()),2)

    def test_normalizer_versions_same_position_coexist(self):
        record,_=self.record()
        for version in ['1','2']:
            self.store.add_publication_authorship_observation(record,NormalizationProvenance('n',version,'1'),author_position=1,author_display_name='John Smith')
        rows=self.store.list_publication_authorship_observations(record)
        self.assertEqual({r['normalizer_version'] for r in rows},{'1','2'})
        self.assertEqual({r['author_position'] for r in rows},{1})

    def test_no_update_direct_sql(self):
        self.add()
        with self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute("UPDATE "+TABLE+" SET author_display_name='Changed'")

    def test_no_delete_direct_sql(self):
        self.add()
        with self.assertRaises(sqlite3.IntegrityError): self.db.connection.execute('DELETE FROM '+TABLE)

    def test_no_replace_recursive_on(self):
        row=self.add(); row['author_display_name']='Changed'
        with self.assertRaises(sqlite3.IntegrityError): self.raw_insert(row,replace=True)

    def test_no_replace_external_recursive_off(self):
        row=self.add(); row['author_display_name']='Changed'
        with closing(sqlite3.connect(self.db.path,isolation_level=None)) as raw:
            raw.execute('PRAGMA recursive_triggers=OFF')
            with self.assertRaises(sqlite3.IntegrityError): self.raw_insert(row,replace=True,connection=raw)
        self.assertEqual(self.store.get_observation(row['observation_id'])['author_display_name'],'Synthetic Author')

    def test_field_provenance_object(self):
        for value in ['[]','null','{']:
            with self.subTest(value=value),self.assertRaises(ValidationError): self.add(field_provenance_json=value)
        row=self.add(field_provenance_json='{"author_display_name":"$.authors[0]"}')
        self.assertEqual(json.loads(row['field_provenance_json'])['author_display_name'],'$.authors[0]')

    def test_get_pao(self):
        row=self.add(); self.assertTrue(row['observation_id'].startswith('pao_'))
        self.assertEqual(self.store.get_observation(row['observation_id']),row)

    def test_generic_provider_record_list(self):
        record,_=self.record(); row=self.add(record)
        self.assertIn(row,self.store.list_observations_for_provider_record(record))

    def test_list_position_order_and_filter(self):
        record,_=self.record()
        for position,name in [(3,'Alpha'),(1,'Zulu'),(2,'Group')]: self.add(record,author_position=position,author_display_name=name)
        self.add(author_position=1,author_display_name='Other record')
        rows=self.store.list_publication_authorship_observations(record)
        self.assertEqual([r['author_position'] for r in rows],[1,2,3])
        self.assertEqual([r['author_display_name'] for r in rows],['Zulu','Group','Alpha'])

    def test_same_position_tie_order_is_deterministic(self):
        record,_=self.record()
        with patch('reprovane.observations.validation.utc_now',return_value='2026-01-01T00:00:00.000000Z'):
            self.add(record); self.add(record)
        rows=self.store.list_publication_authorship_observations(record)
        self.assertEqual([r['observation_id'] for r in rows],sorted(r['observation_id'] for r in rows))

    def test_no_author_entity(self):
        self.assertNotIn('author_entity',self.table_names())

    def test_no_author_resolution_table(self):
        self.assertFalse(any('author' in name and name!=TABLE for name in self.table_names()))

    def test_no_publication_entity_or_resolution_decision(self):
        self.assertFalse({'publication_entity','entity_resolution_decision','institution_entity'} & self.table_names())

    def test_common_provenance_immutable_and_timestamp(self):
        row=self.add(); self.assertTrue(row['normalized_at_utc'].endswith('Z'))
        with self.assertRaises(ImmutabilityError): self.db.execute("UPDATE "+TABLE+" SET normalizer_version='2'")

    def test_direct_sql_position_name_json_constraints(self):
        row=self.add()
        for changes in [dict(author_position=0),dict(author_position=1.5),dict(author_display_name=' '),dict(field_provenance_json='[]'),dict(field_provenance_json='{')]:
            with self.subTest(changes=changes): self.raw_rejected(dict(row,observation_id='pao_bad',**changes))

    def test_missing_record_rejected(self):
        with self.assertRaises(ValidationError): self.add('rec_missing')

    def test_lookup_indexes_not_unique(self):
        indexes=[dict(r) for r in self.db.execute('PRAGMA index_list('+TABLE+')') if r['origin']=='c']
        self.assertEqual(len(indexes),3); self.assertTrue(all(r['unique']==0 for r in indexes))

    def test_other_eight_observations_and_authorship_mix(self):
        record,_=self.record()
        self.store.add_publication_metadata_observation(record,self.norm,title='Synthetic')
        self.add(record)
        self.assertEqual(len(self.store.list_observations_for_provider_record(record)),2)
        self.assertEqual(len(self.store.list_publication_metadata_observations(record)),1)

    def test_transaction_rollback(self):
        record,_=self.record()
        with self.assertRaises(RuntimeError):
            with self.db.transaction(): self.add(record); raise RuntimeError('synthetic')
        self.assertEqual(self.store.list_publication_authorship_observations(record),[])

    def test_upgrade_from_v2_preserves_old_table_ddl(self):
        workspace=self.root/'upgrade'
        with Database(workspace,target_schema_version=2) as db:
            before={r[0]:r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}
            ObservationStore(db)
            after={r[0]:r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}
            self.assertEqual({k:after[k] for k in before},before)
            self.assertEqual(set(after)-set(before),{TABLE})

    def test_validation_errors_do_not_echo_synthetic_values(self):
        value='synthetic-token-not-an-orcid'
        with self.assertRaises(ValidationError) as caught: self.add(orcid_original=value,orcid_normalized=value)
        self.assertNotIn(value,str(caught.exception))


if __name__=='__main__': unittest.main()
