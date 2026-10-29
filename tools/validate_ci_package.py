"""Inspect isolated PEP 517 outputs and smoke wheel/sdist outside checkout."""
from email.parser import Parser
import configparser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def check_names(names):
    forbidden = {'.git', '.env', 'build', 'dist', 'tests', 'data', 'cache', '__pycache__'}
    for name in names:
        path = PurePosixPath(name)
        # Wheel data-files use <distribution>.data/data/share/doc/..., per wheel layout.
        parts = path.parts
        if len(parts) > 5 and parts[0].endswith('.data') and parts[1:5] == ('data', 'share', 'doc', 'scholarflow'):
            parts = parts[2:]
        if path.is_absolute() or '..' in parts or forbidden.intersection(parts):
            raise AssertionError('Unexpected package content: '+name)
        if path.suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.pdf', '.zip', '.pyc'):
            raise AssertionError('Unexpected package artifact: '+name)
        if 'litintel' in path.parts:
            raise AssertionError('Predecessor production namespace packaged')


def identity(text):
    metadata = Parser().parsestr(text)
    assert metadata['Name'] == 'scholarflow'
    assert metadata['Version'] == '1.0.0'


def main():
    wheels = list((ROOT/'dist').glob('*.whl'))
    sources = list((ROOT/'dist').glob('*.tar.gz'))
    assert len(wheels) == len(sources) == 1
    wheel, source = wheels[0], sources[0]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        check_names(names)
        identity(archive.read(next(n for n in names if n.endswith('.dist-info/METADATA'))).decode())
        entry = configparser.ConfigParser()
        entry.read_string(archive.read(next(n for n in names if n.endswith('.dist-info/entry_points.txt'))).decode())
        assert dict(entry['console_scripts']) == {'scholarflow': 'scholarflow.__main__:main'}
        assert len([n for n in names if n.startswith('scholarflow/core/migrations/') and n.endswith('.sql')]) == 13
    with tarfile.open(source) as archive:
        check_names(archive.getnames())
        metadata = archive.extractfile(source.name.removesuffix('.tar.gz')+'/PKG-INFO')
        assert metadata is not None
        identity(metadata.read().decode())
    for artifact in (wheel, source):
        print('SHA256', hashlib.sha256(artifact.read_bytes()).hexdigest(), artifact.name, flush=True)
        with tempfile.TemporaryDirectory(prefix='scholarflow-package-') as folder:
            base = Path(folder)
            venv.EnvBuilder(with_pip=True).create(base/'venv')
            scripts = base/'venv'/('Scripts' if os.name == 'nt' else 'bin')
            python = scripts/('python.exe' if os.name == 'nt' else 'python')
            subprocess.run([str(python), '-m', 'pip', 'install', str(artifact)], cwd=base, check=True)
            guard = base/'guard'
            guard.mkdir()
            shutil.copyfile(ROOT/'tools/ci_network_guard.py', guard/'ci_network_guard.py')
            (guard/'sitecustomize.py').write_text('from ci_network_guard import install\ninstall()\n', encoding='utf-8')
            env = dict(os.environ, PYTHONPATH=str(guard), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
            for key in ('OPENALEX_API_KEY', 'UNPAYWALL_EMAIL', 'GH_TOKEN', 'GITHUB_TOKEN'):
                env.pop(key, None)
            code = (
                'import importlib.metadata as m,json,pathlib,sys,scholarflow; '
                "assert m.version('scholarflow')=='1.0.0'; "
                'assert pathlib.Path(sys.prefix) in pathlib.Path(scholarflow.__file__).parents; '
                "print(json.dumps({'name':m.metadata('scholarflow')['Name'],'version':m.version('scholarflow'),'noneditable':True,'outside_checkout':True}))"
            )
            subprocess.run([str(python), '-c', code], cwd=base, env=env, check=True)
            cli = scripts/('scholarflow.exe' if os.name == 'nt' else 'scholarflow')
            for args in (['--version'], ['--help'], ['doctor', '--offline']):
                subprocess.run([str(cli), *args], cwd=base, env=env, check=True)
            print('Clean install PASS:', artifact.name, flush=True)
    print('PACKAGE VALIDATION PASS', flush=True)


if __name__ == '__main__':
    main()
