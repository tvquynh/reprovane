"""Current English guidance and real offline beginner commands."""
import importlib.util
import json
from pathlib import Path
import re
import tomllib
import unittest

ROOT=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tools'/('audit_documentation.py' if name=='docs_audit' else 'validate_beginner_walkthrough.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
audit=load('docs_audit')


class TestEnglishDocumentation(unittest.TestCase):
    def test_canonical_guides(self):
        for name in ('QUICKSTART.md','USER_GUIDE.md'):
            text=(ROOT/name).read_text(encoding='utf-8');self.assertGreater(len(text.split()),1500);self.assertEqual(audit.language_findings(text),[])

    def test_legacy_paths_are_english_redirect_stubs(self):
        for old,new in [('QUICKSTART_vi.md','QUICKSTART.md'),('USER_GUIDE_vi.md','USER_GUIDE.md')]:
            text=(ROOT/old).read_text(encoding='utf-8');self.assertLess(len(text.split()),70);self.assertIn('/blob/main/'+new,text);self.assertIn('2.0.0',text);self.assertEqual(audit.language_findings(text),[])

    def test_readme_english_canonical_links(self):
        text=(ROOT/'README.md').read_text(encoding='utf-8');self.assertEqual(audit.language_findings(text),[])
        self.assertNotIn('_vi.md',text);self.assertIn('Beginner Quick Start',text)
        for name in ('QUICKSTART.md','USER_GUIDE.md'):self.assertIn('https://github.com/tvquynh/reprovane/blob/main/'+name,text)
        for url in audit.markdown_links(text):self.assertTrue(url.startswith('https://github.com/tvquynh/reprovane'))

    def test_all_public_documentation_english(self):
        docs=audit.public_docs(ROOT);self.assertGreater(len(docs),30)
        self.assertEqual([(p.name,audit.language_findings(p.read_text(encoding='utf-8'))) for p in docs if audit.language_findings(p.read_text(encoding='utf-8'))],[])

    def test_language_detector_catches_diacritics_and_ascii(self):
        self.assertTrue(audit.language_findings('H\u01b0\u1edbng d\u1eabn'))
        self.assertTrue(audit.language_findings('huong dan cai dat'))
        self.assertFalse(audit.language_findings('Install Python and create a workspace.'))

    def test_public_file_links_and_headings(self):
        for p in audit.public_docs(ROOT):
            if p.suffix=='.md':self.assertEqual(audit.link_findings(p,ROOT),[],str(p.relative_to(ROOT)))

    def test_every_documented_cli_parses_without_dispatch(self):
        count=0
        for name in ('README.md','QUICKSTART.md','USER_GUIDE.md','OPERATIONS.md','MIGRATION_FROM_SCHOLARFLOW_1.md'):
            for shell,text,args in audit.cli_commands(ROOT/name):
                with self.subTest(document=name,command=text):self.assertTrue(audit.validate_command(args))
                count+=1
        self.assertGreater(count,70)

    def test_packaging_includes_canonical_english_guides(self):
        files=tomllib.loads((ROOT/'pyproject.toml').read_text())['tool']['setuptools']['data-files']['share/doc/reprovane']
        for name in ('QUICKSTART.md','USER_GUIDE.md','QUICKSTART_vi.md','USER_GUIDE_vi.md'):self.assertIn(name,files)
        self.assertIn('QUICKSTART.md USER_GUIDE.md',(ROOT/'MANIFEST.in').read_text())

    def test_beginner_documented_walkthrough(self):
        self.assertEqual(load('beginner_smoke').walkthrough()['status'],'PASS')


if __name__=='__main__':unittest.main()
