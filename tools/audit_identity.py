"""Classify old identity references and reject stale canonical repository metadata."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OLD = 'scholar' + 'flow'
OLD_SLUG = 'tvquynh/' + OLD
HISTORICAL = {'CHANGELOG.md', 'MIGRATION_TO_SCHOLARFLOW.md',
              'OLD_REPOSITORY_ARCHIVE_NOTICE.md'}
MIGRATION_DOCS = {'README.md', 'QUICKSTART_vi.md', 'USER_GUIDE_vi.md',
                  'OPERATIONS.md', 'ARCHITECTURE.md', 'SECURITY.md',
                  'MIGRATION_FROM_SCHOLARFLOW_1.md', 'PROVENANCE.md',
                  'pyproject.toml', 'MANIFEST.in'}


def classify(name, line, *, historical_section=False):
    if name.startswith('reprovane/'):
        return 'BUG/STALE_CURRENT_IDENTITY'
    if name in HISTORICAL or name.startswith('docs/legacy/') or historical_section:
        return 'HISTORICAL_PROVENANCE'
    if '-writer.lock' in line:
        return 'WORKSPACE_COMPATIBILITY'
    if name.startswith(('tests/', 'tools/')):
        return 'TEST_ASSERTION'
    if name in MIGRATION_DOCS:
        if OLD_SLUG in line.lower() and not any(word in line.lower() for word in
                                               ('former', 'historical', 'redirect', 'trước', 'cũ', 'chuyển hướng')):
            return 'BUG/STALE_CURRENT_IDENTITY'
        return 'CURRENT_MIGRATION_DOC'
    return 'BUG/STALE_CURRENT_IDENTITY'


def stale_current_status(root=ROOT):
    patterns = ('nhánh đang chờ xét duyệt', 'đang được chuẩn bị để review',
                'kho GitHub chưa đổi tên', 'This PR remains', 'future in-place repository',
                '--branch rebrand/reprovane-2.0.0')
    result = []
    for name in ('README.md', 'QUICKSTART_vi.md', 'MIGRATION_FROM_SCHOLARFLOW_1.md',
                 'OPERATIONS.md', 'PROVENANCE.md'):
        text = (root/name).read_text(encoding='utf-8').split('## Historical ScholarFlow bootstrap record')[0]
        for number, line in enumerate(text.splitlines(), 1):
            if any(pattern in line for pattern in patterns):
                result.append(dict(file=name, line=number, context=line))
    return result


def audit(root=ROOT):
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    rows = []
    for name in filter(None, names):
        path = root/name
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except UnicodeDecodeError:
            continue
        historical_section = False
        for number, line in enumerate(lines, 1):
            if name == 'PROVENANCE.md' and line == '## Historical ScholarFlow bootstrap record':
                historical_section = True
            for match in re.finditer(OLD, line, re.I):
                rows.append(dict(file=name, line=number, column=match.start()+1,
                                 category=classify(name, line, historical_section=historical_section),
                                 context=line.strip()))
    return rows


if __name__ == '__main__':
    rows = audit()
    print(json.dumps(rows, indent=2, ensure_ascii=True))
    raise SystemExit(bool(stale_current_status()) or any(r['category'].startswith('BUG/') for r in rows))
