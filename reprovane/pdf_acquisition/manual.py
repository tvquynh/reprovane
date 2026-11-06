"""Manual PDF ingestion and reconciliation for discovery runs and sealed-corpus review runs.

A file is linked only by an explicit user mapping or by an exact identifier key in its file name
(OpenAlex Work ID, v4.1.0 paper key, work ID or corpus item ID). Title similarity is never used. Anything
unmatched, ambiguous or contradictory goes to the reconciliation queue for a human decision. For review runs
a manual PDF is a supplementary, unverified artifact: the sealed Stage11B run and its primaries never change.
"""
import csv
import io
import json
import os
import re
from pathlib import Path

from ..core import ValidationError
from ..core.ids import new_id, utc_now
from ..core.provenance import canonical_json
from ..corpus_release.model import digest, sha
from ..legacy_migration.inventory import sensitive
from . import identifiers as ids
from .pdfcheck import validate_pdf

MAX_FILES = 200_000
TARGET_COLUMNS = ('work_id', 'corpus_item_id', 'openalex_id', 'doi', 'legacy_key')
DECIDER = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,63}')
USER_BASES = ('USER_MAPPING', 'RECONCILIATION_DECISION')


def collect(path, recursive=False):
    """Ordinary *.pdf files under a file or directory; symlinks are never followed."""
    root = Path(path)
    if root.is_symlink():
        raise ValidationError('Symlink input refused')
    if root.is_file():
        return root.parent, [(root.name, root)], 0
    if not root.is_dir():
        raise ValidationError('Manual input must be a PDF file or a directory')
    found, ignored = [], 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        here = Path(folder)
        keep = sorted(d for d in dirs if not (here / d).is_symlink())
        ignored += len(dirs) - len(keep)
        dirs[:] = keep if recursive else []
        for name in sorted(files):
            p = here / name
            if p.is_symlink() or not p.is_file() or p.suffix.lower() != '.pdf':
                ignored += 1
                continue
            found.append((p.relative_to(root).as_posix(), p))
            if len(found) > MAX_FILES:
                raise ValidationError('Too many files in one manual import')
    return root, sorted(found), ignored


def read_mapping(path):
    """User-confirmed mapping CSV: file or sha256, plus work_id/corpus_item_id/openalex_id/doi/legacy_key."""
    if path is None:
        return None
    text = Path(path).read_bytes().decode('utf-8-sig')
    rows = []
    for row in csv.DictReader(io.StringIO(text, newline='')):
        row = {re.sub(r'[\s\-]+', '_', (k or '').strip().lower()): (v or '').strip() for k, v in row.items()}
        targets = {k: row[k] for k in TARGET_COLUMNS if row.get(k)}
        key_file, key_sha = row.get('file', ''), row.get('sha256', '').lower()
        if not (key_file or key_sha) or not targets:
            raise ValidationError('Each mapping row needs file or sha256 and at least one identifier column')
        if key_sha and not re.fullmatch(r'[0-9a-f]{64}', key_sha):
            raise ValidationError('Mapping sha256 must be 64 lowercase hex characters')
        rows.append(dict(file=key_file.replace('\\', '/'), sha256=key_sha, targets=targets))
    return dict(rows=rows, sha256=digest(rows))


def _safe_name(rel, private=()):
    return 'redacted-name/' + sha(rel.encode('utf-8')) if sensitive(rel.encode('utf-8'), private) else rel


class _Index:
    def __init__(self, targets, openalex, dois, legacy, safe):
        self.targets, self.openalex, self.dois, self.legacy, self.safe = set(targets), openalex, dois, legacy, safe

    def resolve(self, column, value):
        if column in ('work_id', 'corpus_item_id'):
            return {value} if value in self.targets else set()
        if column == 'openalex_id':
            return set(self.openalex.get(ids.openalex_work(value) or '', ()))
        if column == 'doi':
            return set(self.dois.get(ids.doi(value) or '', ()))
        return set(self.legacy.get(value, ()))

    def by_stem(self, stem, *, any_key=False):
        """Targets whose file-name key equals the stem.

        Returns (targets, confirm): automatic matching uses only identity-qualified keys; a stem that equals only
        an unqualified key (for example one copied from an invalid identifier field) returns its targets in
        ``confirm`` so the file waits for a human decision instead of being linked.
        """
        found = set((self.legacy if any_key else self.safe).get(stem, ()))
        if stem in self.targets:
            found.add(stem)
        work = ids.openalex_work(stem) if re.fullmatch(r'[Ww][0-9]+', stem) else None
        if work:
            found |= set(self.openalex.get(work, ()))
        return found, (set() if found else set(self.legacy.get(stem, ())))


