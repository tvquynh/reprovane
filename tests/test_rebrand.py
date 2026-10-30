"""Breaking interface rebrand: no aliases, immutable storage and frozen v1 science."""
import contextlib
import hashlib
import importlib.metadata
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

import reprovane
from reprovane.__main__ import main
from reprovane.core.migrations import MIGRATION_DIR
from reprovane.operations.lock import workspace_lock, lock_diagnosis
from reprovane.operations.workspace import initialize, doctor
from reprovane_bootstrap_fixtures import reprovane_e2e

ROOT = Path(__file__).resolve().parents[1]


class TestRebrandContract(unittest.TestCase):
    def test_installed_distribution_metadata(self):
        self.assertEqual(importlib.metadata.metadata('reprovane')['Name'], 'reprovane')
        self.assertEqual(importlib.metadata.version('reprovane'), '2.0.0')

    def test_version_and_lineage(self):
        from reprovane._version import LEGACY_PREDECESSOR_VERSION
        self.assertEqual(reprovane.__version__, '2.0.0')
        self.assertEqual(LEGACY_PREDECESSOR_VERSION, '4.1.0')

    def test_no_old_namespace(self):
        self.assertFalse((ROOT/'scholarflow').exists())
        self.assertIsNone(importlib.util.find_spec('scholarflow'))

    def test_only_new_distribution_entrypoint(self):
        scripts = {e.name: e.value for e in importlib.metadata.distribution('reprovane').entry_points
                   if e.group == 'console_scripts'}
        self.assertEqual(scripts, {'reprovane': 'reprovane.__main__:main'})

    def test_module_cli(self):
        result = subprocess.run([sys.executable, '-m', 'reprovane', '--version'],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, '2.0.0\n')

    def test_cli_help(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()) as out:
            main(['--help'])
        self.assertIn('Reprovane', out.getvalue())

    def test_sql_exact_v1_baseline(self):
        expected = json.loads((ROOT/'tests/fixtures/bootstrap/migration_hashes.json').read_text())
        self.assertEqual({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in MIGRATION_DIR.glob('*.sql')}, expected)

    def test_no_schema14(self):
        self.assertFalse(list(MIGRATION_DIR.glob('0014*')))

    def test_read_existing_workspace_without_byte_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            initialize(root)
            def state():
                return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in root.rglob('*') if p.is_file()}
            before = state()
            result = doctor(root, full=True)
            self.assertEqual(result['schema_version'], 13)
            self.assertEqual(result['pending'], [])
            self.assertEqual(state(), before)
            self.assertTrue((root/'db/litintel.sqlite').is_file())

    def test_single_compatible_os_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with workspace_lock(root):
                self.assertTrue((root/'.litintel-writer.lock').is_file())
                self.assertEqual(lock_diagnosis(root), 'ACTIVE_WRITER_OR_INACCESSIBLE')
                self.assertFalse((root/'.reprovane-writer.lock').exists())
                self.assertFalse((root/'.scholarflow-writer.lock').exists())

    def test_identity_audit_has_no_stale_current_identity(self):
        spec = importlib.util.spec_from_file_location('identity_audit', ROOT/'tools/audit_identity.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = module.audit()
        self.assertTrue(rows)
        self.assertFalse([r for r in rows if r['category'].startswith('BUG/')])

    def test_dependency_ranges_unchanged(self):
        project = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']
        self.assertEqual(project['dependencies'], ['requests>=2.31,<3', 'pypdf>=6,<7'])

    def test_beginner_guides_packaged(self):
        project = tomllib.loads((ROOT/'pyproject.toml').read_text())
        docs = project['tool']['setuptools']['data-files']['share/doc/reprovane']
        for name in ('QUICKSTART_vi.md', 'USER_GUIDE_vi.md', 'MIGRATION_FROM_SCHOLARFLOW_1.md'):
            self.assertIn(name, docs)
            self.assertTrue((ROOT/name).is_file())

    def test_semantics_exact_public_v1_wheel(self):
        expected = json.loads((ROOT/'tests/fixtures/bootstrap/scholarflow_1_0_0_e2e.json').read_text(encoding='utf-8'))
        self.assertEqual(reprovane_e2e(), expected)


if __name__ == '__main__':
    unittest.main()
