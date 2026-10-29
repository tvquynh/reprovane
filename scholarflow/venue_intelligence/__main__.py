"""Explicit feature CLI; existing v4 commands/config/credential loading stay isolated."""
import argparse,json,sys
from pathlib import Path
from ..core import FoundationError
from ..core.db import Database
from ..mirror import MirrorSyncError
from . import VenueIntelligenceStore,VenueIntelligence,sync_venue_lists,import_venue_lists,resolve_imported_snapshots,SCOPUS_PATH


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m scholarflow.venue_intelligence',description='Pinned Scopus/WoS data and explicit venue diagnostics')
    sub=parser.add_subparsers(dest='command',required=True)
    sync=sub.add_parser('sync'); sync.add_argument('--workspace',type=Path,required=True); sync.add_argument('--ref',default='control'); sync.add_argument('--offline',action='store_true'); sync.add_argument('--scopus-path',default=SCOPUS_PATH)
    imp=sub.add_parser('import'); imp.add_argument('--workspace',type=Path,required=True); imp.add_argument('--snapshot-directory',type=Path,required=True); imp.add_argument('--provider',choices=['all','scopus','wos'],default='all'); imp.add_argument('--scopus-path',default=SCOPUS_PATH)
    stats=sub.add_parser('stats'); stats.add_argument('--workspace',type=Path,required=True); stats.add_argument('--snapshot-id',action='append',required=True)
    resolve=sub.add_parser('resolve'); resolve.add_argument('--workspace',type=Path,required=True); resolve.add_argument('--snapshot-id',action='append',required=True); resolve.add_argument('--max-block-size',type=int,default=50)
    query=sub.add_parser('query'); query.add_argument('--workspace',type=Path,required=True); query.add_argument('--run-id',required=True); query.add_argument('--entity-id',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='sync': result=sync_venue_lists(args.workspace,requested_ref=args.ref,offline=args.offline,scopus_path=args.scopus_path)
        else:
            with Database(args.workspace) as db:
                store=VenueIntelligenceStore(db)
                if args.command=='import': result=import_venue_lists(db,args.workspace,args.snapshot_directory,provider=args.provider,scopus_path=args.scopus_path)
                elif args.command=='stats': result={sid:store.import_statistics(sid) for sid in args.snapshot_id}
                elif args.command=='resolve': result=resolve_imported_snapshots(db,args.snapshot_id,max_candidate_block_size=args.max_block_size)
                else: result=VenueIntelligence(store.venues).query(args.run_id,args.entity_id)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
    except (FoundationError,MirrorSyncError,ValueError,OSError):
        print('Venue operation failed validation/integrity/state checks; no research update was inferred.',file=sys.stderr); return 2


_unmanaged_main = main

def main(argv=None):
    from ..operations.cli import module_main
    return module_main(_unmanaged_main, argv, globals())


if __name__=='__main__': raise SystemExit(main())
