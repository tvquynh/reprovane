"""`reprovane pdf` command family. Help and parsing never open a workspace, read credentials or use HTTP."""
import argparse
import json
import sys
from contextlib import nullcontext
from pathlib import Path

from ..core import ArtifactIntegrityError, FoundationError, MigrationDriftError, ValidationError

WRITE = {'import', 'discover', 'download', 'resume', 'manual-import', 'legacy-import', 'close', 'review-acquire'}
NO_WORKSPACE = {'policy', 'verify-export', 'review'}


def build_parser():
    parser = argparse.ArgumentParser(prog='reprovane pdf', description='Unified open-access PDF acquisition: discovery runs, '
                                     'manual PDF ingestion, legacy v4.1.0 import, and the sealed-corpus review bridge.')
    sub = parser.add_subparsers(dest='command', required=True)

    def add(name, help_text, workspace=True):
        p = sub.add_parser(name, help=help_text, description=help_text)
        if workspace:
            p.add_argument('--workspace', type=Path, required=True)
        return p

    p = add('policy', 'Print a built-in policy profile and its SHA-256 (offline).', workspace=False)
    p.add_argument('--profile', choices=['discovery', 'review'], default='discovery')
    add('runs', 'List discovery acquisition runs in a workspace.')
    p = add('import', 'Import inputs into a new or OPEN discovery run.')
    p.add_argument('--run-id', help='Add to this OPEN run instead of creating a new one.')
    p.add_argument('--policy', default='discovery', help="'discovery' (built-in) or a policy JSON file for a new run.")
    p.add_argument('--label', help='Short descriptive label for a new run.')
    p.add_argument('--format', default='auto', choices=['auto', 'legacy-v4', 'openalex-works', 'reprovane-corpus',
                                                        'reprovane-pdf-export', 'generic', 'identifiers'])
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', type=Path, action='append', help='CSV, TSV, JSONL, JSON or TXT file (repeatable).')
    source.add_argument('--identifier', action='append', help='DOI or OpenAlex Work ID (repeatable).')
    source.add_argument('--acquisition-id', help='COMPLETE saved OpenAlex acquisition.')
    source.add_argument('--discovery-run-id', help='All COMPLETE acquisitions of a citation discovery run.')
    source.add_argument('--corpus-release-id', help='SEALED corpus release (membership is never changed).')
    p = add('discover', 'Freeze work identity and candidate plans (provider lookups only with --online).')
    p.add_argument('--run-id', required=True)
    p.add_argument('--online', action='store_true')
    p.add_argument('--accept-failed-lookups', action='store_true',
                   help='Plan works whose provider lookups failed temporarily, recording the failure as evidence.')
    for name, text in (('download', 'Download eligible open-access PDFs politely; safe to repeat.'),
                       ('resume', 'Finish discovery if needed, then continue downloading.')):
        p = add(name, text)
        p.add_argument('--run-id', required=True)
        p.add_argument('--limit', type=int, help='Attempt at most this many works in this invocation.')
        p.add_argument('--retry-failed', action='store_true', help='Retry permanent failures (still bounded by policy).')
        p.add_argument('--expected-policy-sha256', help='Refuse to continue if the frozen policy differs.')
        p.add_argument('--runtime-urls-from', type=Path, action='append', default=[],
                       help='Re-read exact signed URLs from your own input file (kept in memory only).')
        p.add_argument('--quiet', action='store_true')
        if name == 'resume':
            p.add_argument('--online', action='store_true')
    p = add('manual-import', 'Ingest manually downloaded PDFs (file or folder).')
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument('--run-id')
    target.add_argument('--fulltext-run-id', help='REVIEW_FULLTEXT (Stage11B) run: supplementary, never primary.')
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--recursive', action='store_true')
    p.add_argument('--mapping', type=Path, help='CSV: file or sha256, plus work_id/corpus_item_id/openalex_id/doi/legacy_key.')
    p = add('legacy-import', 'Import a v4.1.0 fetch-oa output folder read-only.')
    p.add_argument('--run-id', required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--no-blocked-hosts', action='store_true')
    p = add('reconcile', 'List reconciliation items, or record one immutable decision.')
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument('--run-id')
    target.add_argument('--fulltext-run-id')
    p.add_argument('--item-id')
    decision = p.add_mutually_exclusive_group()
    decision.add_argument('--target-id', help='work_id (discovery) or corpus_item_id (review) to link.')
    decision.add_argument('--reject', action='store_true')
    p.add_argument('--decided-by', default='operator', help='Short role label or initials (no personal contact data).')
    p = add('status', 'Counts, reasons, sources and hosts for a run.')
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument('--run-id')
    target.add_argument('--fulltext-run-id')
    p.add_argument('--items', action='store_true', help='Include per-work rows.')
    p = add('inspect', 'Inspect a run or one work.')
    p.add_argument('--run-id', required=True)
    p.add_argument('--work-id')
    for name, text in (('export', 'Write verified reports (CSV/HTML/RIS/JSON) to a new folder.'),
                       ('export-pdfs', 'Copy primary PDFs to a new folder as <paper>.pdf.')):
        p = add(name, text)
        p.add_argument('--run-id', required=True)
        p.add_argument('--directory', type=Path, required=True)
    p = add('verify', 'Verify a run offline: provenance, plans, ledger, budget and PDF bytes.')
    p.add_argument('--run-id', required=True)
    p = add('verify-export', 'Verify an exported report folder without a database.', workspace=False)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--expected-manifest-sha256')
    p = add('close', 'Verify and freeze a run with its semantic manifest.')
    p.add_argument('--run-id', required=True)
    p = add('hosts', 'List host blocks/cooldowns, or record an operator block/unblock event.')
    p.add_argument('--unblock')
    p.add_argument('--block')
    p.add_argument('--reason', default='operator decision')
    p = add('orphans', 'Report unreferenced content-store objects; optionally move them to a quarantine folder.')
    p.add_argument('--quarantine', type=Path)
    p = sub.add_parser('review', help='REVIEW_FULLTEXT profile: the accepted Stage11B commands (use review --help).',
                       description='REVIEW_FULLTEXT: same commands as `reprovane fulltext` (create, discover, acquire, '
                                   'materialize, finalize, inspect, verify, export, export-pdfs, verify-export). '
                                   '`review acquire` additionally applies robots.txt, per-host pacing, Retry-After '
                                   'cooldowns and blocked hosts before every PDF request.')
    p.add_argument('review_args', nargs=argparse.REMAINDER)
    return parser


def review_acquire_parser():
    parser = argparse.ArgumentParser(prog='reprovane pdf review acquire',
                                     description='Stage11B acquisition with robots.txt, pacing and blocked-host rules.')
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--expected-policy-sha256', required=True)
    return parser


def _private():
    from ..legacy_migration.inventory import runtime_private
    return runtime_private()


def _transport(p):
    from ..fulltext_acquisition.transport import runtime_credentials
    from .engine import make_transport
    return make_transport(p, credentials=runtime_credentials())


def _load_policy(value):
    from .policy import default_policy
    if value in ('discovery', None):
        return default_policy('discovery')
    return json.loads(Path(value).read_text(encoding='utf-8-sig'))


def _progress(quiet):
    if quiet:
        return None

    def report(index, total, work, outcome):
        print(f"[pdf {index}/{total}] {outcome or 'SKIPPED':<22} {work.get('file_key') or work['primary_legacy_key'] or work['work_id']}", file=sys.stderr, flush=True)
    return report


def dispatch(a, db):
    from .store import PdfAcquisitionStore
    store = PdfAcquisitionStore(db)
    if a.command == 'runs':
        return dict(runs=store.list_runs())
    if a.command == 'review-acquire':
        from ..fulltext_acquisition import FullTextStore
        from ..fulltext_acquisition.transport import SafeTransport, runtime_credentials
        from .review import PoliteReviewTransport
        full = FullTextStore(db)
        transport = PoliteReviewTransport(store, a.run_id, SafeTransport(full.policy(a.run_id), credentials=runtime_credentials()))
        result = full.acquire(a.run_id, transport, expected_policy_sha256=a.expected_policy_sha256)
        return dict(result, politeness=transport.summary())
    if a.command == 'import':
        rid = a.run_id or store.create_run(_load_policy(a.policy), a.label)['run_id']
        private = _private()
        if a.input:
            batches = [store.import_file(rid, path, a.format, private=private) for path in a.input]
        elif a.identifier:
            batches = [store.import_identifiers(rid, a.identifier, private=private)]
        elif a.acquisition_id:
            batches = [store.import_acquisition(rid, a.acquisition_id, private=private)]
        elif a.discovery_run_id:
            batches = store.import_discovery_run(rid, a.discovery_run_id, private=private)
        else:
            batches = [store.import_corpus_release(rid, a.corpus_release_id, private=private)]
        return dict(run_id=rid, batches=batches, run=store.inspect(rid))
    if a.command == 'discover':
        p = store.policy(a.run_id)
        return store.discover(a.run_id, _transport(p) if a.online else None, online=a.online,
                              accept_failed_lookups=a.accept_failed_lookups)
    if a.command in ('download', 'resume'):
        run = store.get(a.run_id)
        if a.expected_policy_sha256 and a.expected_policy_sha256 != run['policy_sha256']:
            raise ValidationError('Frozen policy differs from --expected-policy-sha256; nothing was downloaded')
        transport = _transport(store.policy(a.run_id))
        if a.command == 'resume' and run['state'] in ('OPEN', 'DISCOVERING'):
            found = store.discover(a.run_id, transport if a.online else None, online=a.online)
            if store.get(a.run_id)['state'] != 'ACQUIRING':
                return found
        from .engine import Downloader, runtime_urls_from
        runtime = runtime_urls_from(store, a.run_id, a.runtime_urls_from) if a.runtime_urls_from else None
        return Downloader(store, a.run_id, transport, progress=_progress(a.quiet), runtime_urls=runtime).run(
            limit=a.limit, retry_failed=a.retry_failed)
    if a.command == 'manual-import':
        from .manual import manual_import
        return manual_import(store, run_id=a.run_id, fulltext_run_id=a.fulltext_run_id, path=a.input, recursive=a.recursive,
                             mapping=a.mapping)
    if a.command == 'legacy-import':
        from .legacy import import_legacy_fulltext
        return import_legacy_fulltext(store, a.run_id, a.source, import_blocked_hosts=not a.no_blocked_hosts)
    if a.command == 'reconcile':
        from .manual import decide, reconciliation
        if a.item_id:
            return decide(store, a.item_id, target_id=a.target_id, reject=a.reject, decided_by=a.decided_by)
        return dict(items=reconciliation(store, run_id=a.run_id, fulltext_run_id=a.fulltext_run_id))
    if a.command == 'status':
        from .report import review_status
        if a.fulltext_run_id:
            result = review_status(store, a.fulltext_run_id)
            return result if a.items else {k: v for k, v in result.items() if k != 'items'}
        if store.get(a.run_id)['state'] == 'OPEN':
            return dict(store.inspect(a.run_id), note='Run discover to freeze identities and candidate plans.')
        m = store.manifest(a.run_id, store.host_watermark())
        keep = ('run_id', 'label', 'profile', 'run_state_at_manifest', 'policy_sha256', 'summary', 'by_evidence_source', 'by_host',
                'host_states', 'budget', 'identity', 'reconciliation')
        result = {k: m[k] for k in keep}
        result['artifacts'] = m['artifacts']
        if a.items:
            result['items'] = m['items']
        return result
    if a.command == 'inspect':
        return store.inspect(a.run_id, a.work_id)
    if a.command == 'export':
        from .report import export
        return export(store, a.run_id, a.directory)
    if a.command == 'export-pdfs':
        from .report import export_pdfs
        return export_pdfs(store, a.run_id, a.directory)
    if a.command == 'verify':
        return store.verify(a.run_id)
    if a.command == 'close':
        return store.close(a.run_id)
    if a.command == 'hosts':
        if a.unblock:
            store.host_event(a.unblock, 'UNBLOCK', a.reason, origin='OPERATOR')
        if a.block:
            store.host_event(a.block, 'BLOCK', a.reason, origin='OPERATOR')
        return dict(hosts=store.hosts())
    if a.command == 'orphans':
        from .maintenance import quarantine, scan
        return quarantine(db, db.workspace, a.quarantine) if a.quarantine else scan(db, db.workspace)
    raise ValidationError('Unknown command')


def _unmanaged_main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    a = build_parser().parse_args(args)
    if a.command == 'review' and a.review_args[:1] == ['acquire']:
        a = review_acquire_parser().parse_args(a.review_args[1:])
        a.command = 'review-acquire'
    elif a.command == 'review':
        from ..fulltext_acquisition.__main__ import main as review_main
        return review_main(a.review_args)
    try:
        if a.command == 'policy':
            from .policy import describe
            result = describe(a.profile)
        elif a.command == 'verify-export':
            from .report import verify_export
            result = verify_export(a.directory, expected_manifest_sha256=a.expected_manifest_sha256)
        else:
            from ..core.db import Database
            from ..operations.lock import workspace_lock
            from ..operations.workspace import read_database
            writes = a.command in WRITE or (a.command == 'reconcile' and a.item_id) or \
                (a.command == 'hosts' and (a.block or a.unblock)) or (a.command == 'orphans' and a.quarantine)
            with (workspace_lock(a.workspace) if writes else nullcontext()):
                with (Database(a.workspace) if writes else read_database(a.workspace)) as db:
                    result = dispatch(a, db)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print('Interrupted. Completed work is saved; run the same command or `reprovane pdf resume` to continue.', file=sys.stderr)
        return 130
    except (ArtifactIntegrityError, MigrationDriftError):
        print('PDF acquisition integrity verification failed; private inputs are not printed.', file=sys.stderr)
        return 3
    except ValidationError as exc:
        print('PDF acquisition: ' + str(exc), file=sys.stderr)
        return 2
    except (FoundationError, ValueError, OSError):
        print('PDF acquisition validation/configuration failed; check the workspace path and inputs.', file=sys.stderr)
        return 2
    except Exception:
        print('PDF acquisition failed safely; no private exception payload is printed.', file=sys.stderr)
        return 4


def main(argv=None):
    return _unmanaged_main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
