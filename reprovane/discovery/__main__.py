"""Explicit one-hop discovery operations; no credential resolution on help/inspect."""
import argparse,json,sys
from pathlib import Path
from ..core import FoundationError
from ..core.db import Database
from ..openalex_acquisition import local_client
from . import DiscoveryStore


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m reprovane.discovery'); sub=parser.add_subparsers(dest='command',required=True)
    for name in ['create','register-primary','resolve-seeds','expand-backward','expand-forward','materialize','inspect','seal']:
        p=sub.add_parser(name); p.add_argument('--workspace',type=Path,required=True)
        if name=='create': p.add_argument('--project-revision',required=True); p.add_argument('--parent-run')
        else: p.add_argument('--run-id',required=True)
        if name=='register-primary': p.add_argument('--acquisition-id',required=True)
        if name.startswith('expand-'):
            p.add_argument('--seeds',type=Path,required=True); p.add_argument('--max-results',type=int,required=True); p.add_argument('--batch-size',type=int,default=100); p.add_argument('--config',type=Path)
    args=parser.parse_args(argv)
    try:
        with Database(args.workspace) as db:
            store=DiscoveryStore(db)
            if args.command=='create': result=store.create(args.project_revision,parent_run_id=args.parent_run)
            elif args.command=='register-primary': result=store.register_primary(args.run_id,args.acquisition_id)
            elif args.command=='resolve-seeds': result=store.resolve_seeds(args.run_id)
            elif args.command.startswith('expand-'):
                seeds=json.loads(args.seeds.read_text(encoding='utf-8-sig'))
                with local_client(config_path=args.config) as client:
                    result=store.expand_backward(args.run_id,seeds,client,max_results=args.max_results,batch_size=args.batch_size) if args.command=='expand-backward' else store.expand_forward(args.run_id,seeds,client,max_results=args.max_results,page_size=args.batch_size)
            else: result=getattr(store,args.command)(args.run_id)
            print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
    except Exception:
        print('Discovery operation failed closed; inspect persisted run/child acquisition state. Private values are not printed.',file=sys.stderr); return 2


_unmanaged_main = main

def main(argv=None):
    from ..operations.cli import module_main
    return module_main(_unmanaged_main, argv, globals())


if __name__=='__main__': raise SystemExit(main())
