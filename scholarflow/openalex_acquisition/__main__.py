"""Explicit acquire/resume/inspect CLI. Help/status never resolves credentials or calls HTTP."""
import argparse,json,sys
from pathlib import Path
import requests
from ..core import FoundationError
from . import AcquisitionSpec,OpenAlexAcquisitionStore,local_client
from ..core.db import Database


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m scholarflow.openalex_acquisition')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ['acquire','resume','inspect']:
        p=sub.add_parser(name); p.add_argument('--workspace',type=Path,required=True)
        if name!='inspect': p.add_argument('--spec',type=Path,required=True); p.add_argument('--config',type=Path)
        if name!='acquire': p.add_argument('--acquisition-id',required=True)
    args=parser.parse_args(argv)
    try:
        with Database(args.workspace) as db:
            store=OpenAlexAcquisitionStore(db)
            if args.command=='inspect': result=store.get(args.acquisition_id)
            else:
                spec=AcquisitionSpec.from_value(json.loads(args.spec.read_text(encoding='utf-8-sig')))
                with local_client(config_path=args.config) as client:
                    result=store.acquire(spec,client) if args.command=='acquire' else store.resume(args.acquisition_id,spec,client)
            print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
    except (FoundationError,RuntimeError,ValueError,OSError,requests.RequestException):
        print('OpenAlex acquisition incomplete or validation failed; inspect local status. Private values are never printed.',file=sys.stderr); return 2


_unmanaged_main = main

def main(argv=None):
    from ..operations.cli import module_main
    return module_main(_unmanaged_main, argv, globals())


if __name__=='__main__': raise SystemExit(main())
