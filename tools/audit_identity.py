"""Classify every old-name occurrence in tracked UTF-8 files; fail closed on stale identity."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OLD = 'scholar' + 'flow'
HISTORICAL = {'CHANGELOG.md', 'MIGRATION_TO_SCHOLARFLOW.md',
              'OLD_REPOSITORY_ARCHIVE_NOTICE.md'}
TRANSITION = {'README.md', 'QUICKSTART_vi.md', 'USER_GUIDE_vi.md',
              'OPERATIONS.md', 'ARCHITECTURE.md', 'SECURITY.md',
              'MIGRATION_FROM_SCHOLARFLOW_1.md', 'PROVENANCE.md',
              'pyproject.toml', 'MANIFEST.in', 'tools/validate_ci_package.py',
              'tools/audit_identity.py', 'tests/test_rebrand.py',
              'tests/test_reprovane_bootstrap.py', 'tests/test_stage12_release_hardening.py'}


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
        for number, line in enumerate(lines, 1):
            matches = list(re.finditer(OLD, line, re.I))
            if not matches:
                continue
            category = 'BUG/STALE_CURRENT_IDENTITY'
            if name in HISTORICAL or name.startswith('docs/legacy/'):
                category = 'HISTORICAL_PROVENANCE'
            elif name == 'PROVENANCE.md':
                category = ('HISTORICAL_PROVENANCE' if any(x in line for x in
                            ('bootstrap', 'Legacy baseline', 'Stage11B', 'Stage12', 'ported to'))
                            else 'CURRENT_REBRAND_DOC')
            elif name in TRANSITION:
                category = 'WORKSPACE_COMPATIBILITY' if '-writer.lock' in line else 'CURRENT_REBRAND_DOC'
            elif name.startswith('reprovane/') and 'https://github.com/tvquynh/'+OLD in line:
                # Operational repository URL remains valid until a separately reviewed rename.
                if OLD not in line.replace('https://github.com/tvquynh/'+OLD, '').lower():
                    category = 'CURRENT_REBRAND_DOC'
            for match in matches:
                rows.append(dict(file=name, line=number, column=match.start()+1,
                                 category=category, context=line.strip()))
    return rows


if __name__ == '__main__':
    rows = audit()
    print(json.dumps(rows, indent=2, ensure_ascii=True))
    raise SystemExit(any(r['category'].startswith('BUG/') for r in rows))
