import argparse,json,sys
from pathlib import Path
from ..core.db import Database
from .store import CorpusReleaseStore
from .portable import verify_export


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m reprovane.corpus_release'); sub=parser.add_subparsers(dest='command',required=True)
    for name in ['create','materialize','inspect','finalize','verify','export','verify-export','handoff']:
        p=sub.add_parser(name)
        if name=='verify-export': p.add_argument('--directory',type=Path,required=True); p.add_argument('--expected-manifest-sha256')
        else:
            p.add_argument('--workspace',type=Path,required=True)
            if name=='create': p.add_argument('--project-revision',required=True); p.add_argument('--screening-run',required=True); p.add_argument('--plan',type=Path,required=True)
            else: p.add_argument('--release-id',required=True)
            if name=='export': p.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='verify-export': result=verify_export(args.directory,expected_manifest_sha256=args.expected_manifest_sha256)
        else:
            with Database(args.workspace) as db:
                s=CorpusReleaseStore(db)
                if args.command=='create': result=s.create(args.project_revision,args.screening_run,json.loads(args.plan.read_text(encoding='utf-8-sig')))
                elif args.command=='export': result=s.export(args.release_id,args.directory)
                else: result=getattr(s,args.command)(args.release_id)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
    except Exception:
        print('Corpus release operation failed safely; verify sealed upstream state, plan, snapshots and exact export contents. Private input is not printed.',file=sys.stderr); return 2


_unmanaged_main = main

def main(argv=None):
    from ..operations.cli import module_main
    return module_main(_unmanaged_main, argv, globals())


if __name__=='__main__': raise SystemExit(main())
