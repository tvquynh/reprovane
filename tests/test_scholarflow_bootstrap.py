"""Successor identity, resource compatibility and unchanged research semantics."""
import ast,contextlib,hashlib,importlib.util,io,json,subprocess,sys,tempfile,tomllib,unittest
from pathlib import Path
from unittest.mock import patch
import scholarflow
from scholarflow.__main__ import main,ROUTES
from scholarflow.core.migrations import MIGRATION_DIR
from scholarflow.operations.workspace import initialize,doctor
from scholarflow.fulltext_acquisition import DEFAULT_POLICY
from scholarflow_bootstrap_fixtures import scholarflow_e2e
from stage12_fixtures import e2e

ROOT=Path(__file__).parents[1]


def assert_source_hygiene(root):
    """Check distributed source, not ignored state of a developer checkout."""
    if (root/'.git').exists():
        names=subprocess.check_output(['git','-C',str(root),'ls-files','--cached','-z'],stderr=subprocess.PIPE).decode('utf-8').split('\0')
    else:
        names=[p.relative_to(root).as_posix() for p in root.rglob('*')]
    for name in filter(None,names):
        if {'.git','dist','build','.env'}.intersection(Path(name).parts):
            raise AssertionError('Forbidden distributed source baggage: '+name)


class TestScholarFlowIdentity(unittest.TestCase):
    def test_version(self):self.assertEqual(scholarflow.__version__,'1.0.0rc1')
    def test_distribution(self):self.assertEqual(tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['name'],'scholarflow')
    def test_only_console_script(self):self.assertEqual(tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['scripts'],{'scholarflow':'scholarflow.__main__:main'})
    def test_version_source(self):self.assertEqual(tomllib.loads((ROOT/'pyproject.toml').read_text())['tool']['setuptools']['dynamic']['version']['attr'],'scholarflow._version.__version__')
    def test_readme_product(self):self.assertTrue((ROOT/'README.md').read_text(encoding='utf-8').startswith('# ScholarFlow\n'))
    def test_no_canonical_old_package(self):self.assertFalse((ROOT/'litintel').exists())
    def test_no_root_predecessor_cli(self):self.assertFalse((ROOT/'literature_pipeline.py').exists())
    def test_no_v5_migration_guide(self):self.assertFalse((ROOT/'MIGRATION_V4_TO_V5.md').exists());self.assertTrue((ROOT/'MIGRATION_TO_SCHOLARFLOW.md').is_file())
    def test_no_git_or_build_baggage(self):
        assert_source_hygiene(ROOT)
    def test_predecessor_provenance(self):
        text=(ROOT/'PROVENANCE.md').read_text();self.assertIn('6ad3dde25d2a7a6e21591e8e6f131f3885b62ce7',text);self.assertIn('f70796d1ef6003e24ce3afa1328088cc515dc0a0',text)
    def test_archive_notice_is_draft(self):self.assertIn('unpublished draft',(ROOT/'OLD_REPOSITORY_ARCHIVE_NOTICE.md').read_text())
    def test_product_not_v5(self):
        for name in ('README.md','OPERATIONS.md','ARCHITECTURE.md','SECURITY.md','CHANGELOG.md','pyproject.toml'):
            text=(ROOT/name).read_text(encoding='utf-8');self.assertNotIn('5.0.0rc1',text);self.assertNotIn('litintel-v4',text)
    def test_namespace_import_graph(self):
        for path in (ROOT/'scholarflow').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node,ast.Import):self.assertTrue(all(a.name.split('.')[0]!='litintel' for a in node.names))
                if isinstance(node,ast.ImportFrom):self.assertFalse((node.module or '').startswith('litintel'))
    def test_old_package_not_required(self):
        code="import sys; sys.modules['litintel']=None; import scholarflow; from scholarflow.operations.workspace import doctor; assert doctor()['offline']; print(scholarflow.__version__)"
        p=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True);self.assertEqual(p.returncode,0);self.assertEqual(p.stdout.strip(),'1.0.0rc1')
    def test_current_provider_modules_retained(self):
        for name in ('openalex_acquisition','venue_intelligence','providers','discovery','screening','corpus_release','fulltext_acquisition'):self.assertTrue((ROOT/'scholarflow'/name).exists())
    def test_schema_distinct_from_version(self):
        with tempfile.TemporaryDirectory() as folder:
            result=initialize(Path(folder));self.assertEqual(result['schema_version'],13);self.assertEqual(scholarflow.__version__,'1.0.0rc1')
    def test_sql_bytes_and_order_preserved(self):
        expected=json.loads((ROOT/'tests/fixtures/bootstrap/migration_hashes.json').read_text());actual={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(MIGRATION_DIR.glob('*.sql'))};self.assertEqual(actual,expected);self.assertEqual(len(actual),13)
    def test_schema13_hash(self):self.assertEqual(hashlib.sha256((MIGRATION_DIR/'0013_legacy_v4_migration.sql').read_bytes()).hexdigest(),'7d85a8e2a82cc12514b10aa78b5276d32e24c427f778b15f874e01edc35043ff')
    def test_no_schema14(self):self.assertFalse(any(MIGRATION_DIR.glob('0014*')))
    def test_workspace_filenames_are_compatibility_locators(self):
        with tempfile.TemporaryDirectory() as folder:
            initialize(Path(folder));self.assertTrue((Path(folder)/'db/litintel.sqlite').exists());self.assertEqual(doctor(folder)['schema_version'],13)
    def test_no_submitted_unknown_policy_relaxation(self):self.assertEqual(DEFAULT_POLICY['allowed_versions'],['publishedVersion','acceptedVersion']);self.assertFalse(DEFAULT_POLICY['allow_unknown_version'])
    def test_no_runtime_reference_cli_packaging(self):
        p=tomllib.loads((ROOT/'pyproject.toml').read_text());self.assertNotIn('py-modules',p['tool']['setuptools']);self.assertEqual(p['tool']['setuptools']['packages']['find']['include'],['scholarflow*'])


