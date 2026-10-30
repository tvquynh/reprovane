"""Thin discoverable router; scientific operations stay in their accepted modules."""
import argparse,importlib,json,sys
from pathlib import Path
from . import __version__
from .core import ValidationError,ArtifactIntegrityError,MigrationDriftError,FoundationError

ROUTES={'acquisition':'openalex_acquisition','venue-data':'venue_intelligence','discovery':'discovery','frontier':'frontier','search-validation':'search_validation','screening':'screening','corpus':'corpus_release','fulltext':'fulltext_acquisition'}


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ROUTES:return importlib.import_module('reprovane.'+ROUTES[args[0]]+'.__main__').main(args[1:])
    parser=argparse.ArgumentParser(prog='reprovane',description='Reprovane — reproducible literature discovery, screening, evidence management, corpus release, and full-text acquisition.')
    parser.add_argument('--version',action='version',version=__version__);sub=parser.add_subparsers(dest='family',required=True)
    for family,module in ROUTES.items():sub.add_parser(family,help='Existing '+module+' CLI (use '+family+' --help)')
    for name in ('doctor','verify','inspect'):
        p=sub.add_parser(name);p.add_argument('--offline',action='store_true',default=True);p.add_argument('--workspace',type=Path);p.add_argument('--full',action='store_true')
    p=sub.add_parser('workspace');s=p.add_subparsers(dest='command',required=True)
    i=s.add_parser('init');i.add_argument('--workspace',type=Path,required=True)
    i=s.add_parser('upgrade');i.add_argument('--workspace',type=Path,required=True);i.add_argument('--target',type=Path,required=True);i.add_argument('--backup',type=Path,required=True)
    p=sub.add_parser('legacy');s=p.add_subparsers(dest='command',required=True)
    for cmd in ('audit','plan','migrate','verify','inspect'):
        i=s.add_parser(cmd)
        if cmd in ('audit','plan','migrate'):i.add_argument('--source',type=Path,required=True)
        if cmd=='plan':i.add_argument('--project-key',default='legacy-v4-import');i.add_argument('--revision-number',type=int,default=1)
        if cmd=='migrate':i.add_argument('--target',type=Path);i.add_argument('--plan',type=Path);i.add_argument('--dry-run',action='store_true')
        if cmd in ('verify','inspect'):i.add_argument('--workspace',type=Path,required=True);i.add_argument('--migration-id',required=True);i.add_argument('--source',type=Path)
    p=sub.add_parser('project');s=p.add_subparsers(dest='command',required=True)
    for cmd in ('create','revision','inspect'):
        i=s.add_parser(cmd);i.add_argument('--workspace',type=Path,required=True)
        if cmd=='create':i.add_argument('--key',required=True);i.add_argument('--display-name',required=True)
        else:i.add_argument('--project-id',required=True)
        if cmd=='revision':i.add_argument('--number',type=int,required=True);i.add_argument('--manifest',type=Path,required=True)
    for family in ('resolution','venue-resolution'):
        p=sub.add_parser(family);s=p.add_subparsers(dest='command',required=True)
        for cmd in ('create','add','generate','evaluate','materialize','seal','verify','inspect'):
            i=s.add_parser(cmd);i.add_argument('--workspace',type=Path,required=True)
            if cmd=='create':i.add_argument('--project-revision',required=True);i.add_argument('--selector',type=Path,required=True)
            else:i.add_argument('--run-id',required=True)
            if cmd=='add':i.add_argument('--record-id',action='append',required=True)
    p=sub.add_parser('scimago');p.add_argument('--workspace',type=Path,required=True);p.add_argument('--ref',default='control');p.add_argument('--offline',action='store_true');p.add_argument('--import-snapshot',action='store_true')
    a=parser.parse_args(args)
    try:
        result=dispatch(a)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True));return 0
    except (ArtifactIntegrityError,MigrationDriftError):print('Integrity verification failed; private inputs are not printed.',file=sys.stderr);return 3
    except (ValidationError,FoundationError,ValueError,OSError):print('Validation/configuration failed; inspect the offline doctor and immutable inputs.',file=sys.stderr);return 5 if a.family=='legacy' and a.command=='migrate' else 2
    except Exception:
        print('Operation failed safely; no private exception payload is printed.',file=sys.stderr)
        return 4 if a.family=='scimago' else 5 if a.family=='legacy' and a.command=='migrate' else 2


def dispatch(a):
    from .operations.workspace import doctor,initialize,upgrade,read_database
    from .operations.lock import workspace_lock
    from .core.db import Database
    if a.family in ('doctor','verify','inspect'):return doctor(a.workspace,full=a.full or a.family=='verify')
    if a.family=='workspace':return initialize(a.workspace) if a.command=='init' else upgrade(a.workspace,a.target,a.backup)
    if a.family=='legacy':
        from .legacy_migration import audit,plan,execute,LegacyMigrationStore
        if a.command=='audit':return audit(a.source)
        if a.command=='plan':return plan(a.source,a.project_key,a.revision_number)
        if a.command=='migrate':
            if a.dry_run:return plan(a.source)
            if a.target is None or a.plan is None:raise ValidationError('Execution requires saved plan and separate target')
            return execute(a.source,a.target,json.loads(a.plan.read_text(encoding='utf-8-sig')))
        with read_database(a.workspace) as db:
            store=LegacyMigrationStore(db)
            return store.verify(a.migration_id,a.source) if a.command=='verify' else store.get(a.migration_id)
    if a.family=='scimago':
        from .mirror import sync_scimago_from_github,import_synced_scimago
        with workspace_lock(a.workspace):
            result=sync_scimago_from_github(a.workspace,requested_ref=a.ref,offline=a.offline)
            if a.import_snapshot:
                with Database(a.workspace) as db:return import_synced_scimago(db,result)
            from dataclasses import asdict
            return asdict(result)
    readonly=a.command in ('verify','inspect')
    from contextlib import nullcontext
    with (nullcontext() if readonly else workspace_lock(a.workspace)):
        with (read_database(a.workspace) if readonly else Database(a.workspace)) as db:
            if a.family=='project':
                from .core.foundation import Foundation
                f=Foundation(db)
                if a.command=='create':return f.create_project(a.key,a.display_name)
                if a.command=='revision':return f.create_project_revision(a.project_id,a.number,'json',a.manifest.read_text(encoding='utf-8-sig'))
                return dict(project=f.get_project(a.project_id),revisions=f.list_project_revisions(a.project_id))
            venue=a.family=='venue-resolution'
            if venue:
                from .venue_resolution import VenueResolutionStore
                store=VenueResolutionStore(db);prefix='venue_resolution'
            else:
                from .resolution import ResolutionStore
                store=ResolutionStore(db);prefix='resolution'
            if a.command=='create':return getattr(store,'create_'+prefix+'_run')(a.project_revision,json.loads(a.selector.read_text(encoding='utf-8-sig')))
            if a.command=='add':return getattr(store,'add_'+prefix+'_inputs')(a.run_id,a.record_id)
            if a.command=='generate':
                if venue:store.generate_provider_nodes(a.run_id)
                return store.generate_candidates(a.run_id)
            if a.command=='evaluate':return store.evaluate_candidates_automatically(a.run_id)
            method={'materialize':'materialize_','seal':'seal_','verify':'verify_','inspect':'get_'}[a.command]+prefix+'_run'
            return getattr(store,method)(a.run_id)


if __name__=='__main__':raise SystemExit(main())
