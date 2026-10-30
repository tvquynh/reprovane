import argparse
import json
import sys
from pathlib import Path
from ..core.db import Database
from . import FullTextStore,verify_export
from .transport import SafeTransport,runtime_credentials


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m reprovane.fulltext_acquisition')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ['create','discover','acquire','materialize','finalize','inspect','verify','export','export-pdfs','verify-export']:
        p=sub.add_parser(name)
        if name=='verify-export':
            p.add_argument('--directory',type=Path,required=True); p.add_argument('--expected-manifest-sha256')
        else:
            p.add_argument('--workspace',type=Path,required=True)
            if name=='create': p.add_argument('--release-id',required=True); p.add_argument('--policy',type=Path,required=True)
            else: p.add_argument('--run-id',required=True)
            if name in ('export','export-pdfs'): p.add_argument('--directory',type=Path,required=True)
            if name=='acquire': p.add_argument('--expected-policy-sha256',required=True)
    a=parser.parse_args(argv)
    try:
        if a.command=='verify-export': result=verify_export(a.directory,expected_manifest_sha256=a.expected_manifest_sha256)
        else:
            with Database(a.workspace) as db:
                s=FullTextStore(db)
                if a.command=='create': result=s.create(a.release_id,json.loads(a.policy.read_text(encoding='utf-8-sig')))
                elif a.command in ('discover','acquire'):
                    transport=SafeTransport(s.policy(a.run_id),credentials=runtime_credentials())
                    result=s.discover(a.run_id,transport) if a.command=='discover' else s.acquire(a.run_id,transport,expected_policy_sha256=a.expected_policy_sha256)
                elif a.command in ('export','export-pdfs'): result=getattr(s,a.command.replace('-','_'))(a.run_id,a.directory)
                else: result=getattr(s,a.command)(a.run_id)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
    except Exception:
        print('Full-text operation failed safely. Verify frozen policy, sealed input, runtime configuration and integrity. Private values are not printed.',file=sys.stderr); return 2


_unmanaged_main = main

def main(argv=None):
    from ..operations.cli import module_main
    return module_main(_unmanaged_main, argv, globals())


if __name__=='__main__': raise SystemExit(main())
