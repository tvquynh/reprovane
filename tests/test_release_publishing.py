"""Release identity/OIDC gates, without requesting credentials or publishing."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_audit', ROOT/'tools/validate_release.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
WORKFLOW = ROOT/'.github/workflows/release.yml'


class TestReleasePublishing(unittest.TestCase):
    def test_project_urls_exact(self):
        self.assertEqual(tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['urls'], audit.PROJECT_URLS)

    def test_only_release_published_trigger(self):
        text = WORKFLOW.read_text()
        trigger = text.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertEqual(trigger.strip(), 'release:\n    types: [published]')
        self.assertNotIn('workflow_dispatch', text)
        self.assertNotIn('pull_request_target', text)
        self.assertNotIn('issue_comment', text)

    def test_minimum_oidc_permissions_and_environment(self):
        text = WORKFLOW.read_text()
        build, publish = text.split('  publish-pypi:', 1)
        self.assertNotIn('id-token:', build)
        self.assertEqual(text.count('id-token: write'), 1)
        self.assertIn('needs: build', publish)
        self.assertIn('name: pypi', publish)
        self.assertIn('url: https://pypi.org/p/reprovane', publish)
        self.assertNotIn('secrets.', text)
        for unsafe in ('contents: write', 'packages: write', 'actions: write', 'pull-requests: write', 'issues: write', 'password:', 'user:', 'repository-url:', 'attestations: false'):
            self.assertNotIn(unsafe, text)

    def test_official_actions_are_immutable(self):
        text = WORKFLOW.read_text()
        pins = {
            'actions/checkout': '3d3c42e5aac5ba805825da76410c181273ba90b1',
            'actions/setup-python': '6d4fd22b20465ccc0f7354476588e8df7c9ca0e0',
            'actions/upload-artifact': 'cf430e030ddbb5b0abf93d22962f4752f3646cd9',
            'actions/download-artifact': '9000827ccba6bdab643e8b6fd33ac0654aef8333',
            'pypa/gh-action-pypi-publish': '7245906fb61fa33a13554f9d5fb856fe61758086',
        }
        self.assertEqual(text.count('uses:'), len(pins))
        for name, sha in pins.items():
            self.assertIn('uses: '+name+'@'+sha, text)
        self.assertIn('persist-credentials: false', text)
        self.assertIn('ref: ${{ github.sha }}', text)
        self.assertIn('git merge-base --is-ancestor "$GITHUB_SHA" origin/main', text)

    def test_exact_artifact_handoff_and_no_checkout_in_publisher(self):
        text = WORKFLOW.read_text()
        build, publish = text.split('  publish-pypi:', 1)
        self.assertIn('path: dist/', build)
        self.assertIn('run: python tools/validate_release.py', build)
        self.assertIn("version = tag[1:]", publish)
        self.assertIn("RELEASE_TAG: ${{ github.event.release.tag_name }}", publish)
        self.assertNotIn('actions/checkout', publish)
        self.assertEqual(text.count('name: reprovane-pypi-distributions'), 2)
        self.assertIn('assert {p.name for p in root.iterdir()} == expected', publish)

    def test_release_tag_matches_version_and_exact_event_commit(self):
        version = audit.source_version(ROOT)
        self.assertEqual(version, '2.0.1')
        audit.validate_tag('v2.0.1', 'refs/tags/v2.0.1', 'a'*40, 'a'*40, version)
        text = WORKFLOW.read_text()
        for field in ('RELEASE_TAG:', 'RELEASE_REF:', 'RELEASE_SHA:'):
            self.assertIn(field, text)
        self.assertIn('run: python tools/validate_release.py', text)
        self.assertIn('run: python tools/validate_ci_package.py', text)

    def test_wrong_tag_ref_or_sha_fails_closed(self):
        for tag, ref, event, checkout in [('v1.0.0','refs/tags/v1.0.0','a','a'), ('v2.0.0','refs/heads/main','a','a'), ('v2.0.0','refs/tags/v2.0.0','a','b'), (None,None,None,'a')]:
            with self.subTest(tag=tag, ref=ref), self.assertRaises(ValueError):
                audit.validate_tag(tag, ref, event, checkout, '2.0.0')

    def test_metadata_rejects_wrong_distribution_or_urls(self):
        text = 'Name: reprovane\nVersion: 2.0.0\n'+''.join('Project-URL: '+k+', '+v+'\n' for k,v in audit.PROJECT_URLS.items())
        audit.metadata(text, '2.0.0')
        for bad in (text.replace('Name: reprovane','Name: wrong'),text.replace('/issues','/wrong')):
            with self.assertRaises(ValueError):
                audit.metadata(bad, '2.0.0')

    def test_unexpected_artifact_fails_before_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'unexpected.txt').write_text('synthetic')
            with self.assertRaises(ValueError):
                audit.validate_archives(root, '2.0.0', {})


if __name__ == '__main__':
    unittest.main()
