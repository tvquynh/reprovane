"""Durable discovery-oriented PDF acquisition (schema 14).

Lifecycle: OPEN (inputs) -> DISCOVERING (identity frozen, plans being frozen) -> ACQUIRING (downloads,
manual ingestion, reconciliation) -> CLOSED (semantic manifest). Every row is append-only. Artifacts are
placed in the content-addressed store before their database registration, so an interruption can leave
an unreferenced object but never a registered row without verified bytes.
"""
import json
import re
from collections import Counter, defaultdict
from contextlib import contextmanager
from urllib.parse import quote

from ..core import ArtifactIntegrityError, ValidationError
from ..core.foundation import Foundation
from ..core.ids import new_id, utc_now
from ..core.migrations import apply_migrations
from ..core.provenance import canonical_json
from ..corpus_release.model import digest
from ..fulltext_acquisition.candidates import location_routes
from ..fulltext_acquisition.safety import guard, normalize_url, sanitize_url
from ..fulltext_acquisition.transport import FetchError
from ..legacy_migration.inventory import sensitive
from . import inputs
from .candidates import plan_work
from .identity import freeze as freeze_identity
from .pdfcheck import validate_pdf
from .policy import DISCOVERY_DEFAULT_POLICY, LOOKUP_SOURCES, DiscoveryPolicy
from .politeness import PRIORITY, normalize_reason, outcome_class, pick

SCHEMA_VERSION = 14
STRUCTURE_POLICY = dict(min_pdf_bytes=1, max_pdf_bytes=2_000_000_000)
LINK_ORDER = {'AUTO': 0, 'LEGACY_V4': 1, 'MANUAL': 2}
LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9 ._:+-]{0,119}')


def record_id(batch_id, ordinal):
    return 'pir_' + digest([batch_id, ordinal])[:40]


def safe_label(value, default):
    value = (value or default).strip()
    if not LABEL.fullmatch(value):
        raise ValidationError('Labels use 1-120 letters, digits, spaces and . _ : + -')
    return value


def _oa_evidence(candidate):
    """TRUE when any route declares the URL open, FALSE when every route declares it closed, else ABSENT."""
    flags = {r.get('oa_evidence') for r in candidate.get('routes', []) if r.get('url_type') != 'LANDING_PAGE'}
    return 'TRUE' if 'TRUE' in flags else 'FALSE' if flags == {'FALSE'} else 'ABSENT'


