"""Read-only gates for the two release distributions; never performs an upload."""
import ast
import configparser
from email.parser import Parser
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROJECT_URLS = {
    'Homepage': 'https://github.com/tvquynh/reprovane',
    'Repository': 'https://github.com/tvquynh/reprovane',
    'Issues': 'https://github.com/tvquynh/reprovane/issues',
    'Documentation': 'https://github.com/tvquynh/reprovane#documentation',
}


def source_version(root):
    tree = ast.parse((root/'reprovane/_version.py').read_text(encoding='utf-8'))
    values = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets)]
    if len(values) != 1:
        raise ValueError('Unexpected release version')
    return stable_version(values[0])


def stable_version(version):
    """Canonical numeric PEP 440 releases; no pre/dev/post/local/epoch aliases."""
    if not isinstance(version, str) or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*', version):
        raise ValueError('Expected a canonical stable release version')
    return version


def expected_distributions(version):
    stable_version(version)
    return {'reprovane-'+version+'-py3-none-any.whl', 'reprovane-'+version+'.tar.gz'}


def validate_handoff(directory, version):
    expected = expected_distributions(version)
    if {p.name for p in directory.iterdir()} != expected:
        raise ValueError('Unexpected distribution handoff')
    if any(not p.is_file() or p.is_symlink() for p in directory.iterdir()):
        raise ValueError('Unsafe distribution file')
    return expected


def validate_tag(tag, ref, event_sha, checkout_sha, version):
    stable_version(version)
    if tag != 'v'+version or ref != 'refs/tags/'+tag or not event_sha or event_sha != checkout_sha:
        raise ValueError('Release tag/version/ref/commit mismatch')


def metadata(text, version):
    message = Parser().parsestr(text)
    if message['Name'] != 'reprovane' or message['Version'] != version:
        raise ValueError('Release distribution identity mismatch')
    urls = dict(value.split(', ', 1) for value in message.get_all('Project-URL', []))
    if urls != PROJECT_URLS:
        raise ValueError('Release project URL mismatch')


def validate_archives(directory, version, expected_migrations):
    expected = validate_handoff(directory, version)
    for filename in sorted(expected):
        path = directory/filename
        if not path.is_file() or path.is_symlink():
            raise ValueError('Unsafe distribution file')
        if filename.endswith('.whl'):
            with zipfile.ZipFile(path) as archive:
                members = {n: archive.read(n) for n in archive.namelist() if not n.endswith('/')}
            prefix = ''
            metadata_name = 'reprovane-'+version+'.dist-info/METADATA'
            entry_name = 'reprovane-'+version+'.dist-info/entry_points.txt'
        else:
            with tarfile.open(path) as archive:
                if any(m.issym() or m.islnk() for m in archive.getmembers()):
                    raise ValueError('Archive links refused')
                members = {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
            prefix = 'reprovane-'+version+'/'
            metadata_name = prefix+'PKG-INFO'
            entry_name = prefix+'reprovane.egg-info/entry_points.txt'
        if prefix+'reprovane/__init__.py' not in members or any(n.startswith(prefix+'scholarflow/') for n in members):
            raise ValueError('Release namespace mismatch')
        metadata(members[metadata_name].decode('utf-8'), version)
        entries = configparser.ConfigParser()
        entries.read_string(members[entry_name].decode('utf-8'))
        if dict(entries['console_scripts']) != {'reprovane': 'reprovane.__main__:main'}:
            raise ValueError('Release console script mismatch')
        hashes = {Path(n).name: hashlib.sha256(data).hexdigest() for n, data in members.items()
                  if n.startswith(prefix+'reprovane/core/migrations/') and n.endswith('.sql')}
        if hashes != expected_migrations or len(hashes) != 13:
            raise ValueError('Immutable migration mismatch')


def main():
    version = source_version(ROOT)
    checkout = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    validate_tag(os.environ.get('RELEASE_TAG'), os.environ.get('RELEASE_REF'),
                 os.environ.get('RELEASE_SHA'), checkout, version)
    project = tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']
    if project['name'] != 'reprovane' or project['urls'] != PROJECT_URLS:
        raise ValueError('Project identity/URL mismatch')
    expected = json.loads((ROOT/'tests/fixtures/bootstrap/migration_hashes.json').read_text())
    validate_archives(ROOT/'dist', version, expected)
    print('RELEASE GATES PASS: exact tag/commit, reprovane identity, URLs, namespace, entrypoint and 13 migration hashes')


if __name__ == '__main__':
    main()