def _index(entries):
    """entries: (target_id, openalex_ids, dois, legacy_keys, safe_keys)."""
    openalex, dois, legacy, safe = {}, {}, {}, {}
    for target, works, ds, keys, safe_keys in entries:
        for w in works:
            openalex.setdefault(w, set()).add(target)
        for d in ds:
            dois.setdefault(d, set()).add(target)
        for k in keys:
            legacy.setdefault(k, set()).add(target)
        for k in safe_keys:
            safe.setdefault(k, set()).add(target)
    return _Index([e[0] for e in entries], openalex, dois, legacy, safe)


class DiscoveryContext:
    kind = 'DISCOVERY_RUN'

    def __init__(self, store, rid):
        if store.get(rid)['state'] == 'OPEN':
            store.freeze(rid)
        run = store.get(rid)
        if run['state'] not in ('DISCOVERING', 'ACQUIRING'):
            raise ValidationError('Manual ingestion requires an open (not closed) run')
        self.store, self.id = store, rid
        p = store.policy(rid)
        self.bounds = dict(min_pdf_bytes=p['min_pdf_bytes'], max_pdf_bytes=p['max_pdf_bytes'])
        self.rules = set(p['manual_match_rules'])
        self.index = _index([(w['work_id'], w['openalex_ids'], w['dois'], w['legacy_keys'],
                              set(w['match_keys']) | {w['file_key']}) for w in store.works(rid)])

    def linked_targets(self, artifact):
        return {r['work_id'] for r in self.store._all('SELECT work_id FROM pdf_work_artifact WHERE run_id=? AND artifact_sha256=?',
                                                      (self.id, artifact))}

    def link(self, target, artifact, import_id, basis, source):
        verification = ('STRUCTURE_VALIDATED_LINKAGE_USER_CONFIRMED' if basis in USER_BASES else
                        'LEGACY_V4_FETCH_STATE_SHA_MATCHED' if basis == 'LEGACY_V4_FETCH_STATE' else
                        'STRUCTURE_VALIDATED_LINKAGE_BY_EXACT_KEY')
        self.store.add_link(self.id, target, artifact, source, import_id=import_id, basis=basis, version_claim='UNKNOWN',
                            verification=verification)


class ReviewContext:
    kind = 'REVIEW_FULLTEXT_RUN'

    def __init__(self, store, fulltext_run_id):
        run = store._one('SELECT * FROM fulltext_run WHERE run_id=?', (fulltext_run_id,))
        if run is None:
            raise ValidationError('Unknown review full-text run')
        self.store, self.id = store, fulltext_run_id
        policy = json.loads(run['policy_json'])
        self.bounds = dict(min_pdf_bytes=policy['min_pdf_bytes'], max_pdf_bytes=policy['max_pdf_bytes'])
        self.rules = {'USER_MAPPING', 'EXACT_FILENAME_KEY'}
        entries = []
        for row in store._all('SELECT input_json FROM fulltext_input WHERE run_id=? ORDER BY corpus_item_id', (fulltext_run_id,)):
            item = json.loads(row['input_json'])
            works = sorted({w for w in (ids.openalex_work(v) for v in item['openalex_work_ids']) if w})
            dois = sorted({d for d in (ids.doi(v) for v in item['dois']) if d})
            keys = sorted(set(works) | {ids.legacy_doi_key(d) for d in dois})
            entries.append((item['corpus_item_id'], works, dois, keys, keys))
        self.index = _index(entries)

    def linked_targets(self, artifact):
        return {r['corpus_item_id'] for r in self.store._all(
            'SELECT corpus_item_id FROM pdf_review_supplement WHERE fulltext_run_id=? AND artifact_sha256=?', (self.id, artifact))}

    def link(self, target, artifact, import_id, basis, source):
        link_id = 'prs_' + digest([self.id, target, artifact])[:40]
        if self.store._one('SELECT 1 FROM pdf_review_supplement WHERE link_id=?', (link_id,)):
            return
        self.store._insert('pdf_review_supplement', dict(link_id=link_id, fulltext_run_id=self.id, corpus_item_id=target,
                                                         artifact_sha256=artifact, import_id=import_id, basis=basis,
                                                         verification='SUPPLEMENTARY_UNVERIFIED_NOT_PRIMARY',
                                                         created_at_utc=utc_now()))


