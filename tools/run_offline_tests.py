"""Run full unittest suite without credentials, home config or internet.

Loopback is allowed for synthetic HTTP-server regressions. Python children
inherit the audit guard through sitecustomize.
"""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    for name in ('OPENALEX_API_KEY', 'UNPAYWALL_EMAIL', 'GITHUB_TOKEN',
                 'GH_TOKEN', 'OPENAI_API_KEY', 'PYTHONPATH'):
        os.environ.pop(name, None)
    with tempfile.TemporaryDirectory(prefix='scholarflow-offline-') as folder:
        home = Path(folder)
        for name in ('HOME', 'USERPROFILE', 'XDG_CONFIG_HOME', 'APPDATA'):
            os.environ[name] = str(home)
        os.environ['GIT_CONFIG_GLOBAL'] = str(home/'empty-gitconfig')
        os.environ['GIT_CONFIG_NOSYSTEM'] = '1'
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
        os.environ['PYTHONUTF8'] = '1'
        shutil.copyfile(ROOT/'tools/ci_network_guard.py', home/'ci_network_guard.py')
        (home/'sitecustomize.py').write_text(
            'from ci_network_guard import install\ninstall()\n', encoding='utf-8')
        os.environ['PYTHONPATH'] = os.pathsep.join((str(home), str(ROOT)))
        sys.path.insert(0, str(ROOT))
        from ci_network_guard import install
        install()
        suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'))
        print('Network gate: loopback only; credentials and home configuration cleared.', flush=True)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == '__main__':
    raise SystemExit(main())
