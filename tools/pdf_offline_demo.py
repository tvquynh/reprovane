"""Reproducible offline end-to-end demo of unified PDF acquisition (no network, synthetic data only).

Every step except the download uses the real `reprovane pdf` command parser and dispatcher. The download
step uses the same engine through the Python API with the in-memory web from tools/pdf_legacy_parity.py,
because the command-line download would contact real hosts.

    python tools/pdf_offline_demo.py --directory "C:/temp/pdf demo"
"""
import argparse
import contextlib
import csv
import importlib.util
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
spec = importlib.util.spec_from_file_location('pdf_legacy_parity', ROOT / 'tools' / 'pdf_legacy_parity.py')
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)


def cli(*args):
    from reprovane.pdf_acquisition.__main__ import main
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
        code = main([str(a) for a in args])
    if code:
        raise SystemExit(f'reprovane pdf {args[0]} failed with exit code {code}: {err.getvalue().strip()}')
    return json.loads(out.getvalue())


def offline_download(workspace, run_id):
    from reprovane.core.db import Database
    from reprovane.operations.lock import workspace_lock
    from reprovane.pdf_acquisition import PdfAcquisitionStore
    from reprovane.pdf_acquisition.engine import Downloader, make_transport
    from fulltext_acquisition_fixtures import Response

    def opener(url, address, headers, timeout):
        status, given, body = parity.ROUTES.get(url, (404, {}, b'not found'))
        if url.endswith('/robots.txt') and url not in parity.ROUTES:
            status, given, body = 200, {}, b'User-agent: *\nAllow: /\n'
        return Response(body, status, dict(given, **({} if 'Content-Length' in given else {'Content-Length': str(len(body))})))

    def dns(host, *args, **kwargs):
        return [(2, 1, 6, '', ('10.0.0.7' if host == 'internal.example.org' else '93.184.216.34', 443))]
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds
    with workspace_lock(workspace), Database(workspace) as db:
        store = PdfAcquisitionStore(db)
        transport = make_transport(store.policy(run_id), resolver=dns, opener=opener, sleeper=sleep)
        result = Downloader(store, run_id, transport, clock=lambda: clock[0], sleep=sleep).run()
        return {k: v for k, v in result.items() if k != 'run'}


def demo(directory):
    base = Path(directory)
    base.mkdir(parents=True, exist_ok=False)
    workspace = base / 'demo workspace'
    rows = parity.corpus()
    with (base / 'query_main.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manual = base / 'manual pdfs'
    manual.mkdir()
    for key, body in parity.MANUAL.items():
        (manual / (key + '.pdf')).write_bytes(body)
    (manual / 'unknown paper.pdf').write_bytes(parity.MANUAL['W8'][:-6] + b'%%EOF\n')
    steps = {}
    imported = cli('import', '--workspace', workspace, '--input', base / 'query_main.csv', '--label', 'offline demo')
    run_id = imported['run_id']
    steps['import'] = imported['batches'][0]['counts']
    steps['discover'] = {k: cli('discover', '--workspace', workspace, '--run-id', run_id)[k] for k in ('state', 'work_count', 'candidate_count')}
    steps['download_offline_engine'] = offline_download(workspace, run_id)
    steps['manual_import'] = cli('manual-import', '--workspace', workspace, '--run-id', run_id, '--input', manual)['counts']
    steps['reconciliation'] = cli('reconcile', '--workspace', workspace, '--run-id', run_id)['items']
    steps['status'] = cli('status', '--workspace', workspace, '--run-id', run_id)['summary']
    steps['verify'] = cli('verify', '--workspace', workspace, '--run-id', run_id)['checks']
    steps['close'] = cli('close', '--workspace', workspace, '--run-id', run_id)['manifest_sha256']
    steps['export'] = cli('export', '--workspace', workspace, '--run-id', run_id, '--directory', base / 'report')
    steps['verify_export'] = cli('verify-export', '--directory', base / 'report', '--expected-manifest-sha256', steps['close'])
    steps['export_pdfs'] = cli('export-pdfs', '--workspace', workspace, '--run-id', run_id, '--directory', base / 'pdf copies')
    steps['hosts'] = cli('hosts', '--workspace', workspace)['hosts']
    from reprovane.operations.workspace import doctor
    report = doctor(workspace, full=True)
    steps['doctor'] = dict(schema_version=report['schema_version'], pending=report['pending'], pdf_acquisition=report['pdf_acquisition'])
    return dict(run_id=run_id, directory_name=base.name, steps=steps)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline unified PDF acquisition demo')
    parser.add_argument('--directory', type=Path, required=True, help='New folder to create for the demo')
    args = parser.parse_args(argv)
    print(json.dumps(demo(args.directory), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