class PdfAcquisitionStore:
    def __init__(self, database):
        from ..legacy_migration import LegacyMigrationStore
        self.db = database
        # Upstream stores register the connection functions that migrations 0001-0013 declare.
        LegacyMigrationStore(database)
        self.foundation = Foundation(database)
        self.cas = self.foundation.artifacts
        if not hasattr(database, '_pdf_auth'):
            database._pdf_auth = 0
        database.connection.create_function('pdf_acquisition_authorized', 0, lambda: int(database._pdf_auth > 0))
        apply_migrations(database.connection, target_version=SCHEMA_VERSION)

    # -- primitives ---------------------------------------------------------------------------------------------
    @contextmanager
    def write(self):
        self.db._pdf_auth += 1
        try:
            if self.db.connection.in_transaction:
                yield
            else:
                with self.db.transaction():
                    yield
        finally:
            self.db._pdf_auth -= 1

    def _insert(self, table, row):
        self.db.execute('INSERT INTO ' + table + ' (' + ','.join(row) + ') VALUES (' + ','.join('?' * len(row)) + ')',
                        tuple(row.values()))
        return row

    def _all(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, params)]

    def _one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    # -- runs ---------------------------------------------------------------------------------------------------
    def create_run(self, policy=None, label=None):
        p = DiscoveryPolicy.from_value(DISCOVERY_DEFAULT_POLICY if policy is None else policy)
        rid = new_id('pdr')
        with self.write():
            self._insert('pdf_run', dict(run_id=rid, profile='DISCOVERY_DOWNLOAD', label=safe_label(label, 'pdf-acquisition'),
                                         policy_json=p.canonical, policy_sha256=p.sha256, state='OPEN', identity_json=None,
                                         identity_sha256=None, manifest_json=None, manifest_sha256=None,
                                         created_at_utc=utc_now(), closed_at_utc=None))
        return self.inspect(rid)

    def get(self, rid):
        run = self._one('SELECT * FROM pdf_run WHERE run_id=?', (rid,))
        if run is None:
            raise ValidationError('Unknown PDF acquisition run')
        return run

    def policy(self, rid):
        run = self.get(rid)
        p = DiscoveryPolicy.from_value(json.loads(run['policy_json']))
        if p.sha256 != run['policy_sha256'] or p.canonical != run['policy_json']:
            raise ArtifactIntegrityError('Frozen policy drift')
        return p.as_dict()

    def list_runs(self):
        return [dict(run_id=r['run_id'], label=r['label'], state=r['state'], policy_sha256=r['policy_sha256'],
                     created_at_utc=r['created_at_utc']) for r in self._all('SELECT * FROM pdf_run ORDER BY created_at_utc, run_id')]

    # -- inputs -------------------------------------------------------------------------------------------------
    def batches(self, rid):
        return self._all('SELECT * FROM pdf_input_batch WHERE run_id=? ORDER BY ordinal', (rid,))

    def records(self, rid, status=None):
        sql = ('SELECT r.* FROM pdf_input_record r JOIN pdf_input_batch b ON b.batch_id=r.batch_id WHERE r.run_id=?'
               + (' AND r.status=?' if status else '') + ' ORDER BY b.ordinal, r.row_ordinal')
        return self._all(sql, (rid, status) if status else (rid,))

    def add_batch(self, rid, batch, rows, *, raw_body=None, private=()):
        run = self.get(rid)
        if run['state'] != 'OPEN':
            raise ValidationError('Inputs are frozen once discovery starts; create a new run for new inputs')
        old = self._one('SELECT * FROM pdf_input_batch WHERE run_id=? AND source_kind=? AND source_sha256=?',
                        (rid, batch['source_kind'], batch['source_sha256']))
        if old:
            return dict(self._batch_view(old), reused=True)
        warnings, stored = [], None
        if raw_body is not None:
            if inputs.secret_like(raw_body, private):
                warnings.append('RAW_SOURCE_NOT_ARCHIVED_SECRET_LIKE_CONTENT')
            else:
                stored = self.cas.put_bytes(raw_body)
        reasons = Counter(reason for status, reason, _ in rows if status == 'REJECTED')
        body = dict(schema='pdf-input-batch/1', source_kind=batch['source_kind'], source_format=batch['source_format'],
                    detail=batch['detail'], counts=dict(rows=len(rows), accepted=len(rows) - sum(reasons.values()),
                                                        rejected=sum(reasons.values())),
                    rejected_reasons=dict(sorted(reasons.items())), warnings=sorted(warnings),
                    raw_source_archived=stored is not None)
        try:
            guard(body, private)
            for _, _, record in rows:
                guard(record, private)
        except ValidationError:
            raise ValidationError('Input contains a private runtime value; nothing was imported') from None
        bid = new_id('pib')
        label = batch['source_label'] if not inputs.secret_like(batch['source_label'].encode(), private) else 'redacted-name'
        with self.write():
            if stored is not None:
                self.foundation.register_raw_artifact(stored)
            ordinal = self.db.execute('SELECT count(*) FROM pdf_input_batch WHERE run_id=?', (rid,)).fetchone()[0]
            self._insert('pdf_input_batch', dict(batch_id=bid, run_id=rid, ordinal=ordinal, source_kind=batch['source_kind'],
                                                 source_format=batch['source_format'], source_label=label[:200],
                                                 source_sha256=batch['source_sha256'],
                                                 raw_artifact_sha256=stored.artifact_sha256 if stored else None,
                                                 batch_json=canonical_json(body), batch_sha256=digest(body),
                                                 imported_at_utc=utc_now()))
            for n, (status, reason, record) in enumerate(rows):
                self._insert('pdf_input_record', dict(run_id=rid, record_id=record_id(bid, n), batch_id=bid, row_ordinal=n,
                                                      status=status, reason=reason, record_json=canonical_json(record),
                                                      record_sha256=digest(record)))
        return dict(self._batch_view(self._one('SELECT * FROM pdf_input_batch WHERE batch_id=?', (bid,))), reused=False)

    def _batch_view(self, row):
        body = json.loads(row['batch_json'])
        return dict(batch_id=row['batch_id'], source_kind=row['source_kind'], source_format=row['source_format'],
                    source_label=row['source_label'], source_sha256=row['source_sha256'],
                    raw_artifact_sha256=row['raw_artifact_sha256'], counts=body['counts'],
                    rejected_reasons=body['rejected_reasons'], warnings=body['warnings'])

    def import_file(self, rid, path, fmt='auto', *, private=()):
        batch, rows, body = inputs.parse_file(path, fmt)
        return self.add_batch(rid, batch, rows, raw_body=body, private=private)

    def import_identifiers(self, rid, values, *, private=()):
        batch, rows = inputs.parse_identifiers(list(values))
        return self.add_batch(rid, batch, rows, private=private)

    def _acquisition_store(self):
        from ..openalex_acquisition import OpenAlexAcquisitionStore
        return OpenAlexAcquisitionStore(self.db)

    def import_acquisition(self, rid, acquisition_id, *, label='OPENALEX_ACQUISITION', discovery_run_id=None, private=()):
        store = self._acquisition_store()
        acquisition = store.get(acquisition_id)
        if acquisition['state'] != 'COMPLETE':
            raise ValidationError('Only COMPLETE OpenAlex acquisitions can be imported')
        self.foundation.verify_provider_snapshot(acquisition['snapshot_id'])
        batch, rows = inputs.acquisition_records(acquisition, store._replay(acquisition), label)
        if discovery_run_id:
            batch['detail']['discovery_run_id'] = discovery_run_id
            batch['source_sha256'] = digest([discovery_run_id, acquisition['request_spec_sha256'], acquisition_id])
        return self.add_batch(rid, batch, rows, private=private)

    def import_discovery_run(self, rid, discovery_run_id, *, private=()):
        ids = [r['acquisition_id'] for r in self._all(
            'SELECT DISTINCT a.acquisition_id FROM discovery_operation o JOIN discovery_operation_acquisition a '
            'ON a.operation_id=o.operation_id JOIN openalex_acquisition q ON q.acquisition_id=a.acquisition_id '
            "WHERE o.discovery_run_id=? AND q.state='COMPLETE' ORDER BY a.acquisition_id", (discovery_run_id,))]
        if not ids:
            raise ValidationError('Discovery run has no COMPLETE acquisitions to import')
        return [self.import_acquisition(rid, a, label='DISCOVERY_RUN', discovery_run_id=discovery_run_id, private=private)
                for a in ids]

    def import_corpus_release(self, rid, release_id, *, private=()):
        from ..corpus_release import CorpusReleaseStore
        batch, rows = inputs.handoff_records(CorpusReleaseStore(self.db).handoff(release_id))
        return self.add_batch(rid, batch, rows, private=private)

    # -- identity -----------------------------------------------------------------------------------------------
    def works(self, rid):
        return [dict(json.loads(r['identity_json']), ordinal=r['ordinal'])
                for r in self._all('SELECT * FROM pdf_work WHERE run_id=? ORDER BY ordinal', (rid,))]

    def freeze(self, rid):
        run = self.get(rid)
        if run['state'] != 'OPEN':
            return self.inspect(rid)
        accepted = [(r['record_id'], json.loads(r['record_json'])) for r in self.records(rid, 'ACCEPTED')]
        if not accepted:
            raise ValidationError('No accepted input records; import inputs first')
        works, summary = freeze_identity(rid, accepted)
        with self.write():
            self.db.execute("UPDATE pdf_run SET state='DISCOVERING', identity_json=?, identity_sha256=? WHERE run_id=?",
                            (canonical_json(summary), digest(summary), rid))
            for n, w in enumerate(works):
                self._insert('pdf_work', dict(run_id=rid, work_id=w['work_id'], ordinal=n, work_key=w['work_key'],
                                              identity_json=canonical_json(w), identity_sha256=digest(w)))
        return self.inspect(rid)

    # -- request ledger and budget --------------------------------------------------------------------------------
    def budget(self, rid):
        row = self.db.execute('SELECT count(*), coalesce(sum(coalesce(r.bytes, q.reserved_bytes)),0) FROM pdf_request q '
                              'LEFT JOIN pdf_request_result r ON r.run_id=q.run_id AND r.request_id=q.request_id '
                              'WHERE q.run_id=?', (rid,)).fetchone()
        lookups = self.db.execute("SELECT count(DISTINCT json_extract(request_json,'$.lookup_identity')) FROM pdf_request "
                                  "WHERE run_id=? AND purpose='METADATA'", (rid,)).fetchone()[0]
        items = self.db.execute('SELECT count(DISTINCT c.work_id) FROM pdf_attempt a JOIN pdf_candidate c '
                                'ON c.run_id=a.run_id AND c.candidate_id=a.candidate_id WHERE a.run_id=?', (rid,)).fetchone()[0]
        artifacts = self.db.execute("SELECT count(DISTINCT artifact_sha256) FROM pdf_work_artifact WHERE run_id=? AND source='AUTO'",
                                    (rid,)).fetchone()[0]
        return dict(items=items, http_requests=row[0], bytes=row[1], artifacts=artifacts, provider_lookups=lookups)

    def fetch(self, rid, transport, url, purpose, max_bytes, identity, p, *, provider=None, gate=None, inspect=None, sink=None,
              private=()):
        """Fetch through the shared transport with a durable append-only request ledger and budget."""
        private = (transport.key, transport.email) + tuple(private)

        def reserve(safe, _purpose, requested):
            with self.write():
                used, cap = self.budget(rid), p['global_budget']
                reserved = requested + 65536
                if used['http_requests'] >= cap['http_requests'] or cap['bytes'] - used['bytes'] < reserved:
                    raise FetchError('GLOBAL_BUDGET_EXHAUSTED')
                if purpose == 'METADATA' and used['provider_lookups'] >= cap['provider_lookups'] and not self.db.execute(
                        "SELECT 1 FROM pdf_request WHERE run_id=? AND purpose='METADATA' AND json_extract(request_json,'$.lookup_identity')=?",
                        (rid, identity)).fetchone():
                    raise FetchError('GLOBAL_BUDGET_EXHAUSTED')
                request = dict(url=safe, purpose=purpose, reserved_bytes=reserved, lookup_identity=identity)
                try:
                    guard(request, private)
                except ValidationError:
                    raise FetchError('PRIVATE_REQUEST_REFUSED') from None
                tid = new_id('pdq')
                self._insert('pdf_request', dict(run_id=rid, request_id=tid, purpose=purpose, reserved_bytes=reserved,
                                                 request_json=canonical_json(request), request_sha256=digest(request),
                                                 created_at_utc=utc_now()))
            if sink is not None:
                sink.append(tid)
            return tid, requested

        def complete(tid, result):
            result = dict(result)
            guard(result, private)
            with self.write():
                self._insert('pdf_request_result', dict(run_id=rid, request_id=tid, bytes=result['bytes'],
                                                        result_json=canonical_json(result), result_sha256=digest(result),
                                                        finished_at_utc=utc_now()))
        return transport.fetch(url, purpose=purpose, max_bytes=max_bytes, reserve=reserve, complete=complete,
                               provider=provider, gate=gate, inspect=inspect)

    def request_result(self, rid, request_id):
        row = self._one('SELECT result_json FROM pdf_request_result WHERE run_id=? AND request_id=?', (rid, request_id))
        return json.loads(row['result_json']) if row else None

    # -- discovery ------------------------------------------------------------------------------------------------
    def _lookup(self, rid, provider, link, transport, p, gate, *, accept_failed=False):
        """Frozen provider evidence for one lookup, or None after a temporary failure.

        A temporary failure (network, timeout, 429/5xx, budget) is not frozen as evidence unless accept_failed is set,
        so a later discover or resume repeats the lookup. Its HTTP requests stay in the request ledger either way.
        """
        eid = 'pde_' + digest([provider, link])[:40]
        old = self._one('SELECT * FROM pdf_evidence WHERE run_id=? AND evidence_id=?', (rid, eid))
        if old:
            return old
        # An OpenAlex lookup is by Work ID, or by exact DOI ('doi:10.x/y') for a work known only by DOI.
        doi_link = provider == 'OPENALEX' and link.startswith('doi:')
        url = ('https://api.openalex.org/works/' + ('doi:' + quote(link[4:], safe='/') if doi_link else link)
               if provider == 'OPENALEX' else 'https://api.unpaywall.org/v2/' + quote(link, safe='/'))
        metadata = dict(provider=provider, linkage=link, request_url=url, parser='pdf-provider/1', routes=[],
                        locations_count=None,
                        authentication_mode='runtime_email_parameter' if provider == 'UNPAYWALL' else 'runtime_bearer_header')
        sink, response_sha = [], None
        try:
            body, http = self.fetch(rid, transport, url, 'METADATA', p['max_metadata_bytes'], provider + ':' + link, p,
                                    provider='UNPAYWALL' if provider == 'UNPAYWALL' else None, gate=gate, sink=sink,
                                    inspect=getattr(gate, 'inspect', None))
            guard(body, (transport.key, transport.email))
            data = json.loads(body)
            if not isinstance(data, dict):
                raise FetchError('INVALID_PROVIDER_JSON')
            if provider == 'OPENALEX' and doi_link:
                # Exact DOI echo required; the resolved Work ID is evidence only and never re-merges frozen identity.
                resolved = inputs.ids.openalex_work(data.get('id') or '')
                if not resolved or data.get('id') != 'https://openalex.org/' + resolved \
                        or inputs.ids.doi(data.get('doi') or '') != link[4:]:
                    raise FetchError('PROVIDER_ID_MISMATCH')
                metadata['resolved_openalex_id'] = resolved
            elif provider == 'OPENALEX' and data.get('id') != 'https://openalex.org/' + link:
                raise FetchError('PROVIDER_ID_MISMATCH')
            if provider == 'UNPAYWALL' and inputs.ids.doi(data.get('doi') or '') != link:
                raise FetchError('PROVIDER_ID_MISMATCH')
            routes = self._evidence_routes(provider, link, data, eid)
            response_sha = self.cas.put_bytes(body).artifact_sha256
            metadata.update(http_status=http['http_status'], failure_class=None, routes=routes,
                            locations_count=data.get('locations_count'), raw_response_preserved=True,
                            display=self._display(provider, data))
        except FetchError as exc:
            reason = normalize_reason(exc.reason)
            # A temporary failure or a blocked provider host (until an operator unblocks it) is not frozen.
            if outcome_class(reason) in ('TRANSIENT', 'BLOCKED') and not accept_failed:
                return None
            metadata.update(http_status=None, failure_class=reason, raw_response_preserved=False)
        except (ValueError, ValidationError):
            metadata.update(http_status=None, failure_class='INVALID_OR_PRIVATE_PROVIDER_RESPONSE', raw_response_preserved=False)
        metadata['request_ids'] = sink
        guard(metadata, (transport.key, transport.email))
        row = dict(run_id=rid, evidence_id=eid, provider=provider, linkage=link, response_sha256=response_sha,
                   metadata_json=canonical_json(metadata), metadata_sha256=digest(metadata), retrieved_at_utc=utc_now())
        with self.write():
            self._insert('pdf_evidence', row)
        return row

    @staticmethod
    def _display(provider, data):
        """Title, year and DOI shown in manual-download lists; never used for identity, eligibility or ranking."""
        title = (data.get('title') or data.get('display_name')) if provider == 'OPENALEX' else data.get('title')
        year = data.get('publication_year') if provider == 'OPENALEX' else data.get('year')
        return dict(title=' '.join(title.split())[:500] or None if isinstance(title, str) else None,
                    publication_year=year if isinstance(year, int) and not isinstance(year, bool) else None,
                    doi=inputs.ids.doi(data.get('doi') or '') if isinstance(data.get('doi'), str) else None)

    @staticmethod
    def _evidence_routes(provider, link, data, eid):
        def walk(value):
            if isinstance(value, dict):
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)
            elif isinstance(value, str) and value.startswith(('https://', 'http://')):
                try:
                    if sanitize_url(value) != normalize_url(value):
                        raise FetchError('PRIVATE_RESPONSE_REFUSED')
                except ValidationError:
                    pass
        walk(data)
        if provider == 'OPENALEX' and link.startswith('doi:'):
            link = inputs.ids.openalex_work(data.get('id') or '') or link
        linkage = 'https://openalex.org/' + link if provider == 'OPENALEX' else link
        return [dict(r, oa_evidence='TRUE' if r['is_oa'] else 'FALSE')
                for r in location_routes(data, provider, linkage, eid)]

    def _lookup_targets(self, work, enabled):
        targets = []
        if 'OPENALEX' in enabled:
            targets += [('OPENALEX', w) for w in work['openalex_ids']]
            if not work['openalex_ids']:
                targets += [('OPENALEX', 'doi:' + d) for d in work['dois']]
        if 'UNPAYWALL' in enabled:
            targets += [('UNPAYWALL', d) for d in work['dois']]
        return sorted(set(targets))

    def work_routes(self, rid, work, records=None):
        routes = []
        for rec_id in work['record_ids']:
            record = records[rec_id] if records is not None else json.loads(self._one(
                'SELECT record_json FROM pdf_input_record WHERE run_id=? AND record_id=?', (rid, rec_id))['record_json'])
            routes.extend(dict(r, evidence_id=rec_id, linkage=rec_id) for r in record['routes'])
        return routes

    @staticmethod
    def invalid_urls(work, records):
        return sum(1 for rec_id in work['record_ids'] for w in records[rec_id]['warnings'] if w.startswith('UNSAFE_OR_INVALID_URL'))

    def discover(self, rid, transport=None, *, online=False, clock=None, sleep=None, now=None, accept_failed_lookups=False):
        if self.get(rid)['state'] == 'OPEN':
            self.freeze(rid)
        run = self.get(rid)
        if run['state'] != 'DISCOVERING':
            return self.inspect(rid)
        p = self.policy(rid)
        enabled = [s for s in LOOKUP_SOURCES if s in p['evidence_sources_enabled']]
        gate = None
        if enabled:
            if not online or transport is None:
                raise ValidationError('This policy enables provider lookups; run discover with --online')
            if 'UNPAYWALL' in enabled and not transport.email:
                raise ValidationError('UNPAYWALL_EMAIL runtime configuration is required by this policy')
            from .engine import HostRegister, lookup_gate
            from .politeness import now_utc
            hosts = HostRegister(self, rid, now or now_utc, private=(transport.key, transport.email))
            gate = lookup_gate(p, clock=clock, sleep=sleep, now=now, hosts=hosts)
        records = {r['record_id']: json.loads(r['record_json']) for r in self.records(rid, 'ACCEPTED')}
        planned = {r['work_id'] for r in self._all('SELECT work_id FROM pdf_plan WHERE run_id=?', (rid,))}
        pending = 0
        for work in self.works(rid):
            if work['work_id'] in planned:
                continue
            routes, refs = self.work_routes(rid, work, records), []
            rows = [self._lookup(rid, provider, link, transport, p, gate, accept_failed=accept_failed_lookups)
                    for provider, link in self._lookup_targets(work, enabled)]
            if any(row is None for row in rows):
                # A plan is frozen only from complete evidence; this work waits for the next discover or resume.
                pending += 1
                continue
            for row in rows:
                routes.extend(json.loads(row['metadata_json'])['routes'])
                refs.append(dict(evidence_id=row['evidence_id'], metadata_sha256=row['metadata_sha256'],
                                 response_sha256=row['response_sha256']))
            plan = plan_work(work['work_id'], routes, p, refs, self.invalid_urls(work, records), work['oa_flags'])
            with self.write():
                self._insert('pdf_plan', dict(run_id=rid, work_id=work['work_id'], plan_json=canonical_json(plan),
                                              plan_sha256=digest(plan)))
                for c in plan['candidates']:
                    self._insert('pdf_candidate', dict(run_id=rid, candidate_id=c['candidate_id'], work_id=work['work_id'],
                                                       rank=c['rank'], eligible=int(c['eligible']),
                                                       candidate_json=canonical_json(c), candidate_sha256=digest(c)))
        if pending:
            return dict(self.inspect(rid), pending_lookup_works=pending,
                        note='Some provider lookups failed temporarily. Run discover --online (or resume --online) again '
                             'later, or add --accept-failed-lookups to plan those works without the missing evidence.')
        with self.write():
            self.db.execute("UPDATE pdf_run SET state='ACQUIRING' WHERE run_id=?", (rid,))
        return self.inspect(rid)

    def plans(self, rid):
        return {r['work_id']: json.loads(r['plan_json']) for r in self._all('SELECT * FROM pdf_plan WHERE run_id=?', (rid,))}

    def candidates(self, rid, work_id=None):
        sql = 'SELECT candidate_json FROM pdf_candidate WHERE run_id=?' + (' AND work_id=?' if work_id else '') + ' ORDER BY work_id, rank'
        return [json.loads(r['candidate_json']) for r in self._all(sql, (rid, work_id) if work_id else (rid,))]

    def attempts(self, rid, candidate_id=None):
        sql = ('SELECT a.attempt_id,a.candidate_id,a.ordinal,r.outcome,r.failure_class,r.artifact_sha256,r.result_json '
               'FROM pdf_attempt a LEFT JOIN pdf_attempt_result r ON r.run_id=a.run_id AND r.attempt_id=a.attempt_id '
               'WHERE a.run_id=?' + (' AND a.candidate_id=?' if candidate_id else '') + ' ORDER BY a.candidate_id, a.ordinal')
        rows = self._all(sql, (rid, candidate_id) if candidate_id else (rid,))
        for row in rows:
            row['result'] = json.loads(row.pop('result_json')) if row['result_json'] else None
        return rows

    # -- artifacts and links -----------------------------------------------------------------------------------------
    def register_pdf(self, stored, validation):
        """Register validated PDF bytes once per workspace (inside the caller's write transaction)."""
        body = dict(schema='pdf-artifact/1', format=validation['format'], media_type=validation['media_type'],
                    byte_size=validation['byte_size'], pages=validation['pages'], validation=validation['validation'])
        self.foundation.register_raw_artifact(stored)
        old = self._one('SELECT * FROM pdf_artifact WHERE artifact_sha256=?', (stored.artifact_sha256,))
        if old:
            if old['validation_json'] != canonical_json(body):
                raise ArtifactIntegrityError('PDF artifact metadata conflict')
            return old
        return self._insert('pdf_artifact', dict(artifact_sha256=stored.artifact_sha256, byte_size=stored.byte_size,
                                                 validation_json=canonical_json(body), validation_sha256=digest(body),
                                                 registered_at_utc=utc_now()))

    def links(self, rid, work_id=None):
        sql = 'SELECT * FROM pdf_work_artifact WHERE run_id=?' + (' AND work_id=?' if work_id else '') + ' ORDER BY rowid'
        rows = self._all(sql, (rid, work_id) if work_id else (rid,))
        return sorted(rows, key=lambda r: LINK_ORDER[r['source']])

    def add_link(self, rid, work_id, artifact_sha256, source, *, attempt_id=None, import_id=None, basis, version_claim='UNKNOWN',
                 verification):
        link = dict(link_id='pdl_' + digest([rid, work_id, artifact_sha256, source])[:40], run_id=rid, work_id=work_id,
                    artifact_sha256=artifact_sha256, source=source, attempt_id=attempt_id, import_id=import_id, basis=basis,
                    version_claim=version_claim, verification=verification, created_at_utc=utc_now())
        if self._one('SELECT 1 FROM pdf_work_artifact WHERE link_id=?', (link['link_id'],)):
            return None
        return self._insert('pdf_work_artifact', link)

    # -- host register -----------------------------------------------------------------------------------------------
    def host_event(self, host, kind, reason, *, http_status=None, url=None, until_utc=None, run_id=None, origin='ENGINE',
                   private=()):
        # A reason may quote a remote page title; a title that reflects a runtime secret or looks private is withheld.
        reason = ' '.join(str(reason).split())[:300]
        if sensitive(reason.encode('utf-8'), tuple(v for v in private if v)):
            reason = 'REASON_WITHHELD_PRIVATE_LOOKING_TEXT'
        safe = None
        if url:
            try:
                safe = sanitize_url(url)
            except ValidationError:
                safe = None
        with self.write():
            self._insert('pdf_host_event', dict(host=host, kind=kind, reason=reason, http_status=http_status, url=safe,
                                                until_utc=until_utc, run_id=run_id, origin=origin, created_at_utc=utc_now()))

    def host_state(self, host, watermark=None):
        rows = self._all('SELECT * FROM pdf_host_event WHERE host=?' + (' AND seq<=?' if watermark is not None else '') +
                         ' ORDER BY seq', (host, watermark) if watermark is not None else (host,))
        state = dict(host=host, blocked=False, reason=None, cooldown_until=None, events=len(rows))
        for row in rows:
            if row['kind'] == 'BLOCK':
                state.update(blocked=True, reason=row['reason'])
            elif row['kind'] == 'UNBLOCK':
                state.update(blocked=False, reason=None, cooldown_until=None)
            else:
                state['cooldown_until'] = max(state['cooldown_until'] or '', row['until_utc'])
        return state

    def hosts(self):
        names = [r['host'] for r in self._all('SELECT DISTINCT host FROM pdf_host_event ORDER BY host')]
        return [self.host_state(h) for h in names]

    def host_watermark(self):
        return self.db.execute('SELECT coalesce(max(seq),0) FROM pdf_host_event').fetchone()[0]

    # -- status ----------------------------------------------------------------------------------------------------
    def statuses(self, rid, *, watermark=None):
        from ..fulltext import host_of
        plans, works = self.plans(rid), self.works(rid)
        by_work = defaultdict(list)
        for c in self.candidates(rid):
            by_work[c['corpus_item_id']].append(c)
        attempts = defaultdict(list)
        for a in self.attempts(rid):
            attempts[a['candidate_id']].append(a)
        links = defaultdict(list)
        for link in self.links(rid):
            links[link['work_id']].append(link)
        host_cache = {}
        shown = {e['evidence_id']: json.loads(e['metadata_json']).get('display')
                 for e in self._all('SELECT evidence_id, metadata_json FROM pdf_evidence WHERE run_id=?', (rid,))}
        history = defaultdict(list)
        for s in self._all("SELECT session_json FROM pdf_import_session WHERE context_kind='DISCOVERY_RUN' AND context_id=? "
                           "AND kind='LEGACY_V4_FULLTEXT' ORDER BY created_at_utc, session_id", (rid,)):
            for h in json.loads(s['session_json'])['detail'].get('history', []):
                history[h['legacy_key']].append(h)

        def blocked(host):
            if host not in host_cache:
                host_cache[host] = self.host_state(host, watermark)['blocked']
            return host_cache[host]
        result = []
        for w in works:
            plan, cs = plans.get(w['work_id']), sorted(by_work[w['work_id']], key=lambda c: c['rank'])
            eligible = [c for c in cs if c['eligible']][: (plan or {}).get('attempt_limit', 0)]
            details, reasons, hosts = [], [], []
            for c in eligible:
                host = host_of(c['url'])
                hosts.append(host)
                tried = attempts[c['candidate_id']]
                if tried:
                    last = tried[-1]
                    reason = 'ACQUIRED' if last['outcome'] == 'ACQUIRED' else (last['failure_class'] or 'INTERRUPTED')
                    details.append(dict(candidate_id=c['candidate_id'], url=c['url'], host=host, attempts=len(tried), outcome=reason))
                    if reason != 'ACQUIRED':
                        reasons.append(reason)
                else:
                    reason = 'HOST_BLOCKED' if blocked(host) else 'NOT_ATTEMPTED'
                    details.append(dict(candidate_id=c['candidate_id'], url=c['url'], host=host, attempts=0, outcome=reason))
                    reasons.append(reason)
            own = links[w['work_id']]
            artifacts = [dict(artifact_sha256=l['artifact_sha256'], source=l['source'], basis=l['basis'],
                              version_claim=l['version_claim'], verification=l['verification']) for l in own]
            if own:
                status = {'AUTO': 'DOWNLOADED', 'LEGACY_V4': 'LEGACY_V4_DOWNLOADED', 'MANUAL': 'MANUAL_IMPORTED'}[own[0]['source']]
                reasons = []
            elif plan is None:
                status = 'NOT_PLANNED'
            elif not eligible:
                status = plan['no_candidate_reason']
            else:
                status = pick(reasons)
            body = dict(work_id=w['work_id'], ordinal=w['ordinal'], paper=w.get('file_key') or w['primary_legacy_key'] or w['work_id'],
                        work_key=w['work_key'], openalex_ids=w['openalex_ids'], dois=w['dois'], titles=w['titles'],
                        publication_years=w['publication_years'], identity_flags=w['flags'], status=status,
                        reasons=sorted(set(reasons)), candidates=details, artifacts=artifacts,
                        primary_artifact_sha256=own[0]['artifact_sha256'] if own else None,
                        eligible_candidate_count=len([c for c in cs if c['eligible']]), candidate_count=len(cs),
                        candidate_urls=[dict(url=c['url'], url_identity_sha256=c['url_identity_sha256'], url_type=c['url_type'],
                                             runtime_url_required=c['runtime_url_required'], eligible=c['eligible'],
                                             oa_evidence=_oa_evidence(c)) for c in cs],
                        landing_pages=(plan or {}).get('landing_pages', []), hosts=sorted(set(hosts)))
            past = [h for k in w['legacy_keys'] for h in history.get(k, ())]
            if past:
                # 4.1.0 outcomes are historical evidence; they never count as attempts of this run.
                body['legacy_v4_history'] = past
            display = [shown.get(ref['evidence_id']) for ref in (plan or {}).get('evidence', [])]
            display = [d for d in display if d]
            if display:
                # Provider bibliographic fields, kept apart from the input-derived titles/dois/years.
                body['provider_display'] = dict(
                    titles=sorted({d['title'] for d in display if d['title']})[:10],
                    dois=sorted({d['doi'] for d in display if d['doi']}),
                    publication_years=sorted({d['publication_year'] for d in display if d['publication_year'] is not None}))
            result.append(dict(body, status_sha256=digest(body)))
        return result

    # -------------------------------------------------------------------------------------------------------------
    def inspect(self, rid, work_id=None):
        run = self.get(rid)
        result = dict(run_id=rid, label=run['label'], profile=run['profile'], state=run['state'],
                      policy_sha256=run['policy_sha256'], manifest_sha256=run['manifest_sha256'],
                      batches=[self._batch_view(b) for b in self.batches(rid)],
                      identity=json.loads(run['identity_json']) if run['identity_json'] else None,
                      work_count=self.db.execute('SELECT count(*) FROM pdf_work WHERE run_id=?', (rid,)).fetchone()[0],
                      planned_work_count=self.db.execute('SELECT count(*) FROM pdf_plan WHERE run_id=?', (rid,)).fetchone()[0],
                      candidate_count=self.db.execute('SELECT count(*) FROM pdf_candidate WHERE run_id=?', (rid,)).fetchone()[0],
                      budget=self.budget(rid))
        if work_id:
            work = next((w for w in self.works(rid) if w['work_id'] == work_id), None)
            if work is None:
                raise ValidationError('Unknown work in this run')
            result['work'] = dict(identity=work, plan=self.plans(rid).get(work_id), attempts=[
                a for c in self.candidates(rid, work_id) for a in self.attempts(rid, c['candidate_id'])],
                links=self.links(rid, work_id), status=next(s for s in self.statuses(rid) if s['work_id'] == work_id))
        return result

    # -- verification -------------------------------------------------------------------------------------------------
    def _rederive(self, batch):
        detail = json.loads(batch['batch_json'])['detail']
        if batch['source_kind'] == 'FILE':
            if not batch['raw_artifact_sha256']:
                return None
            self.cas.verify(batch['raw_artifact_sha256'])
            body = self.cas.path_for_hash(batch['raw_artifact_sha256']).read_bytes()
            fmt = detail.get('requested_format', 'identifiers' if batch['source_format'] == 'IDENTIFIER_LIST' else 'auto')
            _, rows, _ = inputs.parse_bytes(batch['source_label'], body, fmt)
            return rows
        if batch['source_kind'] == 'IDENTIFIERS':
            return inputs.parse_identifiers(detail['identifiers'])[1]
        if batch['source_kind'] in ('OPENALEX_ACQUISITION', 'DISCOVERY_RUN'):
            store = self._acquisition_store()
            acquisition = store.get(detail['acquisition_id'])
            return inputs.acquisition_records(acquisition, store._replay(acquisition), batch['source_kind'])[1]
        if batch['source_kind'] == 'CORPUS_RELEASE':
            from ..corpus_release import CorpusReleaseStore
            return inputs.handoff_records(CorpusReleaseStore(self.db).handoff(detail['release_id']))[1]
        raise ArtifactIntegrityError('Unknown input batch kind')

    def verify(self, rid):
        run, p = self.get(rid), self.policy(rid)
        checks = Counter()
        for table, field, hashed in (('pdf_input_batch', 'batch_json', 'batch_sha256'), ('pdf_input_record', 'record_json', 'record_sha256'),
                                     ('pdf_work', 'identity_json', 'identity_sha256'), ('pdf_plan', 'plan_json', 'plan_sha256'),
                                     ('pdf_candidate', 'candidate_json', 'candidate_sha256'), ('pdf_evidence', 'metadata_json', 'metadata_sha256'),
                                     ('pdf_request', 'request_json', 'request_sha256'), ('pdf_request_result', 'result_json', 'result_sha256'),
                                     ('pdf_attempt_result', 'result_json', 'result_sha256')):
            for row in self._all('SELECT ' + field + ',' + hashed + ' FROM ' + table + ' WHERE run_id=?', (rid,)):
                if digest(json.loads(row[field])) != row[hashed]:
                    raise ArtifactIntegrityError('PDF acquisition row hash mismatch')
                checks['hashed_rows'] += 1
        stored_records = defaultdict(list)
        for r in self.records(rid):
            stored_records[r['batch_id']].append((r['status'], r['reason'], json.loads(r['record_json'])))
        for batch in self.batches(rid):
            rows = self._rederive(batch)
            if rows is None:
                checks['batches_hash_only'] += 1
                continue
            if [(s, r, rec) for s, r, rec in rows] != stored_records[batch['batch_id']]:
                raise ArtifactIntegrityError('Input batch no longer re-derives from its exact source')
            checks['batches_rederived'] += 1
        if run['state'] != 'OPEN':
            accepted = [(r['record_id'], json.loads(r['record_json'])) for r in self.records(rid, 'ACCEPTED')]
            works, summary = freeze_identity(rid, accepted)
            stored = self.works(rid)
            if [dict(w, ordinal=n) for n, w in enumerate(works)] != stored or canonical_json(summary) != run['identity_json']:
                raise ArtifactIntegrityError('Frozen work identity mismatch')
            records = {rid_: rec for rid_, rec in accepted}
            evidence = {e['evidence_id']: e for e in self._all('SELECT * FROM pdf_evidence WHERE run_id=?', (rid,))}
            for e in evidence.values():
                meta = json.loads(e['metadata_json'])
                if e['response_sha256']:
                    self.cas.verify(e['response_sha256'])
                    data = json.loads(self.cas.path_for_hash(e['response_sha256']).read_bytes())
                    if self._evidence_routes(e['provider'], e['linkage'], data, e['evidence_id']) != meta['routes']:
                        raise ArtifactIntegrityError('Provider evidence projection mismatch')
            plans = self.plans(rid)
            expected_candidates = []
            for w in stored:
                if w['work_id'] not in plans:
                    continue
                plan = plans[w['work_id']]
                routes = self.work_routes(rid, w, records)
                for ref in plan['evidence']:
                    routes.extend(json.loads(evidence[ref['evidence_id']]['metadata_json'])['routes'])
                if plan_work(w['work_id'], routes, p, plan['evidence'], self.invalid_urls(w, records), w['oa_flags']) != plan:
                    raise ArtifactIntegrityError('Frozen candidate plan mismatch')
                expected_candidates.extend(plan['candidates'])
            if sorted(self.candidates(rid), key=digest) != sorted(expected_candidates, key=digest):
                raise ArtifactIntegrityError('Candidate table mismatch')
            if run['state'] in ('ACQUIRING', 'CLOSED') and set(plans) != {w['work_id'] for w in stored}:
                raise ArtifactIntegrityError('Incomplete frozen plans')
            checks['works'] = len(stored)
        candidates = {c['candidate_id']: c for c in self.candidates(rid)}
        for q in self._all('SELECT * FROM pdf_request WHERE run_id=?', (rid,)):
            body = json.loads(q['request_json'])
            if sanitize_url(body['url']) != body['url'] or body['reserved_bytes'] != q['reserved_bytes']:
                raise ArtifactIntegrityError('Unsafe or inconsistent request ledger row')
            result = self.request_result(rid, q['request_id'])
            charged = self._one('SELECT bytes FROM pdf_request_result WHERE run_id=? AND request_id=?', (rid, q['request_id']))
            if result is not None and (type(result['bytes']) is not int or not 0 <= result['bytes'] <= q['reserved_bytes']
                                       or charged['bytes'] != result['bytes']):
                raise ArtifactIntegrityError('Request byte accounting mismatch')
        acquired = {}
        for a in self.attempts(rid):
            c = candidates.get(a['candidate_id'])
            if c is None or not c['eligible'] or a['ordinal'] >= p['max_attempts_per_candidate']:
                raise ArtifactIntegrityError('Ineligible or unbounded attempt')
            if a['outcome'] == 'ACQUIRED':
                if a['result']['artifact_sha256'] != a['artifact_sha256'] or not a['result']['request_ids']:
                    raise ArtifactIntegrityError('Successful attempt lacks request provenance')
                http = self.request_result(rid, a['result']['request_ids'][-1])
                if not http or http['http_status'] != 200:
                    raise ArtifactIntegrityError('Successful artifact requires HTTP 200 provenance')
                acquired[a['attempt_id']] = (c['corpus_item_id'], a['artifact_sha256'])
        # Several successful attempts of one work may return identical bytes; they share one link.
        auto = [l for l in self.links(rid) if l['source'] == 'AUTO']
        if {(l['work_id'], l['artifact_sha256']) for l in auto} != set(acquired.values()) or \
                any(acquired.get(l['attempt_id']) != (l['work_id'], l['artifact_sha256']) for l in auto):
            raise ArtifactIntegrityError('Automatic artifact link mismatch')
        held = self._verify_manual(rid, {w['work_id'] for w in self.works(rid)}, checks)
        artifacts = {l['artifact_sha256'] for l in self.links(rid)} | held
        for digest_ in sorted(artifacts):
            self.verify_artifact(digest_)
        checks['artifacts'] = len(artifacts)
        used = self.budget(rid)
        if any(used[k] > p['global_budget'][k] for k in used):
            raise ArtifactIntegrityError('Budget exceeded')
        if run['state'] == 'CLOSED':
            manifest = json.loads(run['manifest_json'])
            if self.manifest(rid, manifest['host_event_watermark']) != manifest:
                raise ArtifactIntegrityError('Closed run manifest mismatch')
        return dict(verified=True, mode='DB_PROVENANCE_AND_ARTIFACT_BYTES_OFFLINE', run_id=rid, state=run['state'],
                    checks=dict(sorted(checks.items())), budget=used)

    def _verify_manual(self, rid, work_ids, checks):
        """Hash-check this run's manual sessions, imports and reconciliation; return every artifact they hold."""
        context = ('DISCOVERY_RUN', rid)
        for s in self._all('SELECT session_json, session_sha256 FROM pdf_import_session WHERE context_kind=? AND context_id=?', context):
            if digest(json.loads(s['session_json'])) != s['session_sha256']:
                raise ArtifactIntegrityError('Manual import session hash mismatch')
            checks['import_sessions'] += 1
        imports = {m['import_id']: m for m in self._all(
            'SELECT m.* FROM pdf_manual_import m JOIN pdf_import_session s ON s.session_id=m.session_id '
            'WHERE s.context_kind=? AND s.context_id=?', context)}
        for m in imports.values():
            if digest(json.loads(m['detail_json'])) != m['detail_sha256']:
                raise ArtifactIntegrityError('Manual import row hash mismatch')
            checks['manual_imports'] += 1
        links = [l for l in self.links(rid) if l['source'] != 'AUTO']
        for l in links:
            m = imports.get(l['import_id'])
            if m is None or m['artifact_sha256'] != l['artifact_sha256']:
                raise ArtifactIntegrityError('Manual link lacks its import provenance')
        # Several imports or decisions may name the same work and bytes; they share one link (many-to-one).
        associations = {(l['work_id'], l['artifact_sha256']) for l in links}
        for m in imports.values():
            target = json.loads(m['detail_json']).get('target_id')
            if m['outcome'] == 'LINKED' and (target, m['artifact_sha256']) not in associations:
                raise ArtifactIntegrityError('Linked manual import without a link')
        for r in self._all('SELECT r.item_id, r.import_id, d.decision, d.target_id FROM pdf_reconciliation r '
                           'LEFT JOIN pdf_reconciliation_decision d ON d.item_id=r.item_id WHERE r.context_kind=? AND r.context_id=?',
                           context):
            m = imports.get(r['import_id'])
            if m is None or m['outcome'] != 'RECONCILIATION_REQUIRED':
                raise ArtifactIntegrityError('Reconciliation item lacks its manual import')
            if r['decision'] == 'LINK' and (r['target_id'] not in work_ids or
                                            (r['target_id'], m['artifact_sha256']) not in associations):
                raise ArtifactIntegrityError('Reconciliation decision without its link')
            checks['reconciliation_items'] += 1
        return {m['artifact_sha256'] for m in imports.values() if m['artifact_sha256']}

    def verify_artifact(self, artifact_sha256):
        row = self._one('SELECT * FROM pdf_artifact WHERE artifact_sha256=?', (artifact_sha256,))
        if row is None:
            raise ArtifactIntegrityError('Unregistered PDF artifact')
        stored = self.cas.verify(artifact_sha256)
        meta = validate_pdf(self.cas.path_for_hash(artifact_sha256).read_bytes(), STRUCTURE_POLICY)
        body = dict(schema='pdf-artifact/1', format=meta['format'], media_type=meta['media_type'], byte_size=meta['byte_size'],
                    pages=meta['pages'], validation=meta['validation'])
        if canonical_json(body) != row['validation_json'] or stored.byte_size != row['byte_size']:
            raise ArtifactIntegrityError('PDF artifact metadata mismatch')
        return stored

    # -- manifest and close ----------------------------------------------------------------------------------------------
    def manifest(self, rid, watermark, state=None):
        from .report import build_manifest
        return build_manifest(self, rid, watermark, state)

    def close(self, rid):
        run = self.get(rid)
        if run['state'] == 'CLOSED':
            self.verify(rid)
            return self.inspect(rid)
        if run['state'] != 'ACQUIRING':
            raise ValidationError('Run discover before closing the run')
        self.verify(rid)
        manifest = self.manifest(rid, self.host_watermark(), 'CLOSED')
        with self.write():
            self.db.execute("UPDATE pdf_run SET state='CLOSED', manifest_json=?, manifest_sha256=?, closed_at_utc=? WHERE run_id=?",
                            (canonical_json(manifest), digest(manifest), utc_now(), rid))
        return self.inspect(rid)


__all__ = ['PdfAcquisitionStore', 'SCHEMA_VERSION', 'PRIORITY', 'outcome_class']
