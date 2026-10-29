import argparse,json,sys
from pathlib import Path
from ..core.db import Database
from ..openalex_acquisition import local_client
from .store import SearchValidationStore


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m scholarflow.search_validation'); sub=parser.add_subparsers(dest='command',required=True)
    for name in ['create','execute','inspect','finalize']:
        p=sub.add_parser(name); p.add_argument('--workspace',type=Path,required=True)
        if name=='create': p.add_argument('--project-revision',required=True); p.add_argument('--plan',type=Path,required=True)
        else: p.add_argument('--run-id',required=True)
        if name=='execute': p.add_argument('--config',type=Path)
    args=parser.parse_args(argv)
    try:
        with Database(args.workspace) as db:
            store=SearchValidationStore(db)
            if args.command=='create':
                plan=json.loads(args.plan.read_text(encoding='utf-8-sig')); result=store.create(args.project_revision,**plan)
            elif args.command=='execute':
                with local_client(config_path=args.config) as client: result=store.execute(args.run_id,client)
            else: result=getattr(store,args.command)(args.run_id)
            print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
    except Exception:
        print('Search validation failed safely; inspect persisted plan and acquisition state.',file=sys.stderr); return 2


_unmanaged_main = main

def main(argv=None):
    from ..operations.cli import module_main
    return module_main(_unmanaged_main, argv, globals())


if __name__=='__main__': raise SystemExit(main())
