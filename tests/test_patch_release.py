"""Maintenance release gates and PyPI README links; no publishing or network."""
import ast
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('patch_release_audit', ROOT/'tools/validate_release.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class TestPatchRelease(unittest.TestCase):
    def test_patch_source_identity(self):
        self.assertEqual(audit.source_version(ROOT), '2.0.1')

    def test_readme_links_absolute_and_targets_exist(self):
        text = (ROOT/'README.md').read_text(encoding='utf-8')
        links = re.findall(r'\[[^\]]+\]\(([^)]+)\)', text)
        self.assertGreaterEqual(len(links), 8)
        for url in links:
            self.assertTrue(url.startswith('https://github.com/tvquynh/reprovane'), url)
            if '/blob/main/' in url:
                target = url.split('/blob/main/', 1)[1]
                self.assertTrue((ROOT/target).is_file(), target)
        self.assertNotRegex(text, r'\]\((?!https://)[^)]+\)')

    def test_first_release_history_and_concise_readme(self):
        text = (ROOT/'README.md').read_text(encoding='utf-8')
        self.assertIn('2.0.0 was the first public release', text)
        self.assertNotIn('2.0.1 is the first', text)
        self.assertLess(len(text.split()), 450)

    def test_future_canonical_stable_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);(root/'reprovane').mkdir()
            for version in ['2.0.1', '2.0.2', '3.1.0', '12.3.4', '3.1']:
                (root/'reprovane/_version.py').write_text('__version__ = '+repr(version))
                self.assertEqual(audit.source_version(root), version)
                audit.validate_tag('v'+version, 'refs/tags/v'+version, 'a'*40, 'a'*40, version)

    def test_invalid_versions_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);(root/'reprovane').mkdir()
            for version in ['', 'arbitrary', '2..1', '2.0.1+local', '2.0.1rc1', '2.0.1.dev1', '2.0.1.post1', '1!2.0.1', '02.0.1', '../2.0.1', '2.0.1\n', None, 201]:
                with self.subTest(version=version), self.assertRaises(ValueError):
                    (root/'reprovane/_version.py').write_text('__version__ = '+repr(version))
                    audit.source_version(root)
            for source in ['', '__version__="2.0.1"\n__version__="2.0.2"']:
                (root/'reprovane/_version.py').write_text(source)
                with self.assertRaises(ValueError):audit.source_version(root)

    def test_exact_handoff_rejects_extra_missing_wrong_and_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);expected = audit.expected_distributions('2.0.1')
            for name in expected:(root/name).write_bytes(b'synthetic')
            self.assertEqual(audit.validate_handoff(root, '2.0.1'), expected)
            (root/'extra.txt').write_text('synthetic')
            with self.assertRaises(ValueError):audit.validate_handoff(root, '2.0.1')
            (root/'extra.txt').unlink()
            with self.assertRaises(ValueError):audit.validate_handoff(root, '2.0.2')
            name=next(iter(expected));(root/name).unlink()
            with self.assertRaises(ValueError):audit.validate_handoff(root, '2.0.1')
            (root/name).mkdir()
            with self.assertRaises(ValueError):audit.validate_handoff(root, '2.0.1')

    def test_actual_publish_inline_gate(self):
        text = (ROOT/'.github/workflows/release.yml').read_text()
        code = textwrap.dedent(text.split("          python - <<'PY'\n", 1)[1].split('          PY', 1)[0])
        ast.parse(code)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);(root/'dist').mkdir()
            for name in audit.expected_distributions('2.0.1'):(root/'dist'/name).write_bytes(b'synthetic')
            def run(tag):
                return subprocess.run([sys.executable, '-c', code], cwd=root,
                                      env=dict(os.environ, RELEASE_TAG=tag), capture_output=True).returncode
            self.assertEqual(run('v2.0.1'), 0)
            for tag in ['v2.0.0', 'v2.0.2', 'v2.0.1+local', 'v../2.0.1', '', '2.0.1']:
                with self.subTest(tag=tag):self.assertNotEqual(run(tag), 0)
            (root/'dist/extra.txt').write_text('synthetic')
            self.assertNotEqual(run('v2.0.1'), 0)

    def test_wrong_tag_version_ref_sha_rejected(self):
        for tag, ref, event, checkout in [('v2.0.0','refs/tags/v2.0.0','a','a'), ('v2.0.1','refs/heads/main','a','a'), ('v2.0.1','refs/tags/v2.0.1','a','b')]:
            with self.assertRaises(ValueError):audit.validate_tag(tag, ref, event, checkout, '2.0.1')

    def test_release_workflow_remains_tokenless(self):
        text = (ROOT/'.github/workflows/release.yml').read_text()
        build, publish = text.split('  publish-pypi:', 1)
        self.assertNotIn('2.0.0', text)
        self.assertNotIn('id-token:', build)
        self.assertEqual(publish.count('id-token: write'), 1)
        for forbidden in ['secrets.', 'password:', 'repository-url:', 'workflow_dispatch', 'contents: write']:
            self.assertNotIn(forbidden, text)
        self.assertIn('needs: build', publish)
        self.assertIn('name: pypi', publish)


if __name__ == '__main__':unittest.main()
