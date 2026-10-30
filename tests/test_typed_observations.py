"""Synthetic observation contracts; no provider access or scholarly identity inference."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from reprovane.core import FoundationError, ImmutabilityError, MigrationDriftError, MigrationError, ValidationError
from reprovane.core.db import Database
from reprovane.core.foundation import Foundation
from reprovane.core.migrations import MIGRATION_DIR
from reprovane.observations import NormalizationProvenance, ObservationStore
from reprovane.observations.store import TABLES


class TypedObservationCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / 'workspace')
        self.f = Foundation(self.db)
        self.store = ObservationStore(self.db)
        self.norm = NormalizationProvenance('synthetic-normalizer', '1.0', '1', 'a' * 64)
        self.counter = 0

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def record(self, *, sealed=True, namespace=None, data=None):
        self.counter += 1
        provider = self.f.register_provider(namespace or 'synthetic_' + str(self.counter), 'Synthetic provider')
        snapshot = self.f.create_provider_snapshot(provider['provider_id'], 'synthetic', 'MANUAL_IMPORT',
            parser_name='fixture', parser_version='1', schema_profile='synthetic', schema_version='1')
        artifact = self.f.artifacts.put_bytes(json.dumps({'items': [data or {'id': 'synthetic'}]}).encode('utf-8'))
        self.f.register_raw_artifact(artifact)
        self.f.attach_artifact_to_snapshot(snapshot['snapshot_id'], artifact.artifact_sha256, 'file', 0)
        row = self.f.add_provider_record(snapshot['snapshot_id'], artifact.artifact_sha256, '/items/0')
        if sealed:
            self.f.seal_provider_snapshot(snapshot['snapshot_id'])
        return row['provider_record_id'], snapshot['snapshot_id']

    def meta(self, record=None, **kwargs):
        record = record or self.record()[0]
        return self.store.add_publication_metadata_observation(record, self.norm, **kwargs)

    def identifier(self, record=None, **kwargs):
        return self.store.add_publication_identifier_observation(record or self.record()[0], self.norm,
            identifier_namespace='doi', identifier_value_original='https://doi.org/10.example/one',
            identifier_value_normalized='10.example/one', **kwargs)

    def membership(self, record=None, **kwargs):
        return self.store.add_venue_membership_observation(record or self.record()[0], self.norm, **kwargs)

    def metric(self, record=None, **kwargs):
        return self.store.add_venue_metric_observation(record or self.record()[0], self.norm,
                                                       metric_namespace='scimago', metric_code='SJR', **kwargs)

    def citation(self, record=None, **kwargs):
        return self.store.add_citation_edge_observation(record or self.record()[0], self.norm,
            source_identifier_namespace='openalex', source_identifier_value='Wsynthetic1',
            target_identifier_namespace='doi', target_identifier_value='10.example/unresolved', **kwargs)

    def integrity(self, record=None, **kwargs):
        return self.store.add_integrity_event_observation(record or self.record()[0], self.norm,
            subject_identifier_namespace='doi', subject_identifier_value='10.example/one', **kwargs)

    def raw_insert(self, table, row):
        # Fixed table names in these tests; intentionally bypass service and error translation.
        return self.db.connection.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))

    def rejected_raw(self, table, row):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert(table, row)

    def all_rows(self, record=None):
        r = record or self.record()[0]
        return [self.meta(r, title='Example'), self.identifier(r),
            self.store.add_venue_observation(r, self.norm, title='Example Journal'),
            self.store.add_venue_identifier_observation(r, self.norm, identifier_namespace='issn',
                identifier_value_original='1234-567X', identifier_value_normalized='1234567X'),
            self.membership(r, membership_namespace='scopus', status_code='ACTIVE'),
            self.metric(r, value_type='REAL', value_real=1.2), self.citation(r), self.integrity(r, event_type='RETRACTION')]

    def test_0002_applies_after_0001(self):
        self.assertEqual([r[0] for r in self.db.execute('SELECT version FROM schema_migration ORDER BY version')], [1, 2, 3])

    def test_ledger_exact_hashes(self):
        for row in self.db.execute('SELECT * FROM schema_migration'):
            self.assertEqual(row['sha256'], hashlib.sha256((MIGRATION_DIR / row['name']).read_bytes()).hexdigest())

    def test_observation_store_user_version_three(self):
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0], 3)

    def test_migration_one_drift(self):
        self._drift('0001_persistence_foundation.sql')

    def test_migration_two_drift(self):
        self._drift('0002_typed_observations.sql')

    def _drift(self, filename):
        directory = self.root / 'drift'
        directory.mkdir()
        for path in MIGRATION_DIR.glob('*.sql'):
            (directory/path.name).write_bytes(path.read_bytes() + (b'\n-- synthetic drift\n' if path.name == filename else b''))
        with self.assertRaises(MigrationDriftError):
            Database(self.db.workspace, migration_dir=directory, target_schema_version=2)

    def test_failed_v2_upgrade_rollback(self):
        workspace = self.root / 'upgrade'
        with Database(workspace) as prior:
            project = Foundation(prior).create_project('retained', 'Retained project')
        directory = self.root / 'bad_upgrade'; directory.mkdir()
        (directory/'0001_persistence_foundation.sql').write_bytes((MIGRATION_DIR/'0001_persistence_foundation.sql').read_bytes())
        (directory/'0002_fail.sql').write_text('CREATE TABLE partial_observation(x);\nBROKEN;', encoding='utf-8')
        with self.assertRaises(MigrationError):
            Database(workspace, migration_dir=directory, target_schema_version=2)
        with Database(workspace) as prior:
            self.assertEqual(Foundation(prior).get_project(project['project_id'])['project_key'], 'retained')
            self.assertEqual(prior.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(prior.execute('SELECT count(*) FROM schema_migration').fetchone()[0], 1)
            self.assertIsNone(prior.execute("SELECT name FROM sqlite_master WHERE name='partial_observation'").fetchone())

    def test_observation_tables_and_all_guard_triggers(self):
        names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(set(TABLES.values()) <= names)
        guards = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        for table in TABLES.values():
            for suffix in ['sealed_record', 'no_update', 'no_delete', 'no_replace']:
                self.assertIn(table+'_'+suffix, guards)

    def test_open_snapshot_observation_service_rejected(self):
        record, snapshot = self.record(sealed=False)
        with self.assertRaises(ValidationError):
            self.meta(record, title='Not yet frozen')

    def test_observation_allowed_after_sealing(self):
        record, snapshot = self.record(sealed=False)
        self.f.seal_provider_snapshot(snapshot)
        self.assertEqual(self.meta(record, title='Frozen')['provider_record_id'], record)

    def test_open_snapshot_direct_insert_rejected(self):
        row = self.meta(title='Template')
        record, _ = self.record(sealed=False)
        row.update(observation_id='pmo_open', provider_record_id=record)
        self.rejected_raw('publication_metadata_observation', row)

    def test_all_types_require_sealed_evidence_directly(self):
        record, _ = self.record(sealed=False)
        for row in self.all_rows():
            prefix = row['observation_id'].split('_')[0]
            row.update(observation_id=prefix+'_open', provider_record_id=record)
            with self.subTest(prefix=prefix):
                self.rejected_raw(TABLES[prefix], row)

    def test_missing_provider_record_rejected(self):
        with self.assertRaises(ValidationError):
            self.meta('rec_nonexistent', title='Missing')

    def test_valid_publication_metadata(self):
        row = self.meta(title='Example', subtitle='Subtitle', abstract_text='Abstract', publication_type='provider-custom',
            publication_year=2024, publication_date='2024-02-29', language_code='en', volume='3', issue='2',
            first_page='1', last_page='8', article_number='e1', publisher_name='Synthetic publisher',
            field_provenance_json='{"title":"$.title"}')
        self.assertEqual(self.store.get_observation(row['observation_id']), row)

    def test_publication_metadata_requires_field(self):
        with self.assertRaises(ValidationError):
            self.meta()

    def test_invalid_calendar_date(self):
        for value in ['2025-02-29','2024-02-30','2024-2-01','0000-01-01','2024-01-01T00:00:00Z']:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.meta(publication_date=value)

    def test_invalid_year(self):
        for value in [0, 10000, 2024.0, True, '2024']:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.meta(publication_year=value)

    def test_date_year_disagreement_preserved(self):
        row = self.meta(publication_date='2024-12-31', publication_year=2025)
        self.assertEqual((row['publication_date'],row['publication_year']), ('2024-12-31',2025))

    def test_provenance_json_object_required(self):
        for value in ['[]','null','1','"text"','{']:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.meta(title='Example', field_provenance_json=value)

    def test_publication_update_rejected(self):
        self.meta(title='Example')
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute("UPDATE publication_metadata_observation SET title='Changed'")

    def test_publication_delete_rejected(self):
        self.meta(title='Example')
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute('DELETE FROM publication_metadata_observation')

    def test_identifier_has_no_global_uniqueness(self):
        record, _ = self.record()
        a, b = self.identifier(record), self.identifier(record)
        self.assertNotEqual(a['observation_id'], b['observation_id'])

    def test_same_doi_different_provider_records(self):
        a, b = self.identifier(), self.identifier()
        self.assertEqual(a['identifier_value_normalized'], b['identifier_value_normalized'])
        self.assertNotEqual(a['provider_record_id'], b['provider_record_id'])
        self.assertEqual(len(self.store.list_publication_identifier_observations()), 2)

    def test_identifier_original_and_normalized_preserved(self):
        row = self.identifier()
        self.assertEqual(row['identifier_value_original'], 'https://doi.org/10.example/one')
        self.assertEqual(row['identifier_value_normalized'], '10.example/one')

    def test_invalid_namespace_token(self):
        record, _ = self.record()
        for namespace in ['', 'DOI', 'doi token', 'doi;DROP', '1doi']:
            with self.subTest(namespace=namespace), self.assertRaises(ValidationError):
                self.store.add_publication_identifier_observation(record, self.norm, identifier_namespace=namespace,
                    identifier_value_original='x', identifier_value_normalized='x')

    def test_namespace_is_not_closed_provider_list(self):
        record, _ = self.record()
        row = self.store.add_venue_identifier_observation(record, self.norm, identifier_namespace='future.custom_id',
                    identifier_value_original='x', identifier_value_normalized='x')
        self.assertEqual(row['identifier_namespace'], 'future.custom_id')

    def test_venue_description(self):
        record, _ = self.record()
        row = self.store.add_venue_observation(record, self.norm, title='Example Journal', publisher_name='Example',
            source_type='provider-specific', country_code='VN')
        self.assertEqual(self.store.list_venue_observations(record), [row])

    def test_venue_requires_field(self):
        record, _ = self.record()
        with self.assertRaises(ValidationError):
            self.store.add_venue_observation(record, self.norm)

    def test_venue_identifier(self):
        record, _ = self.record()
        row = self.store.add_venue_identifier_observation(record, self.norm, identifier_namespace='issn_l',
            identifier_value_original='1234-567X', identifier_value_normalized='1234567X')
        self.assertEqual(self.store.list_venue_identifier_observations(record), [row])

    def test_same_issn_across_records(self):
        for _ in range(2):
            record, _ = self.record()
            self.store.add_venue_identifier_observation(record, self.norm, identifier_namespace='issn',
                identifier_value_original='1234-567X', identifier_value_normalized='1234567X')
        self.assertEqual(len(self.store.list_venue_identifier_observations()), 2)

    def test_scopus_status_namespace(self):
        row = self.membership(membership_namespace='scopus', status_code='ACTIVE')
        self.assertEqual(row['membership_namespace'], 'scopus')

    def test_wos_collection_namespace(self):
        row = self.membership(membership_namespace='wos', collection_code='SCIE')
        self.assertEqual(row['collection_code'], 'SCIE')

    def test_provider_venue_claims_independent(self):
        scopus, _ = self.record(namespace='scopus')
        wos, _ = self.record(namespace='wos')
        scimago, _ = self.record(namespace='scimago')
        self.membership(scopus, membership_namespace='scopus', status_code='ACTIVE')
        self.membership(wos, membership_namespace='wos', collection_code='SCIE')
        self.store.add_venue_metric_observation(scimago, self.norm, metric_namespace='scimago', metric_code='QUARTILE',
                                              value_type='TEXT', value_text='Q1', category_name='Software')
        self.assertEqual(len(self.store.list_venue_membership_observations()), 2)
        self.assertEqual(len(self.store.list_venue_metric_observations()), 1)
        self.assertNotIn('indexed', self.store.list_venue_membership_observations()[0])

    def test_membership_requires_collection_or_status(self):
        with self.assertRaises(ValidationError):
            self.membership(membership_namespace='scopus')

    def test_coverage_valid_and_equal(self):
        for start, end in [(2020,2025),(2024,2024)]:
            row = self.membership(membership_namespace='scopus', status_code='ACTIVE', coverage_start_year=start, coverage_end_year=end)
            self.assertEqual(row['coverage_start_year'], start)

    def test_reversed_coverage_rejected(self):
        with self.assertRaises(ValidationError):
            self.membership(membership_namespace='wos', status_code='custom', coverage_start_year=2025, coverage_end_year=2020)

    def test_current_status_creates_no_history(self):
        row = self.membership(membership_namespace='scimago', status_code='ACTIVE_LATEST')
        self.assertIsNone(row['coverage_start_year'])
        self.assertIsNone(row['valid_from_date'])
        self.assertEqual(len(self.store.list_venue_membership_observations()), 1)

    def test_membership_dates_strict(self):
        with self.assertRaises(ValidationError):
            self.membership(membership_namespace='wos', status_code='custom', valid_to_date='2025-02-29')

    def test_metric_real(self):
        row = self.metric(value_type='REAL', value_real=1.23)
        self.assertEqual(self.store.get_observation(row['observation_id'])['value_real'], 1.23)

    def test_metric_text(self):
        self.assertEqual(self.metric(value_type='TEXT', value_text='Q1')['value_text'], 'Q1')

    def test_metric_integer(self):
        self.assertEqual(self.metric(value_type='INTEGER', value_integer=12)['value_integer'], 12)

    def test_metric_mismatched_type_rejected(self):
        with self.assertRaises(ValidationError):
            self.metric(value_type='REAL', value_text='1.23')

    def test_metric_multiple_value_columns_rejected(self):
        with self.assertRaises(ValidationError):
            self.metric(value_type='REAL', value_real=1.2, value_integer=1)

    def test_metric_typed_check_direct_sql(self):
        row = self.metric(value_type='REAL', value_real=1.2)
        row.update(observation_id='vmeo_bad', value_text='extra')
        self.rejected_raw('venue_metric_observation', row)

    def test_metric_nonfinite_and_boolean_rejected(self):
        for value in [float('inf'), float('nan'), True]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.metric(value_type='REAL', value_real=value)

    def test_category_quartile_claim_no_best_result(self):
        record, _ = self.record()
        for category, q in [('Software','Q1'),('Medicine','Q4')]:
            self.store.add_venue_metric_observation(record, self.norm, metric_namespace='scimago', metric_code='QUARTILE',
                category_name=category, period_year=2024, value_type='TEXT', value_text=q)
        rows = self.store.list_venue_metric_observations()
        self.assertEqual(len(rows), 2)
        self.assertFalse(any('best' in key for row in rows for key in row))

    def test_cites_edge(self):
        row = self.citation()
        self.assertEqual(self.store.list_citation_edge_observations(), [row])

    def test_unsupported_relation_types_rejected(self):
        for relation in ['PREPRINT_OF','EXTENDED_VERSION_OF','CORRECTION_OF','RETRACTION_OF']:
            with self.subTest(relation=relation), self.assertRaises(ValidationError):
                self.citation(relation_type=relation)

    def test_unresolved_citation_identifiers_preserved(self):
        row = self.citation()
        self.assertEqual(row['target_identifier_value'], '10.example/unresolved')
        self.assertNotIn('source_entity_id', row)

    def test_duplicate_citation_claims_across_providers(self):
        self.citation(); self.citation()
        self.assertEqual(len(self.store.list_citation_edge_observations()), 2)

    def test_no_discovery_tables(self):
        self.assertFalse({'discovery_run','discovery_edge','discovery_event'} & self.table_names())

    def test_retraction_event(self):
        row = self.integrity(event_type='RETRACTION', effective_date='2024-01-01')
        self.assertEqual(row['event_type'], 'RETRACTION')

    def test_correction_independent(self):
        a = self.integrity(event_type='RETRACTION')
        b = self.integrity(event_type='CORRECTION', related_notice_identifier_namespace='doi', related_notice_identifier_value='10.example/notice')
        self.assertNotEqual(a['observation_id'], b['observation_id'])

    def test_integrity_related_notice_pair_service(self):
        with self.assertRaises(ValidationError):
            self.integrity(event_type='RETRACTION', related_notice_identifier_namespace='doi')

    def test_integrity_related_notice_pair_direct_sql(self):
        row = self.integrity(event_type='RETRACTION')
        row.update(observation_id='ieo_bad', related_notice_identifier_value='10.example/notice')
        self.rejected_raw('integrity_event_observation', row)

    def test_integrity_invalid_effective_date(self):
        with self.assertRaises(ValidationError):
            self.integrity(event_type='OTHER', effective_date='2024-02-30')

    def test_conflicting_integrity_claims_preserved(self):
        self.integrity(event_type='RETRACTION', status_code='ASSERTED')
        self.integrity(event_type='RETRACTION', status_code='DISPUTED')
        self.assertEqual({r['status_code'] for r in self.store.list_integrity_event_observations()}, {'ASSERTED','DISPUTED'})

    def test_no_canonical_integrity_status(self):
        self.assertNotIn('integrity_status', self.table_names())
        for table in TABLES.values():
            self.assertNotIn('is_retracted', {r['name'] for r in self.db.execute('PRAGMA table_info('+table+')')})

    def test_normalizer_fields_required(self):
        record, _ = self.record()
        for provenance in [NormalizationProvenance('', '1', '1'), NormalizationProvenance('n', '', '1'), NormalizationProvenance('n', '1', '')]:
            with self.subTest(provenance=provenance), self.assertRaises(ValidationError):
                self.store.add_publication_metadata_observation(record, provenance, title='Example')

    def test_normalizer_requirement_direct_sql(self):
        row = self.meta(title='Template')
        row.update(observation_id='pmo_missing_normalizer', normalizer_name=None)
        self.rejected_raw('publication_metadata_observation', row)

    def test_config_hash_lowercase_sha256(self):
        record, _ = self.record()
        for config in ['A'*64, 'a'*63, 'g'*64, 'synthetic-token']:
            with self.subTest(config=config), self.assertRaises(ValidationError):
                self.store.add_publication_metadata_observation(record, NormalizationProvenance('n','1','1',config), title='Example')

    def test_normalizer_version_coexistence(self):
        record, _ = self.record()
        for version in ['1.0','1.1']:
            self.store.add_publication_metadata_observation(record, NormalizationProvenance('n',version,'1'), title='Example')
        self.assertEqual({r['normalizer_version'] for r in self.store.list_publication_metadata_observations(record)}, {'1.0','1.1'})

    def test_normalized_timestamp_preserved(self):
        row = self.meta(title='Example')
        self.assertTrue(row['normalized_at_utc'].endswith('Z'))
        self.assertEqual(self.store.get_observation(row['observation_id'])['normalized_at_utc'], row['normalized_at_utc'])

    def test_provenance_update_rejected(self):
        self.meta(title='Example')
        with self.assertRaises(ImmutabilityError):
            self.db.execute("UPDATE publication_metadata_observation SET normalizer_version='2'")

    def test_all_observation_updates_rejected_direct_sql(self):
        for row in self.all_rows():
            table = TABLES[row['observation_id'].split('_')[0]]
            with self.subTest(table=table), self.assertRaises(sqlite3.IntegrityError):
                self.db.connection.execute(f"UPDATE {table} SET normalizer_version='changed'")

    def test_all_observation_deletes_rejected_direct_sql(self):
        for row in self.all_rows():
            table = TABLES[row['observation_id'].split('_')[0]]
            with self.subTest(table=table), self.assertRaises(sqlite3.IntegrityError):
                self.db.connection.execute('DELETE FROM '+table)

    def test_replace_guard_recursive_on(self):
        row = self.meta(title='Original')
        row['title'] = 'Changed'
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute('INSERT OR REPLACE INTO publication_metadata_observation ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', tuple(row.values()))

    def test_replace_guard_external_recursive_off(self):
        row = self.meta(title='Original')
        row['title'] = 'Changed'
        with closing(sqlite3.connect(self.db.path, isolation_level=None)) as raw:
            raw.execute('PRAGMA recursive_triggers=OFF')
            with self.assertRaises(sqlite3.IntegrityError):
                raw.execute('INSERT OR REPLACE INTO publication_metadata_observation ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', tuple(row.values()))
        self.assertEqual(self.store.get_observation(row['observation_id'])['title'], 'Original')

    def test_provider_disagreement_survives_without_merge(self):
        for year, title in [(2024,'Example'),(2025,'Example revised')]:
            record, _ = self.record(data={'doi':'10.example/one','year':year,'title':title})
            self.identifier(record)
            self.meta(record, title=title, publication_year=year)
        self.assertEqual({r['publication_year'] for r in self.store.list_publication_metadata_observations()}, {2024,2025})
        self.assertEqual(len(self.store.list_publication_identifier_observations()), 2)
        self.assertEqual(self.db.execute('SELECT count(*) FROM provider_record').fetchone()[0], 2)
        self.assertNotIn('publication_entity', self.table_names())

    def test_same_title_year_no_collapse(self):
        self.meta(title='Example',publication_year=2024); self.meta(title='Example',publication_year=2024)
        self.assertEqual(len(self.store.list_publication_metadata_observations()), 2)

    def table_names(self):
        return {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_no_publication_entity(self):
        self.assertNotIn('publication_entity', self.table_names())

    def test_no_venue_entity(self):
        self.assertNotIn('venue_entity', self.table_names())

    def test_no_resolution_decisions(self):
        self.assertNotIn('entity_resolution_decision', self.table_names())

    def test_validation_errors_no_payload_echo(self):
        secret = 'synthetic-token-do-not-echo'
        with self.assertRaises(ValidationError) as caught:
            self.meta(title='Example', field_provenance_json='{"token":"'+secret+'", BROKEN}')
        self.assertNotIn(secret, str(caught.exception))
        record, _ = self.record()
        with self.assertRaises(ValidationError) as caught:
            self.store.add_venue_membership_observation(record,self.norm,membership_namespace=secret+' invalid',status_code='ACTIVE')
        self.assertNotIn(secret, str(caught.exception))

    def test_synthetic_fixture_and_no_integration_imports(self):
        # Whitelist imports rather than scanning fixture strings for the word "token".
        import ast
        package = Path(__file__).resolve().parents[1] / 'reprovane/observations'
        for path in package.glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse({x.name.split('.')[0] for x in node.names} & {'requests','openai','sqlalchemy'})
                if isinstance(node, ast.ImportFrom) and node.level==0:
                    self.assertNotIn((node.module or '').split('.')[0], {'requests','openai','sqlalchemy'})

    def test_json_object_enforced_direct_sql(self):
        row = self.meta(title='Template')
        for value in ['[]','{']:
            row.update(observation_id='pmo_json_bad',field_provenance_json=value)
            self.rejected_raw('publication_metadata_observation', row)

    def test_filter_reads_and_safe_get(self):
        record, _ = self.record(); rows = self.all_rows(record)
        self.assertEqual(len(self.store.list_observations_for_provider_record(record)), 8)
        for row in rows:
            self.assertEqual(self.store.get_observation(row['observation_id']), row)
        with self.assertRaises(ValidationError):
            self.store.get_observation('publication_metadata_observation;DROP')

    def test_service_transaction_rollback(self):
        record, _ = self.record()
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.meta(record, title='Rolled back')
                self.identifier(record)
                raise RuntimeError('synthetic')
        self.assertEqual(self.store.list_observations_for_provider_record(record), [])

    def test_default_foundation_preserved_and_reopen_no_downgrade(self):
        with Database(self.db.workspace) as again:
            self.assertEqual(again.execute('PRAGMA user_version').fetchone()[0], 3)
        with Database(self.root/'foundation_only') as bare:
            self.assertEqual(bare.execute('PRAGMA user_version').fetchone()[0], 1)

    def test_explicit_schema_target_two(self):
        with Database(self.root/'explicit',target_schema_version=2) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_no_assertion_or_universal_confidence(self):
        self.assertNotIn('assertion', self.table_names())
        for table in TABLES.values():
            self.assertNotIn('confidence', {r['name'] for r in self.db.execute('PRAGMA table_info('+table+')')})

    def test_real_integer_input_and_huge_numbers(self):
        row = self.metric(value_type='REAL', value_real=10**100)
        self.assertEqual(self.store.get_observation(row['observation_id'])['value_real'], 1e100)
        with self.assertRaises(ValidationError):
            self.metric(value_type='REAL', value_real=10**1000)
        with self.assertRaises(ValidationError):
            self.metric(value_type='INTEGER', value_integer=2**63)

    def test_sql_date_shape_and_year_checks(self):
        row = self.meta(title='Template')
        for changes in [dict(publication_date='2024-13-01'),dict(publication_date='2024-1-01'),dict(publication_year=0)]:
            bad = dict(row, observation_id='pmo_bad_date', **changes)
            self.rejected_raw('publication_metadata_observation', bad)

    def test_unchanged_0001_bytes_from_accepted_history(self):
        self.assertEqual(hashlib.sha256((MIGRATION_DIR/'0001_persistence_foundation.sql').read_bytes()).hexdigest(),
                         '6f5779b2073b4cdeb4bc8a70b8fbcf3f5892348eeedf74d35fa66430ba0e6b8e')

    def test_store_initialization_requires_idle_connection(self):
        with self.db.transaction():
            with self.assertRaises(MigrationError):
                ObservationStore(self.db)


if __name__ == '__main__':
    unittest.main()