class TestSourceExportHygiene(unittest.TestCase):
    def test_clean_export_without_git(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'README.md').write_text('synthetic export')
            assert_source_hygiene(root)

    def test_export_rejects_each_original_baggage_name(self):
        for name in ('.git','dist','build','.env'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);(root/name).mkdir()
                error=subprocess.CalledProcessError if name=='.git' else AssertionError
                with self.assertRaises(error):assert_source_hygiene(root)

    def test_export_rejects_nested_baggage(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'nested/build').mkdir(parents=True)
            with self.assertRaises(AssertionError):assert_source_hygiene(root)

    def test_checkout_allows_untracked_metadata_and_build_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            subprocess.run(['git','init','--quiet',str(root)],check=True)
            (root/'README.md').write_text('synthetic tracked source')
            subprocess.run(['git','-C',str(root),'add','README.md'],check=True)
            for name in ('build','dist'):(root/name).mkdir()
            assert_source_hygiene(root)

    def test_checkout_rejects_tracked_baggage(self):
        for name in ('dist/artifact','build/artifact','.env'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                subprocess.run(['git','init','--quiet',str(root)],check=True)
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('synthetic forbidden artifact')
                subprocess.run(['git','-C',str(root),'add','--force',name],check=True)
                with self.assertRaises(AssertionError):assert_source_hygiene(root)


class TestScholarFlowCLI(unittest.TestCase):
    def test_version_only_offline(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),self.assertRaises(SystemExit) as ctx,contextlib.redirect_stdout(io.StringIO()) as out:main(['--version'])
        self.assertEqual(ctx.exception.code,0);self.assertEqual(out.getvalue(),'1.0.0rc1\n')
    def test_help_product(self):
        with self.assertRaises(SystemExit),contextlib.redirect_stdout(io.StringIO()) as out:main(['--help'])
        self.assertIn('ScholarFlow',out.getvalue());self.assertNotIn('compat =',out.getvalue())
    def test_compat_is_not_product_command(self):
        with self.assertRaises(SystemExit) as ctx,contextlib.redirect_stderr(io.StringIO()):main(['compat','--help'])
        self.assertEqual(ctx.exception.code,2)
    def test_doctor_offline(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),contextlib.redirect_stdout(io.StringIO()) as out:code=main(['doctor','--offline'])
        self.assertEqual(code,0);self.assertTrue(json.loads(out.getvalue())['offline'])


def help_test(family):
    def test(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),self.assertRaises(SystemExit) as ctx,contextlib.redirect_stdout(io.StringIO()):main([family,'--help'])
        self.assertEqual(ctx.exception.code,0)
    return test
for family in [*ROUTES,'project','workspace','legacy','resolution','venue-resolution','scimago']:setattr(TestScholarFlowCLI,'test_help_'+family.replace('-','_'),help_test(family))


class TestScholarFlowResearchSemantics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=e2e();cls.new=scholarflow_e2e();cls.again=scholarflow_e2e()
    def test_predecessor_semantic_hashes_exact(self):self.assertEqual(self.original,json.loads((ROOT/'tests/fixtures/bootstrap/predecessor_e2e.json').read_text(encoding='utf-8')))
    def test_two_fresh_workspaces_exact(self):self.assertEqual(self.new,self.again)
    def test_human_adjudication(self):self.assertEqual(self.new['adjudication_count'],1)
    def test_backward_citation_route(self):self.assertGreaterEqual(self.new['backward_events'],1)
    def test_fulltext_failure_keeps_member(self):self.assertEqual((self.new['corpus_items'],self.new['auto_acquired'],self.new['manual_queue']),(3,2,1))
    def test_all_sealed_layers(self):self.assertTrue(all(v['sealed_count']==v['verified_count'] and v['verified_count']>0 for v in self.new['all_sealed_layers'].values()))
    def test_legacy_not_promoted(self):self.assertFalse(any(self.new['zero_modern_authority_after_legacy'].values()))
    def test_source_unchanged(self):self.assertTrue(self.new['source_unchanged'])
    def test_provider_semantics_separate(self):self.assertTrue(self.new['provider_membership_not_invented'])


if __name__=='__main__':unittest.main()
