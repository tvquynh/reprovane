"""Functional parity with v4.1.0 fetch-oa on one synthetic in-memory web (tools/pdf_legacy_parity.py)."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pdf_legacy_parity', ROOT / 'tools' / 'pdf_legacy_parity.py')
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)


class TestLegacyParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = parity.run_parity()

    def test_no_unclassified_difference(self):
        self.assertEqual(self.report['regressions'], [])

    def test_every_difference_is_a_documented_policy(self):
        self.assertEqual(sorted(e['paper'] for e in self.report['intentional']), sorted(parity.INTENTIONAL))

    def test_resume_and_integrity(self):
        m = self.report['metrics']
        self.assertTrue(m['resume_correctness']['unified_limit_then_resume_equals_single_pass'])
        self.assertTrue(m['resume_correctness']['legacy_limit_then_resume_equals_single_pass'])
        self.assertTrue(m['artifact_integrity']['identical_bytes'])
        self.assertTrue(m['artifact_integrity']['unified_db_and_cas_verified'])

    def test_inputs_manual_and_legacy_import(self):
        m = self.report['metrics']
        self.assertEqual(m['accepted_input_records']['legacy_papers'], m['accepted_input_records']['unified_unique_works'])
        self.assertEqual(m['accepted_input_records']['legacy_rows_without_identifier'], m['accepted_input_records']['unified_rejected'])
        self.assertEqual(m['manual_ingestion']['legacy_manual'], m['manual_ingestion']['unified_manual'])
        imported = m['legacy_v4_import']
        self.assertTrue(imported['legacy_folder_unchanged'] and imported['verified'])
        self.assertEqual(imported['statuses']['W1'], 'LEGACY_V4_DOWNLOADED')
        self.assertEqual(imported['statuses']['W8'], 'MANUAL_IMPORTED')


if __name__ == '__main__':
    unittest.main()
