import argparse,json,sys
from pathlib import Path
from ..core.db import Database
from ..openalex_acquisition import local_client
from .store import FrontierStore


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m scholarflow.frontier'); sub=parser.add_subparsers(dest='command',required=True)
    for name in ['create','plan-next','run-next','run-to-policy-stop','inspect','finalize']:
        p=sub.add_parser(name); p.add_argument('--workspace',type=Path,required=True)
        if name=='create': p.add_argument('--project-revision',required=True); p.add_argument('--discovery-run',required=True); p.add_argument('--policy',type=Path,required=True)
        else: p.add_argument('--run-id',required=True)
        if name in ('run-next','run-to-policy-stop'): p.add_argument('--config',type=Path)
    args=parser.parse_args(argv)
    try:
        with Database(args.workspace) as db:
            store=FrontierStore(db)
            if args.command=='create': result=store.create(args.project_revision,args.discovery_run,json.loads(args.policy.read_text(encoding='utf-8-sig')))
            elif args.command in ('run-next','run-to-policy-stop'):
                with local_client(config_path=args.config) as client: result=getattr(store,args.command.replace('-','_'))(args.run_id,client)
            else: result=getattr(store,args.command.replace('-','_'))(args.run_id)
            print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
    except Exception:
        print('Frontier operation failed safely; inspect frozen round and Stage08 child state.',file=sys.stderr); return 2


_unmanaged_main = main

def main(argv=None):
    from ..operations.cli import module_main
    return module_main(_unmanaged_main, argv, globals())


if __name__=='__main__': raise SystemExit(main())