def _mapping_rows(mapping, rel, digest_):
    """Every mapping row that applies to this file, plus rows whose selectors contradict each other.

    A row giving both a file name and a SHA-256 applies only when both agree with the file; when exactly one
    agrees the row is contradictory. Exact path or SHA rows take precedence over bare file-name rows.
    """
    name = Path(rel).name
    exact, bare, contradictory = [], [], []
    for m in mapping['rows']:
        sha_hit = bool(m['sha256']) and m['sha256'] == digest_
        path_hit = bool(m['file']) and m['file'] == rel
        name_hit = bool(m['file']) and '/' not in m['file'] and m['file'] == name
        if m['sha256'] and m['file']:
            if sha_hit and (path_hit or name_hit):
                exact.append(m)
            elif sha_hit or path_hit or name_hit:
                contradictory.append(m)
        elif sha_hit or path_hit:
            exact.append(m)
        elif name_hit:
            bare.append(m)
    return exact or bare, contradictory


def _match(ctx, rel, digest_, mapping, spec):
    rows, contradictory = _mapping_rows(mapping, rel, digest_) if mapping else ([], [])
    if rows or contradictory:
        if 'USER_MAPPING' not in ctx.rules:
            raise ValidationError('The frozen policy does not permit user mappings')
        if contradictory:
            # The user's own file and checksum disagree: never guess which one was meant.
            named = {t for m in contradictory for col, v in m['targets'].items() for t in ctx.index.resolve(col, v)}
            return None, 'USER_MAPPING', 'MAPPING_CONFLICT', sorted(named)
        chosen = set()
        for entry in rows:
            sets = [ctx.index.resolve(col, value) for col, value in sorted(entry['targets'].items())]
            if any(not s for s in sets):
                return None, 'USER_MAPPING', 'IDENTIFIER_NOT_IN_CONTEXT', sorted(set().union(*sets))
            both = set.intersection(*sets)
            if len(both) != 1:
                return None, 'USER_MAPPING', 'IDENTIFIER_CONFLICT' if not both else 'AMBIGUOUS', sorted(both or set().union(*sets))
            chosen |= both
        if len(chosen) == 1:
            return next(iter(chosen)), 'USER_MAPPING', None, []
        # Several mapping rows apply to this file and name different publications.
        return None, 'USER_MAPPING', 'MAPPING_CONFLICT', sorted(chosen)
    key = spec.get('forced_key') or Path(rel).stem
    basis = spec.get('basis') or 'EXACT_FILENAME_KEY'
    if 'EXACT_FILENAME_KEY' not in ctx.rules and not spec.get('forced_key'):
        return None, basis, 'UNMATCHED', []
    # Only a 4.1.0 fetch-state entry whose bytes matched its recorded SHA-256 may use any historical key; ingest has
    # already refused the file when the recorded checksum differs.
    found, confirm = ctx.index.by_stem(key, any_key=basis == 'LEGACY_V4_FETCH_STATE' and bool(spec.get('expected_sha256')))
    if len(found) == 1:
        return next(iter(found)), basis, None, []
    if found:
        return None, basis, 'AMBIGUOUS', sorted(found)
    if confirm:
        return None, basis, 'KEY_NEEDS_CONFIRMATION', sorted(confirm)
    return None, basis, 'UNMATCHED', []


def _open_session(ctx, session_kind, source_label, session_detail, mapping):
    session_id = new_id('pms')
    session = dict(schema='pdf-import-session/1', kind=session_kind, source_label=source_label,
                   detail=session_detail or {}, mapping_sha256=mapping['sha256'] if mapping else None)
    ctx.store._insert('pdf_import_session', dict(session_id=session_id, context_kind=ctx.kind, context_id=ctx.id,
                                                 kind=session_kind, source_label=source_label[:200],
                                                 session_json=canonical_json(session), session_sha256=digest(session),
                                                 created_at_utc=utc_now()))
    return session_id


