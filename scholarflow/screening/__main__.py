"""Offline screening CLI; never resolves network or AI credentials."""
import argparse,json,sys
from pathlib import Path
from ..core.db import Database
from .store import ScreeningStore


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m scholarflow.screening'); sub=parser.add_subparsers(dest='command',required=True)
    for name in ['create','inspect','export-review-packet','import-decisions','submit-decision','resolve','submit-adjudication','export-conflicts','finalize','verify','handoff']:
        p=sub.add_parser(name); p.add_argument('--workspace',type=Path,required=True)
        if name=='create': p.add_argument('--project-revision',required=True); p.add_argument('--discovery-run',required=True); p.add_argument('--plan',type=Path,required=True)
        else: p.add_argument('--run-id',required=True)
        if name=='export-review-packet': p.add_argument('--reviewer',required=True); p.add_argument('--directory',type=Path,required=True)
        if name=='import-decisions': p.add_argument('--reviewer',required=True); p.add_argument('--input',type=Path,required=True)
        if name in ('submit-decision','submit-adjudication'): p.add_argument('--input',type=Path,required=True)
        if name=='export-conflicts': p.add_argument('--adjudicator',required=True)
    args=parser.parse_args(argv)
    try:
        with Database(args.workspace) as db:
            s=ScreeningStore(db)
            if args.command=='create': result=s.create(args.project_revision,args.discovery_run,json.loads(args.plan.read_text(encoding='utf-8-sig')))
            elif args.command=='export-review-packet': result=s.export_review_packet(args.run_id,args.reviewer,args.directory)
            elif args.command=='import-decisions': result=s.import_decisions(args.run_id,args.reviewer,[json.loads(line) for line in args.input.read_text(encoding='utf-8-sig').splitlines() if line.strip()])
            elif args.command in ('submit-decision','submit-adjudication'): result=getattr(s,args.command.replace('-','_'))(args.run_id,**json.loads(args.input.read_text(encoding='utf-8-sig')))
            elif args.command=='export-conflicts': result=s.export_conflicts(args.run_id,args.adjudicator)
            else: result=getattr(s,args.command)(args.run_id)
            print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
    except Exception:
        print('Screening operation rejected safely; check frozen plan, actor, packet hashes and current supersession context. No submitted private data is printed.',file=sys.stderr); return 2


_unmanaged_main = main

def main(argv=None):
    from ..operations.cli import module_main
    return module_main(_unmanaged_main, argv, globals())


if __name__=='__main__': raise SystemExit(main())