def ingest(ctx, specs, *, session_kind='MANUAL', source_label='manual-input', session_detail=None, mapping=None,
           always_session=False, private=()):
    """specs: dicts with rel, path and optional forced_key, basis, link_source, expected_sha256, detail.

    With always_session the import session (and its detail, such as legacy history) is recorded even when
    there is no file to ingest.
    """
    store = ctx.store
    seen = {(r['source_name'], r['source_sha256'], r['byte_size']) for r in store._all(
        'SELECT m.source_name,m.source_sha256,m.byte_size FROM pdf_manual_import m JOIN pdf_import_session s '
        'ON s.session_id=m.session_id WHERE s.context_kind=? AND s.context_id=?', (ctx.kind, ctx.id))}
    session_id, ordinal = None, 0
    if always_session:
        with store.write():
            session_id = _open_session(ctx, session_kind, source_label, session_detail, mapping)
    counts = dict(files=len(specs), linked=0, already_linked=0, reconciliation_required=0, rejected=0, unchanged=0)
    results = []
    for spec in specs:
        rel, path = spec['rel'], Path(spec['path'])
        name = _safe_name(rel, private)
        outcome, reason, artifact, detail, problem = None, None, None, dict(spec.get('detail') or {}), None
        body, digest_ = None, None
        try:
            if path.is_symlink() or not path.is_file():
                raise ValidationError('NOT_A_REGULAR_FILE')
            size = path.stat().st_size
            if size > ctx.bounds['max_pdf_bytes']:
                raise ValidationError('TOO_LARGE')
            body = path.read_bytes()
            if len(body) != size:
                raise ValidationError('SOURCE_CHANGED_WHILE_READING')
            digest_ = sha(body)
            if spec.get('expected_sha256') and spec['expected_sha256'] != digest_:
                raise ValidationError('LEGACY_STATE_SHA_MISMATCH')
            try:
                validation = validate_pdf(body, ctx.bounds)
            except ValidationError as exc:
                raise ValidationError('PDF_SIZE_INVALID' if str(exc) == 'PDF_SIZE_INVALID' else 'PDF_VALIDATION_FAILED') from None
        except ValidationError as exc:
            outcome, reason = 'REJECTED', str(exc)
        except OSError:
            outcome, reason = 'REJECTED', 'UNREADABLE'
        size = len(body) if body is not None else (path.stat().st_size if path.is_file() and not path.is_symlink() else None)
        if (name, digest_, size) in seen:
            counts['unchanged'] += 1
            results.append(dict(source_name=name, outcome='UNCHANGED', sha256=digest_))
            continue
        target = None
        if outcome is None:
            target, basis, problem, candidates = _match(ctx, rel, digest_, mapping, spec)
            detail.update(match_basis=basis)
            if target is not None:
                elsewhere = ctx.linked_targets(digest_) - {target}
                if elsewhere:
                    problem, candidates = 'ARTIFACT_LINKED_ELSEWHERE', sorted({target} | elsewhere)
                    target = None
            if problem:
                outcome, reason = 'RECONCILIATION_REQUIRED', problem
                detail['candidates'] = candidates
            elif digest_ is not None and target in ctx.linked_targets(digest_):
                outcome = 'ALREADY_LINKED'
            else:
                outcome = 'LINKED'
            stored = store.cas.put_bytes(body)
            artifact = stored.artifact_sha256
        import_id = new_id('pmi')
        with store.write():
            if session_id is None:
                session_id = _open_session(ctx, session_kind, source_label, session_detail, mapping)
            if artifact:
                store.register_pdf(stored, validation)
            if target is not None:
                detail['target_id'] = target
            store._insert('pdf_manual_import', dict(import_id=import_id, session_id=session_id, ordinal=ordinal, source_name=name,
                                                    source_sha256=digest_, byte_size=size, outcome=outcome, reason=reason,
                                                    artifact_sha256=artifact, detail_json=canonical_json(detail),
                                                    detail_sha256=digest(detail)))
            ordinal += 1
            if outcome == 'LINKED':
                ctx.link(target, artifact, import_id, basis, spec.get('link_source', 'MANUAL'))
            elif outcome == 'RECONCILIATION_REQUIRED':
                store._insert('pdf_reconciliation', dict(item_id='prc_' + digest([ctx.id, import_id])[:40], context_kind=ctx.kind,
                                                         context_id=ctx.id, import_id=import_id, kind=problem,
                                                         candidates_json=canonical_json(detail['candidates']),
                                                         created_at_utc=utc_now()))
        seen.add((name, digest_, size))
        counts[outcome.lower()] += 1
        results.append(dict(source_name=name, outcome=outcome, reason=reason, sha256=digest_, target_id=target,
                            import_id=import_id))
    return dict(context_kind=ctx.kind, context_id=ctx.id, session_id=session_id, counts=counts, files=results)


def manual_import(store, *, run_id=None, fulltext_run_id=None, path, recursive=False, mapping=None):
    if bool(run_id) == bool(fulltext_run_id):
        raise ValidationError('Give exactly one of --run-id or --fulltext-run-id')
    ctx = DiscoveryContext(store, run_id) if run_id else ReviewContext(store, fulltext_run_id)
    root, files, ignored = collect(path, recursive)
    mapping_rows = read_mapping(mapping)
    label = _safe_name(Path(path).name) or 'manual-input'
    result = ingest(ctx, [dict(rel=rel, path=p) for rel, p in files], source_label=label,
                    session_detail=dict(recursive=bool(recursive), ignored_entries=ignored), mapping=mapping_rows)
    result['counts']['ignored_non_pdf_or_symlink'] = ignored
    return result


def reconciliation(store, *, run_id=None, fulltext_run_id=None):
    kind, cid = ('DISCOVERY_RUN', run_id) if run_id else ('REVIEW_FULLTEXT_RUN', fulltext_run_id)
    rows = store._all('SELECT r.*, d.decision, d.target_id, d.decided_by, m.source_name, m.source_sha256 FROM pdf_reconciliation r '
                      'JOIN pdf_manual_import m ON m.import_id=r.import_id LEFT JOIN pdf_reconciliation_decision d ON d.item_id=r.item_id '
                      'WHERE r.context_kind=? AND r.context_id=? ORDER BY r.created_at_utc, r.item_id', (kind, cid))
    return [dict(item_id=r['item_id'], kind=r['kind'], source_name=r['source_name'], sha256=r['source_sha256'],
                 candidates=json.loads(r['candidates_json']), state='DECIDED' if r['decision'] else 'OPEN',
                 decision=r['decision'], target_id=r['target_id'], decided_by=r['decided_by']) for r in rows]


def decide(store, item_id, *, target_id=None, reject=False, decided_by='operator'):
    if bool(target_id) == bool(reject):
        raise ValidationError('Give exactly one of --target-id or --reject')
    if not DECIDER.fullmatch(decided_by or ''):
        raise ValidationError('Decision label must be a short role or initials, not personal contact data')
    item = store._one('SELECT r.*, m.artifact_sha256 FROM pdf_reconciliation r JOIN pdf_manual_import m ON m.import_id=r.import_id '
                      'WHERE r.item_id=?', (item_id,))
    if item is None:
        raise ValidationError('Unknown reconciliation item')
    if store._one('SELECT 1 FROM pdf_reconciliation_decision WHERE item_id=?', (item_id,)):
        raise ValidationError('This reconciliation item already has an immutable decision')
    ctx = DiscoveryContext(store, item['context_id']) if item['context_kind'] == 'DISCOVERY_RUN' else ReviewContext(store, item['context_id'])
    if target_id and target_id not in ctx.index.targets:
        raise ValidationError('Target is not a work or corpus item of this context')
    with store.write():
        store._insert('pdf_reconciliation_decision', dict(item_id=item_id, decision='REJECT' if reject else 'LINK', target_id=target_id,
                                                          decided_by=decided_by, decided_at_utc=utc_now()))
        if target_id:
            ctx.link(target_id, item['artifact_sha256'], item['import_id'], 'RECONCILIATION_DECISION', 'MANUAL')
    return dict(item_id=item_id, decision='REJECT' if reject else 'LINK', target_id=target_id)
