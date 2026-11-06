"""Unified PDF acquisition: inputs, identity, policy, polite download engine, resume, manual ingestion,
legacy v4.1.0 import, review bridge, exports and offline verification. Synthetic and offline only."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from pdf_acquisition_fixtures import (CHALLENGE, NOT_FOUND, PDF, PDF2, PDF2_OK, PDF_OK, FakeWeb, PdfCase, json, policy, row,
                                      tree_hashes, write_csv)
from fulltext_acquisition_fixtures import FullCase, Response, policy as review_policy
from reprovane.core import ArtifactIntegrityError, FoundationError, ValidationError
from reprovane.core.db import Database
from reprovane.pdf_acquisition import DiscoveryPolicy, PdfAcquisitionStore, default_policy, verify_export
from reprovane.pdf_acquisition import inputs
from reprovane.pdf_acquisition.__main__ import main as pdf_main
from reprovane.pdf_acquisition.legacy import import_legacy_fulltext
from reprovane.pdf_acquisition.maintenance import quarantine, scan
from reprovane.pdf_acquisition.manual import decide, manual_import, reconciliation
from reprovane.pdf_acquisition.report import FILES, export, export_pdfs, review_status
from reprovane.fulltext_acquisition import DEFAULT_POLICY as STAGE11B_POLICY

A = 'https://a.example.org'
B = 'https://b.example.org'
C = 'https://c.example.org'


class TestPolicy(unittest.TestCase):
    def test_canonical_and_hash_stable(self):
        p = default_policy()
        q = dict(reversed(list(p.items())))
        q['allowed_versions'] = list(reversed(q['allowed_versions']))
        self.assertEqual(DiscoveryPolicy.from_value(p).sha256, DiscoveryPolicy.from_value(q).sha256)

    def test_change_changes_hash(self):
        self.assertNotEqual(DiscoveryPolicy.from_value(policy()).sha256, DiscoveryPolicy.from_value(policy(max_pdf_bytes=1000)).sha256)

    def test_incomplete_or_unknown_fields_rejected(self):
        p = policy()
        del p['retry_count']
        for value in (p, policy(scrape_landing_pages=True)):
            with self.assertRaises(ValidationError):
                DiscoveryPolicy.from_value(value)

    def test_invalid_values_rejected(self):
        for change in (dict(allowed_versions=['guess']), dict(retry_count=99), dict(request_timeout=float('nan')),
                       dict(min_pdf_bytes=10, max_pdf_bytes=5), dict(concurrency='PARALLEL'), dict(version_rank=['UNKNOWN']),
                       dict(robots_policy='IGNORE'), dict(global_budget=dict(items=1)), dict(block_on_plain_403='yes')):
            with self.assertRaises(ValidationError, msg=str(change)):
                DiscoveryPolicy.from_value(policy(**change))

    def test_review_profile_is_stage11b_policy(self):
        self.assertEqual(default_policy('review'), STAGE11B_POLICY)
        self.assertFalse(default_policy('review')['allow_unknown_version'])


class TestInputs(PdfCase):
    def test_legacy_csv_detected_and_mapped(self):
        path = write_csv(self.base / 'x_main.csv', [dict(row('W1', A + '/1.pdf'), main_corpus='True', scimago_best_quartile='Q1')])
        batch, rows, body = inputs.parse_file(path)
        self.assertEqual(batch['source_format'], 'LEGACY_V4_1_0_FLAT')
        self.assertEqual(batch['detail']['field_mapping']['best_oa_pdf_url'], 'best_oa_pdf_url')
        self.assertEqual(batch['detail']['file_family'], 'LEGACY_MAIN')
        status, reason, record = rows[0]
        self.assertEqual((status, record['openalex_ids'], record['dois'], record['legacy_key']), ('ACCEPTED', ['W1'], ['10.9999/w1'], 'W1'))
        self.assertEqual(record['historical']['screening_authority'], 'NONE')
        self.assertEqual(record['historical']['machine_classification']['main_corpus'], 'True')

    def test_missing_fields_and_invalid_rows(self):
        rid = self.store.create_run()['run_id']
        path = write_csv(self.base / 'generic.csv', [dict(title='', doi='not-a-doi'), dict(doi='10.1234/ok'), dict(title='Only title')])
        batch = self.store.import_file(rid, path)
        self.assertEqual(batch['counts'], dict(rows=3, accepted=2, rejected=1))
        self.assertEqual(batch['rejected_reasons'], {'NO_IDENTIFIER_OR_CONTENT': 1})
        self.store.discover(rid)
        self.assertEqual(sorted(s['status'] for s in self.store.statuses(rid)), ['NO_OA_URL', 'NO_OA_URL'])

    def test_duplicate_doi_and_openalex_rows_merge_exactly(self):
        rid = self.make_run([row('W1', A + '/1.pdf'), row('W1', A + '/1.pdf', title='Different title'),
                             dict(doi='https://doi.org/10.5555/X'), dict(doi='10.5555/x', title='Same DOI')])
        identity = self.store.inspect(rid)['identity']
        self.assertEqual((identity['unique_works'], identity['duplicate_records']), (2, 2))

    def test_doi_record_joins_openalex_work_by_exact_doi(self):
        rid = self.make_run([row('W1', A + '/1.pdf'), dict(doi='10.9999/w1', best_oa_pdf_url=A + '/alt.pdf')])
        works = self.store.works(rid)
        self.assertEqual(len(works), 1)
        self.assertEqual(len(self.store.candidates(rid)), 2)

    def test_doi_conflict_between_openalex_ids_is_reported_not_merged(self):
        rid = self.make_run([row('W1', doi='10.7777/shared'), row('W2', doi='10.7777/shared'), dict(doi='10.7777/shared')])
        works = self.store.works(rid)
        self.assertEqual(len(works), 3)
        kinds = {i['kind'] for i in self.store.inspect(rid)['identity']['issues']}
        self.assertIn('DOI_SHARED_BY_SEVERAL_WORKS', kinds)
        self.assertIn('DOI_RECORD_MATCHES_SEVERAL_OPENALEX_WORKS', kinds)
        self.assertIn('AMBIGUOUS_DOI_OWNERSHIP', [f for w in works for f in w['flags']])

    def test_same_title_never_merges(self):
        rid = self.make_run([dict(title='Same Title', best_oa_pdf_url=A + '/a.pdf'), dict(title='Same Title', best_oa_pdf_url=A + '/b.pdf')])
        self.assertEqual(len(self.store.works(rid)), 2)

    def test_reimport_same_file_is_idempotent(self):
        rid = self.store.create_run()['run_id']
        path = write_csv(self.base / 'x_main.csv', [row('W1')])
        self.assertFalse(self.store.import_file(rid, path)['reused'])
        self.assertTrue(self.store.import_file(rid, path)['reused'])
        self.assertEqual(self.table_count('pdf_input_record'), 1)

    def test_inputs_frozen_after_discovery(self):
        rid = self.make_run([row('W1')])
        with self.assertRaises(ValidationError):
            self.store.import_identifiers(rid, ['W2'])

    def test_identifier_list_and_jsonl_formats(self):
        rid = self.store.create_run()['run_id']
        self.store.import_identifiers(rid, ['https://openalex.org/W5', 'doi:10.1234/abc', 'nonsense'])
        works_path = self.base / 'works.jsonl'
        work = dict(id='https://openalex.org/W6', doi='https://doi.org/10.1234/w6', title='Six', publication_year=2023,
                    open_access=dict(is_oa=True, oa_url=A + '/landing6'),
                    best_oa_location=dict(is_oa=True, pdf_url=A + '/6.pdf', landing_page_url=A + '/landing6', version='acceptedVersion',
                                          source=dict(type='repository')),
                    locations=[dict(is_oa=True, pdf_url=A + '/6.pdf', landing_page_url=A + '/landing6', version='acceptedVersion',
                                    source=dict(type='repository'))])
        works_path.write_text(json.dumps(work) + '\n', encoding='utf-8')
        batch = self.store.import_file(rid, works_path)
        self.assertEqual(batch['source_format'], 'OPENALEX_WORK_JSON')
        self.store.discover(rid)
        eligible = [c for c in self.store.candidates(rid) if c['eligible']]
        self.assertEqual([c['url'] for c in eligible], [A + '/6.pdf'])
        self.assertEqual(eligible[0]['version'], 'acceptedVersion')
        self.assertEqual(self.store.inspect(rid)['batches'][0]['rejected_reasons'], {'INVALID_IDENTIFIER': 1})

    def test_saved_openalex_acquisition_import(self):
        from openalex_acquisition_fixtures import Session, Response as PageResponse, page, work
        from reprovane.openalex import OpenAlexClient
        from reprovane.openalex_acquisition import OpenAlexAcquisitionStore
        session = Session([PageResponse(page([work(1, best_oa_location=dict(is_oa=True, pdf_url=A + '/w1.pdf', version='publishedVersion',
                                                                                   source=dict(type='journal'))), work(2)]))])
        client = OpenAlexClient('', session=session, max_attempts=1, sleeper=lambda _: None)
        acquisition = OpenAlexAcquisitionStore(self.db).acquire(dict(search='synthetic', max_records=2, page_size=2), client)
        rid = self.store.create_run()['run_id']
        batch = self.store.import_acquisition(rid, acquisition['acquisition_id'])
        self.assertEqual(batch['counts']['accepted'], 2)
        self.store.discover(rid)
        self.assertIn(A + '/w1.pdf', [c['url'] for c in self.store.candidates(rid) if c['eligible']])
        self.assertTrue(self.store.verify(rid)['verified'])

    def test_secret_like_source_is_not_archived(self):
        rid = self.store.create_run()['run_id']
        path = write_csv(self.base / 'signed.csv', [row('W1', A + '/1.pdf?token=abc123secretvalue')])
        batch = self.store.import_file(rid, path)
        self.assertIsNone(batch['raw_artifact_sha256'])
        self.assertIn('RAW_SOURCE_NOT_ARCHIVED_SECRET_LIKE_CONTENT', batch['warnings'])
        dump = '\n'.join(r[0] for r in self.db.execute('SELECT record_json FROM pdf_input_record'))
        self.assertNotIn('abc123secretvalue', dump)

    def test_private_runtime_value_refuses_import(self):
        rid = self.store.create_run()['run_id']
        path = write_csv(self.base / 'leak.csv', [row('W1', title='contains SYNTHETIC-PRIVATE-KEY')])
        with self.assertRaises(ValidationError):
            self.store.import_file(rid, path, private=('SYNTHETIC-PRIVATE-KEY',))
        self.assertEqual(self.table_count('pdf_input_record'), 0)


class TestCandidates(PdfCase):
    def test_best_pdf_url_before_oa_url_and_landing_never_attempted(self):
        rid = self.make_run([row('W1', A + '/best.pdf', A + '/oa', landing=A + '/landing')])
        eligible = [c['url'] for c in self.store.candidates(rid) if c['eligible']]
        self.assertEqual(eligible, [A + '/best.pdf', A + '/oa'])
        self.assertEqual(self.store.plans(rid)[self.store.works(rid)[0]['work_id']]['landing_pages'], [A + '/landing'])

    def test_same_url_in_two_fields_is_one_candidate_with_two_routes(self):
        rid = self.make_run([row('W1', A + '/same.pdf', A + '/same.pdf')])
        candidates = self.store.candidates(rid)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(candidates[0]['routes']), 2)

    def test_closed_flag_means_no_request(self):
        rid = self.make_run([row('W1', A + '/1.pdf', is_oa='False'), row('W2', is_oa='False'), row('W3')])
        st = self.status(rid)
        self.assertEqual([st[k]['status'] for k in ('W1', 'W2', 'W3')], ['NOT_OPEN_ACCESS', 'NOT_OPEN_ACCESS', 'NO_OA_URL'])
        self.download(rid)
        self.assertEqual(self.web.calls, [])

    def test_unknown_version_is_unverified_evidence(self):
        self.web.add(A + '/1.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.download(rid)
        artifact = self.status(rid)['W1']['artifacts'][0]
        self.assertEqual((artifact['version_claim'], artifact['verification']), ('UNKNOWN', 'VERSION_UNKNOWN_UNVERIFIED'))

    def test_explicit_policy_can_refuse_unknown_version(self):
        rid = self.make_run([row('W1', A + '/1.pdf')], p=policy(allowed_versions=['publishedVersion', 'acceptedVersion']))
        self.assertEqual(self.status(rid)['W1']['status'], 'ONLY_UNKNOWN_VERSION')

    def test_require_explicit_open_access(self):
        rid = self.make_run([{k: v for k, v in row('W1', A + '/1.pdf').items() if k != 'is_open_access'}],
                            p=policy(oa_evidence_policy='EXPLICIT_OPEN_REQUIRED'))
        self.assertEqual(self.status(rid)['W1']['status'], 'NO_ACCEPTABLE_OA')


class TestDownloadEngine(PdfCase):
    def test_direct_success_headers_and_pacing(self):
        self.web.add(A + '/1.pdf', PDF_OK)
        self.web.add(A + '/2.pdf', PDF2_OK)
        rid = self.make_run([row('W1', A + '/1.pdf'), row('W2', A + '/2.pdf')])
        result = self.download(rid)
        self.assertEqual(result['acquired'], 2)
        pdf_calls = [c for c in self.web.calls if c['url'].endswith('.pdf')]
        self.assertTrue(all(c['headers']['User-Agent'].startswith('Reprovane/') and 'Authorization' not in c['headers'] for c in pdf_calls))
        self.assertIn('application/pdf', pdf_calls[0]['headers']['Accept'])
        self.assertTrue(any(s >= 1.9 for s in self.sleeps))   # 2 s per-host spacing
        self.assertEqual(self.web.calls[0]['url'], A + '/robots.txt')
        self.assertTrue(self.store.verify(rid)['verified'])

    def test_redirect_followed_and_recorded(self):
        self.web.add(A + '/go', (302, {'Location': B + '/final.pdf'}, b''))
        self.web.add(B + '/final.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/go')])
        self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')
        self.assertEqual(self.store.attempts(rid)[0]['result']['chain'], ['a.example.org', 'b.example.org'])

    def test_http_403_blocks_host_persistently(self):
        self.web.add(A + '/1.pdf', (403, {'Content-Type': 'text/html'}, b'<html>no</html>'))
        rid = self.make_run([row('W1', A + '/1.pdf'), row('W2', A + '/2.pdf')])
        self.download(rid)
        st = self.status(rid)
        self.assertEqual((st['W1']['status'], st['W2']['status']), ('BOT_CHECK', 'HOST_BLOCKED'))
        self.assertEqual(self.web.requested('/2.pdf'), [])
        self.assertTrue(self.store.host_state('a.example.org')['blocked'])
        rid2 = self.make_run([row('W3', A + '/3.pdf')], name='second.csv')
        self.download(rid2)
        self.assertEqual(self.web.requested('/3.pdf'), [])

    def test_unblock_allows_retry(self):
        self.web.add(A + '/1.pdf', (403, {}, b'denied'), PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.download(rid)
        self.store.host_event('a.example.org', 'UNBLOCK', 'operator', origin='OPERATOR')
        self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')

    def test_captcha_and_challenge_pages(self):
        self.web.add(A + '/1.pdf', CHALLENGE)
        self.web.add(B + '/2.pdf', (200, {'Content-Type': 'text/html', 'cf-mitigated': 'challenge'}, b'<html>x</html>'))
        rid = self.make_run([row('W1', A + '/1.pdf'), row('W2', B + '/2.pdf')])
        self.download(rid)
        self.assertEqual({s['status'] for s in self.store.statuses(rid)}, {'BOT_CHECK'})

    def test_not_found_is_permanent_until_retry_failed(self):
        self.web.add(A + '/gone.pdf', NOT_FOUND, PDF_OK)
        rid = self.make_run([row('W1', A + '/gone.pdf')])
        self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'HTTP_404')
        self.download(rid)
        self.assertEqual(len(self.web.requested('gone')), 1)
        self.download(rid, retry_failed=True)
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')

    def test_429_short_retry_after_is_retried_in_place(self):
        self.web.add(A + '/1.pdf', (429, {'Retry-After': '3'}, b'slow'), PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')
        self.assertIn(3.0, self.sleeps)

    def test_429_long_retry_after_sets_persistent_cooldown(self):
        self.web.add(A + '/1.pdf', (429, {'Retry-After': '7200'}, b'slow'), PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf'), row('W2', A + '/2.pdf')])
        self.download(rid)
        st = self.status(rid)
        self.assertEqual(st['W1']['status'], 'RATE_LIMITED')
        self.assertEqual(self.web.requested('/2.pdf'), [])
        self.assertTrue(self.store.host_state('a.example.org')['cooldown_until'])
        self.download(rid)
        self.assertEqual(len(self.web.requested('/1.pdf')), 1)   # still cooling down
        self.now = self.now + timedelta(hours=3)
        self.web.add(A + '/2.pdf', PDF2_OK)
        self.download(rid)
        self.assertEqual({s['status'] for s in self.store.statuses(rid)}, {'DOWNLOADED'})

    def test_5xx_bounded_retries_then_transient(self):
        self.web.add(A + '/1.pdf', (500, {}, b'error'))
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.download(rid)
        self.assertEqual(len(self.web.requested('/1.pdf')), 3)    # retry_count=2
        self.assertEqual(self.status(rid)['W1']['status'], 'HTTP_5XX')
        for _ in range(4):
            self.download(rid)
        self.assertEqual(len(self.web.requested('/1.pdf')), 9)    # max_attempts_per_candidate=3, never more
        self.assertTrue(self.store.verify(rid)['verified'])

    def test_503_with_long_retry_after_cools_down(self):
        self.web.add(A + '/1.pdf', (503, {'Retry-After': '86400'}, b'maintenance'))
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'HTTP_5XX')
        self.assertEqual(len(self.web.requested('/1.pdf')), 1)

    def test_robots_disallow_allow_and_crawl_delay(self):
        self.web.add(A + '/robots.txt', (200, {}, b'User-agent: *\nDisallow: /private/\nCrawl-delay: 7\n'))
        self.web.add(A + '/ok.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/private/1.pdf'), row('W2', A + '/ok.pdf')])
        self.download(rid)
        st = self.status(rid)
        self.assertEqual((st['W1']['status'], st['W2']['status']), ('ROBOTS_DISALLOWED', 'DOWNLOADED'))
        self.assertEqual(self.web.requested('/private/'), [])
        self.assertTrue(any(abs(s - 7) < 0.01 for s in self.sleeps))

    def test_robots_specific_agent_group(self):
        self.web.add(A + '/robots.txt', (200, {}, b'User-agent: Reprovane\nDisallow: /\n\nUser-agent: *\nAllow: /\n'))
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'ROBOTS_DISALLOWED')

    def test_robots_unreachable_pauses_host_without_pdf_request(self):
        self.web.add(A + '/robots.txt', (500, {}, b'down'))
        rid = self.make_run([row('W1', A + '/1.pdf'), row('W2', A + '/2.pdf')])
        self.download(rid)
        self.assertEqual(self.web.requested('.pdf'), [])
        self.assertEqual(self.status(rid)['W1']['status'], 'ROBOTS_UNREACHABLE')

    def test_robots_404_means_allow_all(self):
        self.web.add(A + '/robots.txt', NOT_FOUND)
        self.web.add(A + '/1.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')

    def test_redirect_to_private_address_refused(self):
        def dns(host, *a, **kw):
            return [(2, 1, 6, '', ('10.0.0.5' if host == 'internal.example.org' else '93.184.216.34', 443))]
        self.resolver = dns
        self.web.add(A + '/go', (302, {'Location': 'https://internal.example.org/secret.pdf'}, b''))
        rid = self.make_run([row('W1', A + '/go'), row('W2', 'https://169.254.169.254/latest/meta-data'),
                             row('W3', 'http://localhost/x.pdf')])
        self.download(rid)
        self.assertEqual(self.web.requested('internal'), [])
        self.assertEqual(self.web.requested('169.254'), [])
        self.assertEqual(self.status(rid)['W1']['status'], 'UNSAFE_URL')
        self.assertEqual(self.status(rid)['W2']['status'], 'UNSAFE_URL')

    def test_redirect_into_blocked_host_never_contacts_it(self):
        self.store.host_event('b.example.org', 'BLOCK', 'operator', origin='OPERATOR')
        self.web.add(A + '/to-b', (302, {'Location': B + '/x.pdf'}, b''))
        rid = self.make_run([row('W1', A + '/to-b')])
        self.download(rid)
        self.assertEqual(self.web.requested('b.example.org'), [])
        self.assertEqual(self.status(rid)['W1']['status'], 'HOST_BLOCKED')

    def test_bot_check_after_redirect_blocks_chain_but_not_resolver(self):
        self.web.add('https://doi.org/10.1/x', (302, {'Location': A + '/hop'}, b''))
        self.web.add(A + '/hop', (302, {'Location': B + '/x.pdf'}, b''))
        self.web.add(B + '/x.pdf', CHALLENGE)
        rid = self.make_run([row('W1', 'https://doi.org/10.1/x')])
        self.download(rid)
        self.assertTrue(self.store.host_state('b.example.org')['blocked'])
        self.assertTrue(self.store.host_state('a.example.org')['blocked'])
        self.assertFalse(self.store.host_state('doi.org')['blocked'])

    def test_pdf_payload_validation(self):
        truncated = (200, {'Content-Type': 'application/pdf', 'Content-Length': str(len(PDF) + 50)}, PDF)
        self.web.add(A + '/html.pdf', (200, {'Content-Type': 'application/pdf'}, b'<html>Not a PDF</html>' * 20))
        self.web.add(A + '/corrupt.pdf', (200, {}, b'%PDF-1.7\n' + b'x' * 400 + b'\n%%EOF'))
        self.web.add(A + '/cut.pdf', truncated)
        self.web.add(A + '/noeof.pdf', (200, {}, PDF[:-40]))
        rid = self.make_run([row('W1', A + '/html.pdf'), row('W2', A + '/corrupt.pdf'), row('W3', A + '/cut.pdf'),
                             row('W4', A + '/noeof.pdf')])
        self.download(rid)
        st = self.status(rid)
        self.assertEqual([st[k]['status'] for k in ('W1', 'W2', 'W3', 'W4')],
                         ['NOT_PDF', 'PDF_VALIDATION_FAILED', 'TRUNCATED', 'PDF_VALIDATION_FAILED'])
        self.assertEqual(self.table_count('pdf_artifact'), 0)

    def test_too_large_declared_and_streamed(self):
        big = b'%PDF-1.4\n' + b'0' * 3000
        self.web.add(A + '/declared.pdf', (200, {'Content-Length': '999999'}, big))
        self.web.add(A + '/streamed.pdf', (200, {'Content-Length': 'omit'}, big))
        rid = self.make_run([row('W1', A + '/declared.pdf'), row('W2', A + '/streamed.pdf')], p=policy(max_pdf_bytes=2000))
        self.download(rid)
        self.assertEqual({s['status'] for s in self.store.statuses(rid)}, {'TOO_LARGE'})

    def test_interrupted_download_resumes(self):
        self.web.add(A + '/1.pdf', KeyboardInterrupt(), PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf')])
        with self.assertRaises(KeyboardInterrupt):
            self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'INTERRUPTED')
        self.assertTrue(self.store.verify(rid)['verified'])
        self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')
        self.assertEqual([a['ordinal'] for a in self.store.attempts(rid)], [0, 1])
        self.assertTrue(self.store.verify(rid)['verified'])

    def test_crash_after_cas_placement_leaves_harmless_orphan(self):
        self.web.add(A + '/1.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf')])
        with patch.object(PdfAcquisitionStore, 'register_pdf', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.download(rid)
        report = scan(self.db, self.ws)
        self.assertEqual([o['sha256'] for o in report['orphans']], [hashlib.sha256(PDF).hexdigest()])
        self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')
        self.assertEqual(scan(self.db, self.ws)['orphans'], [])

    def test_repeat_download_is_idempotent(self):
        self.web.add(A + '/1.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.download(rid)
        before = len(self.web.calls)
        result = self.download(rid)
        self.assertEqual(len(self.web.calls), before)
        self.assertEqual(result['skipped_with_pdf'], 1)

    def test_same_pdf_from_several_sources_is_one_artifact(self):
        self.web.add(A + '/1.pdf', PDF_OK)
        self.web.add(B + '/mirror.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf'), row('W2', B + '/mirror.pdf')])
        self.download(rid)
        self.assertEqual(self.table_count('pdf_artifact'), 1)
        self.assertEqual(self.table_count('pdf_work_artifact'), 2)

    def test_first_failure_then_second_candidate(self):
        self.web.add(A + '/landing', (200, {'Content-Type': 'text/html'}, b'<html><title>Article</title>' + b' ' * 30000 + b'</html>'))
        self.web.add(A + '/p.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/landing', A + '/p.pdf')])
        self.download(rid)
        st = self.status(rid)['W1']
        self.assertEqual(st['status'], 'DOWNLOADED')
        self.assertEqual([c['outcome'] for c in st['candidates']], ['NOT_PDF', 'ACQUIRED'])

    def test_budget_and_limit(self):
        for n in range(1, 5):
            self.web.add(A + f'/{n}.pdf', PDF_OK if n % 2 else PDF2_OK)
        rid = self.make_run([row(f'W{n}', A + f'/{n}.pdf') for n in range(1, 5)])
        self.assertEqual(self.download(rid, limit=1)['works_attempted'], 1)
        capped = self.make_run([row('W9', B + '/9.pdf'), row('W8', B + '/8.pdf')], name='cap.csv',
                               p=policy(global_budget=dict(items=10, http_requests=2, bytes=10 ** 9, artifacts=10, provider_lookups=10)))
        self.web.add(B + '/9.pdf', PDF_OK)
        result = self.download(capped)
        self.assertTrue(result['budget_exhausted'])
        self.assertLessEqual(self.store.budget(capped)['http_requests'], 2)
        self.assertTrue(self.store.verify(capped)['verified'])

    def test_signed_url_needs_runtime_url_and_is_never_stored(self):
        rid = self.make_run([row('W1', A + '/1.pdf?signature=SECRETSIG123')])
        self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'RUNTIME_URL_REQUIRED')
        self.assertEqual(self.web.calls, [])
        with contextlib.closing(sqlite3.connect(self.ws / 'db' / 'litintel.sqlite')) as connection:
            self.assertFalse(any('SECRETSIG123' in line for line in connection.iterdump()))
        # The exact signed URL can be re-read from the user's own input file for one command, in memory only.
        from reprovane.pdf_acquisition.engine import runtime_urls_from
        self.web.add(A + '/1.pdf?signature=SECRETSIG123', PDF_OK)
        runtime = runtime_urls_from(self.store, rid, [self.base / 'inputs' / 'x_main.csv'])
        self.assertEqual(list(runtime.values()), [A + '/1.pdf?signature=SECRETSIG123'])
        self.downloader(rid, runtime_urls=runtime).run()
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')
        self.assertTrue(self.store.verify(rid)['verified'])
        with contextlib.closing(sqlite3.connect(self.ws / 'db' / 'litintel.sqlite')) as connection:
            self.assertFalse(any('SECRETSIG123' in line for line in connection.iterdump()))

    def test_network_errors_pause_host_after_threshold(self):
        self.web.add(A + '/1.pdf', OSError('reset'))
        self.web.add(A + '/2.pdf', OSError('reset'))
        rid = self.make_run([row('W1', A + '/1.pdf'), row('W2', A + '/2.pdf'), row('W3', A + '/3.pdf')], p=policy(host_failure_threshold=1))
        self.download(rid)
        self.assertEqual(self.status(rid)['W1']['status'], 'NETWORK_ERROR')
        self.assertEqual(self.web.requested('/3.pdf'), [])

    def test_policy_freeze_on_resume(self):
        rid = self.make_run([row('W1', A + '/1.pdf')])
        stored = self.store.policy(rid)
        self.assertEqual(stored['max_pdf_bytes'], default_policy()['max_pdf_bytes'])
        with self.assertRaises(FoundationError):
            self.db.execute("UPDATE pdf_run SET policy_json='{}' WHERE run_id=?", (rid,))
        code = self.cli(['download', '--workspace', str(self.ws), '--run-id', rid, '--expected-policy-sha256', '0' * 64, '--quiet'])
        self.assertEqual(code, 2)

    def cli(self, args):
        self.db.close()
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                code = pdf_main(args)
            self.last_output = out.getvalue()
            return code
        finally:
            self.db = Database(self.ws)
            self.store = PdfAcquisitionStore(self.db)


class TestProviderLookups(PdfCase):
    KEY, EMAIL = 'SYNTHETIC_KEY_NOT_REAL_123', 'synthetic-private@example.invalid'

    def lookup_policy(self):
        return policy(evidence_sources_enabled=['INPUT_ROW', 'OPENALEX', 'UNPAYWALL', 'OPENALEX_CONTENT'])

    def test_lookups_require_online_flag(self):
        rid = self.make_run([row('W1')], p=self.lookup_policy(), discover=False)
        with self.assertRaises(ValidationError):
            self.store.discover(rid)

    def test_openalex_and_unpaywall_lookups(self):
        work = dict(id='https://openalex.org/W1', open_access=dict(is_oa=True), has_content=dict(pdf=True),
                    locations=[dict(is_oa=True, pdf_url=B + '/pub.pdf', version='publishedVersion', source=dict(type='journal'))])
        self.web.add('https://api.openalex.org/works/W1', (200, {'Content-Type': 'application/json'}, json.dumps(work).encode()))
        upw = dict(doi='10.9999/w1', best_oa_location=dict(url_for_pdf=C + '/aam.pdf', version='acceptedVersion', host_type='repository'),
                   oa_locations=[])
        self.web.add('https://api.unpaywall.org/v2/10.9999/w1?email=synthetic-private%40example.invalid',
                     (200, {'Content-Type': 'application/json'}, json.dumps(upw).encode()))
        rid = self.make_run([row('W1')], p=self.lookup_policy(), discover=False)
        self.store.discover(rid, self.transport(rid, credentials=(self.KEY, self.EMAIL)), online=True)
        eligible = [c for c in self.store.candidates(rid) if c['eligible']]
        self.assertEqual([c['version'] for c in eligible][:2], ['publishedVersion', 'acceptedVersion'])
        self.assertIn('https://content.openalex.org/works/W1.pdf', [c['url'] for c in eligible])
        openalex_call = next(c for c in self.web.calls if 'api.openalex.org' in c['url'])
        self.assertEqual(openalex_call['headers']['Authorization'], 'Bearer ' + self.KEY)
        unpaywall_call = next(c for c in self.web.calls if 'api.unpaywall.org' in c['url'])
        self.assertNotIn('Authorization', unpaywall_call['headers'])
        with contextlib.closing(sqlite3.connect(self.ws / 'db' / 'litintel.sqlite')) as connection:
            dump = '\n'.join(connection.iterdump())
        self.assertNotIn(self.KEY, dump)
        self.assertNotIn(self.EMAIL, dump)
        self.assertTrue(self.store.verify(rid)['verified'])
        # Without a key the cached OpenAlex Content PDF is never requested.
        self.web.add(B + '/pub.pdf', (404, {}, b'gone'))
        self.web.add(C + '/aam.pdf', (404, {}, b'gone'))
        self.download(rid)
        self.assertEqual(self.web.requested('content.openalex.org'), [])
        self.assertIn('CREDENTIAL_REQUIRED', [c['outcome'] for c in self.status(rid)['W1']['candidates']])

    def test_doi_only_work_is_looked_up_in_openalex_by_exact_doi(self):
        found = dict(id='https://openalex.org/W77', doi='https://doi.org/10.9999/Only', open_access=dict(is_oa=True),
                     locations=[dict(is_oa=True, pdf_url=B + '/only.pdf', version='publishedVersion', source=dict(type='journal'))])
        other = dict(found, id='https://openalex.org/W78', doi='https://doi.org/10.9999/other')
        self.web.add('https://api.openalex.org/works/doi:10.9999/only', (200, {'Content-Type': 'application/json'}, json.dumps(found).encode()))
        self.web.add('https://api.openalex.org/works/doi:10.9999/echo', (200, {'Content-Type': 'application/json'}, json.dumps(other).encode()))
        p = policy(evidence_sources_enabled=['INPUT_ROW', 'OPENALEX'])
        rid = self.make_run([row('', doi='10.9999/only'), row('', doi='10.9999/echo'), row('W5')], p=p, discover=False)
        self.store.discover(rid, self.transport(rid), online=True)
        lookups = sorted(c['url'] for c in self.web.calls if 'api.openalex.org' in c['url'])
        self.assertEqual(lookups, ['https://api.openalex.org/works/W5', 'https://api.openalex.org/works/doi:10.9999/echo',
                                   'https://api.openalex.org/works/doi:10.9999/only'])
        evidence = {e['linkage']: json.loads(e['metadata_json']) for e in self.store._all('SELECT * FROM pdf_evidence WHERE run_id=?', (rid,))}
        self.assertEqual(evidence['doi:10.9999/only']['resolved_openalex_id'], 'W77')
        self.assertEqual(evidence['doi:10.9999/only']['routes'][0]['linkage'], 'https://openalex.org/W77')
        # A response for a different DOI is refused, never used as evidence for this work.
        self.assertEqual(evidence['doi:10.9999/echo']['failure_class'], 'PROVIDER_ID_MISMATCH')
        self.assertEqual(evidence['doi:10.9999/echo']['routes'], [])
        self.assertNotIn('resolved_openalex_id', evidence['doi:10.9999/echo'])
        # Identity stays frozen: the resolved Work ID does not merge works.
        self.assertEqual(len(self.store.works(rid)), 3)
        eligible = {c['url'] for c in self.store.candidates(rid) if c['eligible']}
        self.assertIn(B + '/only.pdf', eligible)
        self.assertTrue(self.store.verify(rid)['verified'])

    def test_temporary_lookup_failure_is_retried_not_frozen(self):
        work = dict(id='https://openalex.org/W1', open_access=dict(is_oa=True),
                    locations=[dict(is_oa=True, pdf_url=B + '/pub.pdf', version='publishedVersion', source=dict(type='journal'))])
        busy = (503, {}, b'busy')
        self.web.add('https://api.openalex.org/works/W1', busy, busy, busy, (200, {'Content-Type': 'application/json'}, json.dumps(work).encode()))
        self.web.add('https://api.openalex.org/works/W2', (404, {}, b'{}'))
        rid = self.make_run([row('W1'), row('W2')], p=policy(evidence_sources_enabled=['INPUT_ROW', 'OPENALEX']), discover=False)
        first = self.store.discover(rid, self.transport(rid), online=True)
        self.assertEqual((first['state'], first['pending_lookup_works']), ('DISCOVERING', 1))
        evidence = [json.loads(e['metadata_json']) for e in self.store._all('SELECT metadata_json FROM pdf_evidence')]
        self.assertEqual([(e['linkage'], e['failure_class']) for e in evidence], [('W2', 'HTTP_404')])
        self.assertEqual(len(self.store.plans(rid)), 1)
        second = self.store.discover(rid, self.transport(rid), online=True)
        self.assertEqual(second['state'], 'ACQUIRING')
        self.assertIn(B + '/pub.pdf', [c['url'] for c in self.store.candidates(rid) if c['eligible']])
        self.assertTrue(self.store.verify(rid)['verified'])

    def test_failed_lookup_can_be_accepted_explicitly(self):
        self.web.add('https://api.openalex.org/works/W1', (503, {}, b'busy'))
        rid = self.make_run([row('W1', A + '/1.pdf')], p=policy(evidence_sources_enabled=['INPUT_ROW', 'OPENALEX']), discover=False)
        result = self.store.discover(rid, self.transport(rid), online=True, accept_failed_lookups=True)
        self.assertEqual(result['state'], 'ACQUIRING')
        meta = json.loads(self.store._one('SELECT metadata_json FROM pdf_evidence WHERE run_id=?', (rid,))['metadata_json'])
        self.assertEqual((meta['failure_class'], meta['routes']), ('HTTP_5XX', []))
        self.assertEqual([c['url'] for c in self.store.candidates(rid)], [A + '/1.pdf'])

    def test_manual_list_falls_back_to_provider_title_doi_and_year(self):
        from reprovane.pdf_acquisition.report import build_manifest, manual_rows
        work = dict(id='https://openalex.org/W9', doi='https://doi.org/10.9999/W9', title='  Provider   Title ',
                    publication_year=2021, open_access=dict(is_oa=True),
                    locations=[dict(is_oa=True, pdf_url=B + '/w9.pdf', version='publishedVersion', source=dict(type='journal'))])
        self.web.add('https://api.openalex.org/works/W9', (200, {'Content-Type': 'application/json'}, json.dumps(work).encode()))
        self.web.add(B + '/w9.pdf', NOT_FOUND)
        p = policy(evidence_sources_enabled=['INPUT_ROW', 'OPENALEX'])
        rid = self.store.create_run(p, label='ids')['run_id']
        self.store.import_identifiers(rid, ['W9', 'W10'])
        self.web.add('https://api.openalex.org/works/W10', (404, {}, b'{}'))
        self.store.discover(rid, self.transport(rid), online=True)
        self.download(rid)
        status = self.status(rid)
        self.assertEqual(status['W9']['titles'], [])
        self.assertEqual(status['W9']['provider_display'], dict(titles=['Provider Title'], dois=['10.9999/w9'], publication_years=[2021]))
        self.assertNotIn('provider_display', status['W10'])
        rows = {r['paper']: r for r in manual_rows(build_manifest(self.store, rid, self.store.host_watermark()))}
        self.assertEqual((rows['W9']['title'], rows['W9']['doi'], rows['W9']['year']), ('Provider Title', '10.9999/w9', '2021'))
        self.assertEqual((rows['W10']['title'], rows['W10']['doi']), ('', ''))
        self.assertTrue(self.store.verify(rid)['verified'])


class TestSharedTransportHooks(unittest.TestCase):
    def fetch(self, responses, **hooks):
        from fulltext_acquisition_fixtures import Opener, public_dns
        from reprovane.fulltext_acquisition.transport import SafeTransport
        self.opener = Opener(responses)
        t = SafeTransport(review_policy(), resolver=public_dns, opener=self.opener, sleeper=lambda _: None)
        return t.fetch('https://papers.example.org/a', purpose='PDF', max_bytes=10 ** 6, reserve=lambda *a: ('id', a[-1]),
                       complete=lambda *a: None, **hooks)

    def test_gate_runs_before_any_connection(self):
        from reprovane.fulltext_acquisition.transport import FetchError

        def gate(url):
            raise FetchError('HOST_BLOCKED', terminal=True)
        with self.assertRaises(FetchError):
            self.fetch([Response()], gate=gate)
        self.assertEqual(self.opener.calls, [])

    def test_inspect_sees_head_and_terminal_stops_retries(self):
        from reprovane.fulltext_acquisition.transport import FetchError
        seen = []

        def inspect(status, headers, head, url):
            seen.append((status, head[:5]))
            if status == 503:
                raise FetchError('SERVER_ERROR', True, True)
        with self.assertRaises(FetchError):
            self.fetch([Response(b'busy', 503), Response()], inspect=inspect)
        self.assertEqual(len(self.opener.calls), 1)
        self.assertEqual(self.fetch([Response()], inspect=inspect)[0], PDF)
        self.assertEqual(seen[-1], (200, b'%PDF-'))

    def test_default_behaviour_unchanged_without_hooks(self):
        body, http = self.fetch([Response(b'x', 500), Response()])
        self.assertEqual((body, http['retry_count']), (PDF, 1))
        self.assertEqual(self.opener.calls[0]['headers']['User-Agent'], 'reprovane-fulltext/1')


class TestQuietPdfCheck(unittest.TestCase):
    def test_pypdf_repair_warnings_are_muted_only_during_the_check(self):
        import logging
        from reprovane.pdf_acquisition import pdfcheck
        logger = logging.getLogger('pypdf')
        seen = []

        def fake(body, bounds):
            seen.append(logger.level)
            logger.warning('Xref table not zero-indexed.')
            raise ValidationError('PDF_VALIDATION_FAILED')
        logger.setLevel(logging.NOTSET)
        with patch.object(pdfcheck, '_stage11b_validate_pdf', fake), self.assertNoLogs('pypdf', level='WARNING'):
            with self.assertRaises(ValidationError):
                pdfcheck.validate_pdf(b'%PDF-', {})
        self.assertEqual((seen, logger.level), ([logging.ERROR], logging.NOTSET))
        self.assertEqual(pdfcheck.validate_pdf(PDF, review_policy())['pages'], 1)


class TestImmutability(PdfCase):
    def test_rows_are_append_only(self):
        self.web.add(A + '/1.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.download(rid)
        self.store.host_event('x.example.org', 'BLOCK', 'operator', origin='OPERATOR')
        for sql in ('DELETE FROM pdf_attempt_result', 'UPDATE pdf_work SET work_key=work_key', 'DELETE FROM pdf_artifact',
                    'DELETE FROM pdf_host_event', "UPDATE pdf_run SET state='OPEN'", 'DELETE FROM pdf_input_record'):
            with self.assertRaises(FoundationError, msg=sql):
                self.db.execute(sql)

    def test_external_connection_cannot_insert(self):
        rid = self.make_run([row('W1')])
        with contextlib.closing(sqlite3.connect(self.ws / 'db' / 'litintel.sqlite')) as c:
            with self.assertRaises(sqlite3.Error):
                c.execute("INSERT INTO pdf_host_event(host,kind,reason,origin,created_at_utc) VALUES('x','BLOCK','r','OPERATOR','t')")

    def test_tampered_artifact_bytes_fail_verification(self):
        self.web.add(A + '/1.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.download(rid)
        path = self.store.cas.path_for_hash(hashlib.sha256(PDF).hexdigest())
        path.write_bytes(PDF2)
        with self.assertRaises(ArtifactIntegrityError):
            self.store.verify(rid)


class TestManualIngestion(PdfCase):
    def setUp(self):
        super().setUp()
        self.rid = self.make_run([row('W1', A + '/1.pdf'), row('W2', doi='10.4242/two'), row('W3'),
                                  dict(title='No identifiers here', best_oa_pdf_url=A + '/r.pdf')])

    def test_exact_identifier_links_and_wrong_identifier_waits(self):
        folder = self.pdf_dir({'W1.pdf': PDF, 'W999.pdf': PDF2, 'Paper W3 final.pdf': PDF2})
        result = manual_import(self.store, run_id=self.rid, path=folder)
        self.assertEqual(result['counts']['linked'], 1)
        self.assertEqual(result['counts']['reconciliation_required'], 2)
        st = self.status(self.rid)
        self.assertEqual(st['W1']['status'], 'MANUAL_IMPORTED')
        self.assertEqual(st['W3']['status'], 'NO_OA_URL')    # a title-like file name is never matched
        self.assertEqual({i['kind'] for i in reconciliation(self.store, run_id=self.rid)}, {'UNMATCHED'})

    def test_legacy_doi_key_and_recursive_unicode_folders(self):
        folder = self.pdf_dir({'thư mục con/doi_10.4242_two.pdf': PDF2, 'top.txt': b'not a pdf'}, name='tải về thủ công')
        flat = manual_import(self.store, run_id=self.rid, path=folder)
        self.assertEqual(flat['counts']['files'], 0)
        deep = manual_import(self.store, run_id=self.rid, path=folder, recursive=True)
        self.assertEqual(deep['counts']['linked'], 1)
        self.assertEqual(self.status(self.rid)['W2']['status'], 'MANUAL_IMPORTED')

    def test_single_file_and_rerun_unchanged(self):
        folder = self.pdf_dir({'W1.pdf': PDF})
        manual_import(self.store, run_id=self.rid, path=folder / 'W1.pdf')
        again = manual_import(self.store, run_id=self.rid, path=folder / 'W1.pdf')
        self.assertEqual(again['counts']['unchanged'], 1)
        self.assertEqual(self.table_count('pdf_manual_import'), 1)

    def test_invalid_pdf_rejected_and_not_stored(self):
        folder = self.pdf_dir({'W1.pdf': b'<html>login</html>' * 20, 'W2.pdf': PDF[:-60]})
        result = manual_import(self.store, run_id=self.rid, path=folder)
        self.assertEqual(result['counts']['rejected'], 2)
        self.assertEqual(self.table_count('pdf_artifact'), 0)

    def test_user_mapping_correct_conflicting_and_unknown(self):
        folder = self.pdf_dir({'a.pdf': PDF, 'b.pdf': PDF2, 'c.pdf': b'%PDF' + PDF[4:]})
        mapping = write_csv(self.base / 'mapping.csv', [dict(file='a.pdf', openalex_id='W1', doi='10.9999/w1'),
                                                         dict(file='b.pdf', openalex_id='W1', doi='10.4242/two'),
                                                         dict(file='c.pdf', doi='10.0000/not-in-run')])
        result = manual_import(self.store, run_id=self.rid, path=folder, mapping=mapping)
        outcomes = {f['source_name']: (f['outcome'], f['reason']) for f in result['files']}
        self.assertEqual(outcomes['a.pdf'], ('LINKED', None))
        self.assertEqual(outcomes['b.pdf'], ('RECONCILIATION_REQUIRED', 'IDENTIFIER_CONFLICT'))
        self.assertEqual(outcomes['c.pdf'][0], 'RECONCILIATION_REQUIRED')

    def test_ambiguous_identifier(self):
        rid = self.make_run([row('W1', doi='10.6666/shared'), row('W2', doi='10.6666/shared')], name='amb.csv')
        folder = self.pdf_dir({'doi_10.6666_shared.pdf': PDF}, name='amb')
        result = manual_import(self.store, run_id=rid, path=folder)
        self.assertEqual(result['files'][0]['reason'], 'AMBIGUOUS')

    def test_same_bytes_for_two_works_requires_decision(self):
        folder = self.pdf_dir({'W1.pdf': PDF, 'W3.pdf': PDF})
        result = manual_import(self.store, run_id=self.rid, path=folder)
        self.assertEqual(sorted(f['outcome'] for f in result['files']), ['LINKED', 'RECONCILIATION_REQUIRED'])
        self.assertEqual(reconciliation(self.store, run_id=self.rid)[0]['kind'], 'ARTIFACT_LINKED_ELSEWHERE')

    def test_reconciliation_decisions_are_immutable(self):
        folder = self.pdf_dir({'mystery.pdf': PDF, 'other.pdf': PDF2})
        manual_import(self.store, run_id=self.rid, path=folder)
        items = {i['source_name']: i for i in reconciliation(self.store, run_id=self.rid)}
        w3 = next(w['work_id'] for w in self.store.works(self.rid) if w['openalex_ids'] == ['W3'])
        decide(self.store, items['mystery.pdf']['item_id'], target_id=w3, decided_by='reviewer_1')
        decide(self.store, items['other.pdf']['item_id'], reject=True)
        self.assertEqual(self.status(self.rid)['W3']['artifacts'][0]['basis'], 'RECONCILIATION_DECISION')
        with self.assertRaises(ValidationError):
            decide(self.store, items['other.pdf']['item_id'], target_id=w3)
        with self.assertRaises(ValidationError):
            decide(self.store, items['mystery.pdf']['item_id'], reject=True, decided_by='someone@example.org')

    def test_manual_pdf_after_failed_download_and_download_skips_it(self):
        self.web.add(A + '/1.pdf', (403, {}, b'denied'))
        self.download(self.rid)
        self.assertEqual(self.status(self.rid)['W1']['status'], 'BOT_CHECK')
        manual_import(self.store, run_id=self.rid, path=self.pdf_dir({'W1.pdf': PDF}))
        self.assertEqual(self.status(self.rid)['W1']['status'], 'MANUAL_IMPORTED')
        before = len(self.web.calls)
        self.download(self.rid, retry_failed=True)
        self.assertEqual(len(self.web.calls), before)
        self.assertTrue(self.store.verify(self.rid)['verified'])

    def test_symlinks_are_not_followed(self):
        folder = self.pdf_dir({'real/W1.pdf': PDF})
        try:
            os.symlink(folder / 'real', folder / 'link', target_is_directory=True)
        except (OSError, NotImplementedError):
            return    # the platform account cannot create symlinks; nothing to follow
        result = manual_import(self.store, run_id=self.rid, path=folder, recursive=True)
        self.assertEqual(result['counts']['files'], 1)

    def test_manual_import_on_closed_run_refused(self):
        self.store.close(self.rid)
        with self.assertRaises(ValidationError):
            manual_import(self.store, run_id=self.rid, path=self.pdf_dir({'W1.pdf': PDF}))


class TestIndependentReviewFindings(PdfCase):
    """Regressions for the independent review of the first implementation (findings F-01 to F-12)."""

    def manual_rows(self, rid):
        from reprovane.pdf_acquisition.report import build_manifest, manual_rows
        return {r['paper']: r for r in manual_rows(build_manifest(self.store, rid, self.store.host_watermark()))}

    def test_f01_reflected_secret_in_a_bot_check_title_is_not_persisted(self):
        marker = 'SYNTHETICKEY42'
        url = 'https://content.openalex.org/works/W1.pdf'
        self.web.add(url, (200, {'Content-Type': 'text/html'}, ('<html><title>Just a moment... ' + marker + '</title></html>').encode()))
        rid = self.make_run([row('W1', url)])
        self.downloader(rid, credentials=(marker, '')).run()
        # The page title is replaced by its known challenge phrase: no remote free text is stored.
        self.assertEqual([r[0] for r in self.db.execute('SELECT reason FROM pdf_host_event')], ["bot-check page 'just a moment' (HTTP 200)"])
        self.assertTrue(self.store.host_state('content.openalex.org')['blocked'])
        with contextlib.closing(sqlite3.connect(self.ws / 'db' / 'litintel.sqlite')) as connection:
            self.assertNotIn(marker, '\n'.join(connection.iterdump()))
        # Second line of defence for any other reason text: a known runtime value withholds the whole reason.
        self.store.host_event('x.example.org', 'BLOCK', 'operator note ' + marker, origin='OPERATOR', private=(marker,))
        self.assertEqual(self.store.host_state('x.example.org')['reason'], 'REASON_WITHHELD_PRIVATE_LOOKING_TEXT')

    def test_f02_artifact_budget_stops_downloads_at_the_cap(self):
        p = policy(global_budget=dict(items=10, http_requests=20, bytes=10 ** 9, artifacts=1, provider_lookups=10))
        rid = self.make_run([row('W1', A + '/1.pdf'), row('W2', A + '/2.pdf')], p=p)
        self.web.add(A + '/1.pdf', PDF_OK)
        self.web.add(A + '/2.pdf', PDF2_OK)
        self.assertTrue(self.download(rid)['budget_exhausted'])
        self.assertEqual((self.store.budget(rid)['artifacts'], self.web.requested('/2.pdf')), (1, []))
        self.assertTrue(self.store.verify(rid)['verified'])

    def test_f02_new_distinct_pdf_is_refused_inside_the_attempt(self):
        p = policy(stop_after_first_success=False,
                   global_budget=dict(items=10, http_requests=20, bytes=10 ** 9, artifacts=1, provider_lookups=10))
        rid = self.make_run([row('W1', A + '/1.pdf', B + '/2.pdf')], p=p)
        self.web.add(A + '/1.pdf', PDF_OK)
        self.web.add(B + '/2.pdf', PDF2_OK)
        self.download(rid)
        self.assertEqual(sorted(a['failure_class'] or a['outcome'] for a in self.store.attempts(rid)), ['ACQUIRED', 'BUDGET_EXHAUSTED'])
        self.assertEqual(self.store.budget(rid)['artifacts'], 1)
        self.assertFalse(self.store.cas.path_for_hash(hashlib.sha256(PDF2).hexdigest()).exists())
        self.assertTrue(self.store.verify(rid)['verified'])

    def test_f03_title_typed_into_the_openalex_column_never_links_a_file(self):
        rid = self.make_run([dict(openalex_id='Some Article', title='Some Article')])
        work = self.store.works(rid)[0]
        self.assertEqual((work['openalex_ids'], work['match_keys'], work['file_key']), ([], [], work['work_id']))
        result = manual_import(self.store, run_id=rid, path=self.pdf_dir({'Some_Article.pdf': PDF}))
        self.assertEqual((result['counts']['linked'], result['counts']['reconciliation_required']), (0, 1))
        item = reconciliation(self.store, run_id=rid)[0]
        self.assertEqual((item['kind'], item['candidates']), ('KEY_NEEDS_CONFIRMATION', [work['work_id']]))
        decide(self.store, item['item_id'], target_id=work['work_id'], decided_by='r1')
        self.assertEqual(self.status(rid)[work['work_id']]['status'], 'MANUAL_IMPORTED')
        self.assertTrue(self.store.verify(rid)['verified'])

    def test_f04_rows_without_identifiers_never_merge_on_title(self):
        rid = self.make_run([dict(title='Editorial', publication_year='2024'), dict(title='Editorial', publication_year='2024')])
        identity = self.store.inspect(rid)['identity']
        self.assertEqual((identity['accepted_records'], identity['unique_works']), (2, 2))
        self.assertEqual([i['kind'] for i in identity['issues']], ['LEGACY_KEY_COLLISION'])
        works = self.store.works(rid)
        self.assertEqual({w['file_key'] for w in works}, {w['work_id'] for w in works})
        manual_import(self.store, run_id=rid, path=self.pdf_dir({works[0]['primary_legacy_key'] + '.pdf': PDF}))
        self.assertEqual(reconciliation(self.store, run_id=rid)[0]['kind'], 'AMBIGUOUS')

    def test_f05_contradictory_or_conflicting_mappings_wait_for_a_decision(self):
        rid = self.make_run([row('W1'), row('W2')])
        folder = self.pdf_dir({'a.pdf': PDF, 'b.pdf': PDF2, 'a2.pdf': PDF})
        mapping = write_csv(self.base / 'mapping.csv', [
            dict(file='a.pdf', sha256='0' * 64, openalex_id='W1'),
            dict(file='b.pdf', openalex_id='W1'), dict(file='b.pdf', openalex_id='W2'),
            dict(file='a2.pdf', sha256=hashlib.sha256(PDF).hexdigest(), openalex_id='W1')])
        result = manual_import(self.store, run_id=rid, path=folder, mapping=mapping)
        self.assertEqual({r['source_name']: (r['outcome'], r['reason']) for r in result['files']},
                         {'a.pdf': ('RECONCILIATION_REQUIRED', 'MAPPING_CONFLICT'),
                          'b.pdf': ('RECONCILIATION_REQUIRED', 'MAPPING_CONFLICT'),
                          'a2.pdf': ('LINKED', None)})

    def test_f06_corrupt_pdf_waiting_in_reconciliation_fails_verification(self):
        rid = self.make_run([row('W1')])
        manual_import(self.store, run_id=rid, path=self.pdf_dir({'mystery.pdf': PDF}))
        sha = hashlib.sha256(PDF).hexdigest()
        from reprovane.pdf_acquisition.report import build_manifest
        manifest = build_manifest(self.store, rid, self.store.host_watermark())
        self.assertEqual(([a['artifact_sha256'] for a in manifest['held_manual_artifacts']], manifest['artifacts']), ([sha], []))
        self.assertEqual(self.store.verify(rid)['checks']['artifacts'], 1)
        self.store.cas.path_for_hash(sha).write_bytes(PDF2)
        with self.assertRaises(ArtifactIntegrityError):
            self.store.verify(rid)
        with self.assertRaises(ArtifactIntegrityError):
            self.store.close(rid)
        self.assertEqual(self.store.get(rid)['state'], 'ACQUIRING')

    def test_f07_prefix_read_obeys_the_request_deadline(self):
        from fulltext_acquisition_fixtures import public_dns
        from reprovane.fulltext_acquisition.transport import FetchError
        from reprovane.pdf_acquisition.engine import make_transport
        clock = [0.0]

        class Slow(Response):
            def stream(self, amt, decode_content=False):
                for i in range(0, len(self.body), 16):
                    clock[0] += 0.25
                    yield self.body[i:i + 16]
        transport = make_transport(policy(request_timeout=1, retry_count=0), resolver=public_dns,
                                   opener=lambda *args: Slow(PDF), sleeper=lambda _: None)
        with patch('reprovane.fulltext_acquisition.transport.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(FetchError) as caught:
                transport.fetch(A + '/slow.pdf', purpose='PDF', max_bytes=10 ** 6, reserve=lambda url, purpose, size: ('t', size),
                                complete=lambda *args: None, inspect=lambda *args: None)
        self.assertEqual(caught.exception.reason, 'REQUEST_TIMEOUT')

    def test_f08_identical_bytes_from_two_candidates_of_one_work_verify(self):
        rid = self.make_run([row('W1', A + '/1.pdf', B + '/mirror.pdf')], p=policy(stop_after_first_success=False))
        self.web.add(A + '/1.pdf', PDF_OK)
        self.web.add(B + '/mirror.pdf', PDF_OK)
        self.download(rid)
        self.assertEqual((sum(a['outcome'] == 'ACQUIRED' for a in self.store.attempts(rid)), len(self.store.links(rid))), (2, 1))
        self.assertEqual(self.store.close(rid)['state'], 'CLOSED')

    def test_f09_short_http_date_retry_after_is_honoured(self):
        from email.utils import format_datetime
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.web.add(A + '/1.pdf', (429, {'Retry-After': format_datetime(self.now + timedelta(seconds=10), usegmt=True)}, b'wait'), PDF_OK)
        web, times = self.web, []

        def tracked(url, *args):
            if url == A + '/1.pdf':
                times.append(self.clock[0])
            return web(url, *args)
        self.web = tracked
        self.download(rid)
        self.web = web
        self.assertEqual(len(times), 2)
        self.assertGreaterEqual(times[1] - times[0], 10)
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')

    def test_f09_crawl_delay_longer_than_policy_defers_the_host(self):
        self.web.add(A + '/robots.txt', (200, {'Content-Type': 'text/plain'}, b'User-agent: *\nCrawl-delay: 120\n'))
        self.web.add(A + '/1.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf')])
        result = self.download(rid)
        self.assertEqual((self.web.requested('/1.pdf'), result['hosts_paused_this_invocation']), ([], {'a.example.org': 'CRAWL_DELAY_TOO_LONG'}))
        self.assertEqual(self.status(rid)['W1']['status'], 'CRAWL_DELAY_TOO_LONG')

    def test_f10_own_exports_reimport_with_identity_and_urls(self):
        rid = self.make_run([row('W1', A + '/1.pdf', doi=''), row('', B + '/2.pdf', doi='10.9999/x2')])
        self.download(rid)
        out = self.base / 'export'
        export(self.store, rid, out)
        for name in ('pdf_items.jsonl', 'manual_download.csv'):
            again = self.store.create_run(label=name)['run_id']
            self.store.import_file(again, out / name)
            self.store.discover(again)
            self.assertEqual(sorted((w['openalex_ids'], w['dois']) for w in self.store.works(again)), [([], ['10.9999/x2']), (['W1'], [])])
            self.assertEqual(sorted(c['url'] for c in self.store.candidates(again)), [A + '/1.pdf', B + '/2.pdf'], name)

    def test_f11_legacy_failures_are_kept_as_history(self):
        rid = self.make_run([row('W1', A + '/1.pdf')])
        source = self.base / 'legacy'
        source.mkdir()
        state = dict(tool_version='4.1.0', papers=dict(W1=dict(status='HTTP_404', detail='synthetic historical failure', url=A + '/1.pdf')))
        (source / 'fetch_state.json').write_text(json.dumps(state), encoding='utf-8')
        before = tree_hashes(source)
        self.assertEqual(import_legacy_fulltext(self.store, rid, source)['history_entries'], 1)
        status = self.status(rid)['W1']
        self.assertEqual((status['status'], [h['status'] for h in status['legacy_v4_history']]), ('NOT_ATTEMPTED', ['HTTP_404']))
        self.assertEqual((self.table_count('pdf_import_session'), tree_hashes(source)), (1, before))
        self.assertIn('4.1.0 HTTP_404', self.manual_rows(rid)['W1']['detail'])
        self.assertTrue(self.store.verify(rid)['verified'])

    def test_f12_manual_rows_need_an_existing_open_context(self):
        from reprovane.corpus_release.model import digest
        rid = self.make_run([row('W1')])
        manual_import(self.store, run_id=rid, path=self.pdf_dir({'mystery.pdf': PDF}))
        session = self.db.execute('SELECT session_id FROM pdf_import_session').fetchone()[0]

        def insert(table, row):
            with self.store.write():
                self.store._insert(table, row)
        new_session = dict(context_kind='DISCOVERY_RUN', kind='MANUAL', source_label='x', session_json='{}',
                           session_sha256=digest({}), created_at_utc='2026-01-01T00:00:00Z')
        insert('pdf_import_session', dict(new_session, session_id='pms_control', context_id=rid))  # control: accepted
        with self.assertRaises(FoundationError):
            insert('pdf_import_session', dict(new_session, session_id='pms_orphan', context_id='pdr_missing'))
        rejected = dict(session_id=session, source_name='late.pdf', source_sha256=None, byte_size=None, outcome='REJECTED',
                        reason='TEST', artifact_sha256=None, detail_json='{}', detail_sha256=digest({}))
        insert('pdf_manual_import', dict(rejected, import_id='pmi_control', ordinal=98))  # control: accepted while open
        self.store.close(rid)
        with self.assertRaises(FoundationError):
            insert('pdf_manual_import', dict(rejected, import_id='pmi_late', ordinal=99))
        self.assertEqual(self.db.execute("SELECT count(*) FROM pdf_manual_import WHERE import_id='pmi_late'").fetchone()[0], 0)


class TestIndependentReviewRound2(PdfCase):
    """Regressions for the second independent review (findings R2-01 to R2-10; R2-04 is in TestReviewBridge)."""

    def test_r2_01_reflected_signed_token_is_never_persisted_or_exported(self):
        from reprovane.pdf_acquisition.engine import runtime_urls_from
        marker = 'SYNTHSIG42'
        url = A + '/1.pdf?signature=' + marker
        rid = self.make_run([row('W1', url)])
        self.web.add(url, (200, {'Content-Type': 'text/html'}, ('<html><title>Just a moment... ' + marker + '</title></html>').encode()))
        self.downloader(rid, runtime_urls=runtime_urls_from(self.store, rid, [self.base / 'inputs' / 'x_main.csv'])).run()
        self.assertEqual([r[0] for r in self.db.execute('SELECT reason FROM pdf_host_event')],
                         ["bot-check page 'just a moment' (HTTP 200)"])
        self.assertNotIn(marker, '\n'.join(self.db.connection.iterdump()))
        out = self.base / 'export'
        export(self.store, rid, out)
        self.assertFalse(any(marker.encode() in p.read_bytes() for p in out.iterdir()))

    def test_r2_02_legacy_history_never_keeps_a_runtime_secret(self):
        marker = 'SYNTHETICKEY42'
        rid = self.make_run([row('W1', A + '/1.pdf')])
        source = self.base / 'legacy'
        source.mkdir()
        state = dict(tool_version='4.1.0', papers=dict(W1=dict(status='BOT_CHECK', detail='bot-check page ' + marker,
                                                               host='a.example.org', url=A + '/1.pdf')))
        (source / 'fetch_state.json').write_text(json.dumps(state), encoding='utf-8')
        with patch('reprovane.pdf_acquisition.legacy.runtime_private', return_value=(marker,)):
            import_legacy_fulltext(self.store, rid, source)
        session = self.db.execute('SELECT session_json FROM pdf_import_session').fetchone()[0]
        self.assertNotIn(marker, session)
        self.assertNotIn(marker, json.dumps(self.store.manifest(rid, self.store.host_watermark())))
        history = self.status(rid)['W1']['legacy_v4_history']
        self.assertEqual([(h['status'], h['detail'], h['host']) for h in history],
                         [('BOT_CHECK', 'REDACTED_PRIVATE_LOOKING_TEXT', 'a.example.org')])

    def test_r2_03_key_shape_never_proves_identifier_provenance(self):
        cases = [dict(openalex_id='doi_10.9999_wrong', doi='10.9999/right', title='Right paper'),
                 dict(openalex_id='row_012345abcdef', title='No identifier')]
        for index, record in enumerate(cases):
            rid = self.make_run([record], name=f'bad-key-{index}.csv')
            work = self.store.works(rid)[0]
            key = work['primary_legacy_key']
            self.assertNotIn(key, work['match_keys'])
            result = manual_import(self.store, run_id=rid, path=self.pdf_dir({key + '.pdf': PDF}, name=f'manual-{index}'))
            self.assertEqual((result['counts']['linked'], result['counts']['reconciliation_required']), (0, 1), key)
        # A genuine 4.1.0 row hash of a row without identifiers is still trusted.
        rid = self.make_run([dict(title='No identifier at all', publication_year='2020')], name='genuine.csv')
        work = self.store.works(rid)[0]
        self.assertIn(work['primary_legacy_key'], work['match_keys'])

    def test_r2_05_two_decisions_for_identical_bytes_verify(self):
        rid = self.make_run([row('W1')])
        manual_import(self.store, run_id=rid, path=self.pdf_dir({'mystery-a.pdf': PDF, 'mystery-b.pdf': PDF}))
        target = self.store.works(rid)[0]['work_id']
        items = reconciliation(self.store, run_id=rid)
        for item in items:
            decide(self.store, item['item_id'], target_id=target, decided_by='r1')
        self.assertEqual((len(items), len(self.store.links(rid))), (2, 1))
        self.assertEqual(self.store.close(rid)['state'], 'CLOSED')

    def test_r2_06_robots_retry_after_is_waited_for(self):
        from email.utils import format_datetime
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.web.add(A + '/robots.txt', (503, {'Retry-After': format_datetime(self.now + timedelta(seconds=10), usegmt=True)}, b'busy'),
                     (200, {'Content-Type': 'text/plain'}, b'User-agent: *\nAllow: /\n'))
        self.web.add(A + '/1.pdf', PDF_OK)
        web, times = self.web, []

        def tracked(url, *args):
            if url == A + '/robots.txt':
                times.append(self.clock[0])
            return web(url, *args)
        self.web = tracked
        self.download(rid)
        self.web = web
        self.assertGreaterEqual(times[1] - times[0], 10)
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')

    def test_r2_06_retry_after_beyond_the_cooldown_limit_blocks_instead_of_shortening(self):
        url = B + '/long.pdf'
        rid = self.make_run([row('W2', url)], p=policy(max_cooldown_seconds=5))
        self.web.add(url, (429, {'Retry-After': '120'}, b'busy'), PDF_OK)
        self.download(rid)
        state = self.store.host_state('b.example.org')
        self.assertTrue(state['blocked'])
        self.assertIn('exceeds max_cooldown_seconds', state['reason'])
        self.now += timedelta(seconds=6)
        self.download(rid)
        self.assertEqual(len(self.web.requested('/long.pdf')), 1)

    def test_r2_07_reimport_keeps_signed_identity_and_oa_declaration(self):
        from reprovane.pdf_acquisition.engine import runtime_urls_from
        rid = self.make_run([row('W1', A + '/signed.pdf?signature=SYNTHSIG42')])
        first = self.store.candidates(rid)[0]
        out = self.base / 'export'
        export(self.store, rid, out)
        again = self.store.create_run()['run_id']
        self.store.import_file(again, out / 'pdf_items.jsonl')
        self.store.discover(again)
        second = self.store.candidates(again)[0]
        self.assertEqual((second['url_identity_sha256'], second['runtime_url_required']), (first['url_identity_sha256'], True))
        self.assertEqual(len(runtime_urls_from(self.store, again, [self.base / 'inputs' / 'x_main.csv'])), 1)
        strict = policy(oa_evidence_policy='EXPLICIT_OPEN_REQUIRED')
        plain = self.make_run([row('W3', A + '/plain.pdf')], p=strict, name='plain.csv')
        export(self.store, plain, self.base / 'export-plain')
        reimported = self.store.create_run(strict)['run_id']
        self.store.import_file(reimported, self.base / 'export-plain' / 'pdf_items.jsonl')
        self.store.discover(reimported)
        self.assertEqual(sum(c['eligible'] for c in self.store.candidates(reimported)), 1)

    def test_r2_07_redacted_url_in_a_csv_view_is_never_requested(self):
        rid = self.make_run([row('W1', A + '/signed.pdf?signature=SYNTHSIG42')])
        self.download(rid)
        out = self.base / 'export'
        export(self.store, rid, out)
        again = self.store.create_run()['run_id']
        self.store.import_file(again, out / 'manual_download.csv')
        self.store.discover(again)
        self.assertTrue(all(c['runtime_url_required'] for c in self.store.candidates(again) if 'REDACTED' in c['url']))
        self.download(again)
        self.assertFalse(any('REDACTED' in u for u in self.web.requested()))

    def test_r2_08_runtime_urls_are_found_inside_openalex_work_json(self):
        from reprovane.pdf_acquisition.engine import runtime_urls_from
        url = A + '/nested.pdf?signature=SYNTHSIG42'
        work = dict(id='https://openalex.org/W1', title='Synthetic', open_access=dict(is_oa=True),
                    best_oa_location=dict(is_oa=True, pdf_url=url, version='publishedVersion', source=dict(type='journal')))
        source = self.base / 'work.jsonl'
        source.write_text(json.dumps(work) + '\n', encoding='utf-8')
        rid = self.store.create_run()['run_id']
        self.store.import_file(rid, source)
        self.store.discover(rid)
        self.assertEqual(list(runtime_urls_from(self.store, rid, [source]).values()), [url])

    def test_r2_09_bytes_read_before_a_prefix_timeout_are_charged(self):
        from fulltext_acquisition_fixtures import public_dns
        from reprovane.fulltext_acquisition.transport import FetchError
        from reprovane.pdf_acquisition.engine import make_transport
        clock, consumed, completed = [0.0], [0], []

        class Slow(Response):
            def stream(self, amt, decode_content=False):
                for i in range(0, len(self.body), 16):
                    clock[0] += 0.25
                    consumed[0] += len(self.body[i:i + 16])
                    yield self.body[i:i + 16]
        transport = make_transport(policy(request_timeout=1, retry_count=0), resolver=public_dns,
                                   opener=lambda *args: Slow(PDF), sleeper=lambda _: None)
        with patch('reprovane.fulltext_acquisition.transport.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(FetchError):
                transport.fetch(A + '/slow.pdf', purpose='PDF', max_bytes=10 ** 6, reserve=lambda url, purpose, size: ('t', size),
                                complete=lambda ticket, result: completed.append(result), inspect=lambda *args: None)
        self.assertEqual(sum(r['bytes'] for r in completed), consumed[0])

    def test_r2_10_temporary_dns_failure_leaves_the_lookup_pending(self):
        from reprovane.pdf_acquisition.engine import make_transport
        rid = self.make_run([row('W1')], p=policy(evidence_sources_enabled=['INPUT_ROW', 'OPENALEX']), discover=False)

        def dns_down(*args, **kwargs):
            raise OSError('synthetic temporary DNS outage')
        down = make_transport(self.store.policy(rid), resolver=dns_down, opener=self.web, sleeper=lambda _: None)
        self.assertEqual(self.store.discover(rid, down, online=True)['state'], 'DISCOVERING')
        work = dict(id='https://openalex.org/W1', open_access=dict(is_oa=True),
                    best_oa_location=dict(is_oa=True, pdf_url=A + '/recovered.pdf', version='publishedVersion'))
        self.web.add('https://api.openalex.org/works/W1', (200, {'Content-Type': 'application/json'}, json.dumps(work).encode()))
        self.assertEqual(self.store.discover(rid, self.transport(rid), online=True)['state'], 'ACQUIRING')
        self.assertIn(A + '/recovered.pdf', [c['url'] for c in self.store.candidates(rid)])

    def test_r2_10_private_address_is_still_refused_permanently(self):
        from reprovane.fulltext_acquisition.transport import FetchError
        from reprovane.pdf_acquisition.engine import make_transport
        transport = make_transport(policy(), resolver=lambda *a, **k: [(2, 1, 6, '', ('10.0.0.5', 443))], opener=self.web,
                                   sleeper=lambda _: None)
        with self.assertRaises(FetchError) as caught:
            transport.fetch(A + '/x.pdf', purpose='PDF', max_bytes=10 ** 6, reserve=lambda *a: ('t', a[-1]),
                            complete=lambda *a: None, gate=lambda url: None)
        self.assertEqual((caught.exception.reason, caught.exception.retryable), ('UNSAFE_URL', False))


class TestIndependentReviewRound3(PdfCase):
    """Regressions for the third independent review (findings R3-01 to R3-10)."""

    def test_r3_01_redirected_or_short_tokens_never_reach_the_ledger(self):
        from reprovane.pdf_acquisition.engine import runtime_urls_from
        reflected, short = 'SYNTHREDIRECT42', 'abC12'
        start, target = A + '/start.pdf?signature=SYNTHORIGINAL42', A + '/final.pdf?signature=' + reflected
        rid = self.make_run([row('W1', start), row('W2', B + '/short.pdf?token=' + short)])
        self.web.add(start, (302, {'Location': target}, b''))
        self.web.add(target, (200, {'Content-Type': 'text/html; note=' + reflected}, ('<title>Just a moment... ' + reflected + '</title>').encode()))
        self.web.add(B + '/short.pdf?token=' + short, (200, {'Content-Type': 'text/html; note=' + short}, b'<title>Just a moment</title>'))
        self.downloader(rid, runtime_urls=runtime_urls_from(self.store, rid, [self.base / 'inputs' / 'x_main.csv'])).run()
        dump = '\n'.join(self.db.connection.iterdump())
        self.assertNotIn(reflected, dump)
        self.assertNotIn('note=' + short, dump)
        headers = [json.loads(r[0])['headers'] for r in self.db.execute('SELECT result_json FROM pdf_request_result')]
        self.assertIn('text/html', {h.get('content-type') for h in headers})   # media type kept, parameters dropped

    def test_r3_02_legacy_status_and_blocked_host_url_are_guarded(self):
        marker = 'SYNTHETICKEY42'
        rid = self.make_run([row('W1', A + '/1.pdf')])
        source = self.base / 'legacy'
        source.mkdir()
        (source / 'fetch_state.json').write_text(json.dumps(dict(tool_version='4.1.0', papers=dict(
            W1=dict(status=marker, detail='ordinary failure', url=A + '/1.pdf')))), encoding='utf-8')
        (source / 'blocked_hosts.json').write_text(json.dumps({'a.example.org': dict(
            reason='ordinary block', url=A + '/notice/' + marker, http_status=403)}), encoding='utf-8')
        before = tree_hashes(source)
        with patch('reprovane.pdf_acquisition.legacy.runtime_private', return_value=(marker,)):
            import_legacy_fulltext(self.store, rid, source)
        self.assertNotIn(marker, '\n'.join(self.db.connection.iterdump()))
        self.assertEqual(self.status(rid)['W1']['legacy_v4_history'][0]['status'], 'UNRECOGNIZED')
        self.assertTrue(self.store.host_state('a.example.org')['blocked'])
        self.assertEqual(tree_hashes(source), before)

    def test_r3_03_fetch_state_without_a_checksum_never_uses_an_untrusted_key(self):
        rid = self.make_run([dict(openalex_id='not_a_work', title='Synthetic paper without a valid identifier')])
        key = self.store.works(rid)[0]['primary_legacy_key']
        source = self.pdf_dir({'pdf/unrelated.pdf': PDF}, name='legacy-no-checksum')
        (source / 'fetch_state.json').write_text(json.dumps(dict(tool_version='4.1.0', papers={
            key: dict(status='DOWNLOADED', file='pdf/unrelated.pdf')})), encoding='utf-8')
        result = import_legacy_fulltext(self.store, rid, source)
        self.assertEqual((result['counts']['linked'], result['counts']['reconciliation_required']), (0, 1))
        self.assertEqual(reconciliation(self.store, run_id=rid)[0]['kind'], 'KEY_NEEDS_CONFIRMATION')
        # With the recorded checksum the 4.1.0 replay keeps its authority and its label is truthful.
        other = self.make_run([dict(openalex_id='not_a_work', title='Synthetic paper without a valid identifier')], name='y.csv')
        okey = self.store.works(other)[0]['primary_legacy_key']
        verified = self.pdf_dir({'pdf/unrelated.pdf': PDF2}, name='legacy-with-checksum')
        (verified / 'fetch_state.json').write_text(json.dumps(dict(tool_version='4.1.0', papers={okey: dict(
            status='DOWNLOADED', file='pdf/unrelated.pdf', sha256=hashlib.sha256(PDF2).hexdigest())})), encoding='utf-8')
        self.assertEqual(import_legacy_fulltext(self.store, other, verified)['counts']['linked'], 1)
        self.assertEqual([l['verification'] for l in self.store.links(other)], ['LEGACY_V4_FETCH_STATE_SHA_MATCHED'])

    def test_r3_04_ordinary_query_parameters_stay_usable_with_signed_urls(self):
        from reprovane.pdf_acquisition.engine import query_values, runtime_urls_from
        url = A + '/signed.pdf?format=application%2Fpdf&signature=SYNTHSIG42'
        self.assertEqual(query_values([url]), ('SYNTHSIG42',))
        rid = self.make_run([row('W1', url)])
        self.web.add(url, PDF_OK)
        self.downloader(rid, runtime_urls=runtime_urls_from(self.store, rid, [self.base / 'inputs' / 'x_main.csv'])).run()
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')

    def test_r3_05_provider_lookups_honour_retry_after(self):
        from email.utils import format_datetime
        for index, header in enumerate((format_datetime(self.now + timedelta(seconds=10), usegmt=True), '120')):
            wid = f'W{index + 1}'
            rid = self.make_run([row(wid)], p=policy(evidence_sources_enabled=['INPUT_ROW', 'OPENALEX'], retry_count=1),
                                name=f'provider-{index}.csv', discover=False)
            url = 'https://api.openalex.org/works/' + wid
            body = json.dumps(dict(id='https://openalex.org/' + wid, open_access=dict(is_oa=True))).encode()
            self.web.add(url, (503, {'Retry-After': header}, b'busy'), (200, {'Content-Type': 'application/json'}, body))
            web, times = self.web, []

            def tracked(target, *args):
                if target == url:
                    times.append(self.clock[0])
                return web(target, *args)
            self.web = tracked
            result = self.store.discover(rid, self.transport(rid), online=True, clock=lambda: self.clock[0], sleep=self.sleep,
                                         now=lambda: self.now)
            self.web = web
            if index == 0:
                self.assertEqual((len(times), result['state']), (2, 'ACQUIRING'))
                self.assertGreaterEqual(times[1] - times[0], 10)
            else:
                # Longer than this command may wait: one request, lookup left pending, never retried early.
                self.assertEqual((len(times), result['state']), (1, 'DISCOVERING'))

    def test_r3_06_short_retry_after_survives_a_new_command(self):
        url = A + '/resume.pdf'
        rid = self.make_run([row('W1', url)], p=policy(retry_count=0))
        self.web.add(url, (429, {'Retry-After': '10'}, b'busy'), PDF_OK)
        web, times = self.web, []

        def tracked(target, *args):
            if target == url:
                times.append(self.clock[0])
            return web(target, *args)
        self.web = tracked
        self.downloader(rid).run()
        self.assertEqual(self.store.host_state('a.example.org')['cooldown_until'], '2026-01-01T00:00:10.000000Z')
        self.clock[0] += 1
        self.now += timedelta(seconds=1)
        self.downloader(rid).run()
        self.web = web
        self.assertEqual(len(times), 2)
        self.assertGreaterEqual(times[1] - times[0], 10)
        self.assertEqual(self.status(rid)['W1']['status'], 'DOWNLOADED')

    def test_r3_07_and_r3_08_earlier_exports_and_url_only_rows_reimport(self):
        legacy_row = dict(work_id='pw_old', paper='W1', status='NOT_ATTEMPTED', openalex_ids=['W1'], dois=[],
                          candidates=[dict(url=A + '/old.pdf', outcome='NOT_ATTEMPTED')])
        old = self.base / 'old_pdf_items.jsonl'
        old.write_text(json.dumps(legacy_row) + '\n', encoding='utf-8')
        rid = self.store.create_run()['run_id']
        self.store.import_file(rid, old)
        self.store.discover(rid)
        self.assertEqual([c['url'] for c in self.store.candidates(rid)], [A + '/old.pdf'])
        anonymous = self.make_run([dict(best_oa_pdf_url=A + '/anonymous.pdf')], name='anonymous.csv')
        export(self.store, anonymous, self.base / 'export-anon')
        again = self.store.create_run()['run_id']
        self.store.import_file(again, self.base / 'export-anon' / 'pdf_items.jsonl')
        self.assertEqual([r['status'] for r in self.store.records(again)], ['ACCEPTED'])
        self.store.discover(again)
        self.assertEqual([c['url'] for c in self.store.candidates(again)], [A + '/anonymous.pdf'])

    def test_r3_09_open_flag_of_a_landing_page_never_makes_a_direct_pdf_open(self):
        from reprovane.pdf_acquisition.store import _oa_evidence
        candidate = dict(routes=[dict(url_type='DIRECT_PDF', oa_evidence='FALSE'), dict(url_type='LANDING_PAGE', oa_evidence='TRUE')])
        self.assertEqual(_oa_evidence(candidate), 'FALSE')

    def test_r3_10_byte_projections_must_agree(self):
        from reprovane.core.provenance import canonical_json
        from reprovane.corpus_release.model import digest
        rid = self.make_run([row('W1', A + '/1.pdf')])
        request = dict(url=A + '/1.pdf', purpose='PDF', reserved_bytes=1000, lookup_identity='synthetic')

        def insert(table, values):
            with self.store.write():
                self.store._insert(table, values)
        for request_id in ('pdq_a', 'pdq_b', 'pdq_c'):
            insert('pdf_request', dict(run_id=rid, request_id=request_id, purpose='PDF', reserved_bytes=1000,
                                       request_json=canonical_json(request), request_sha256=digest(request),
                                       created_at_utc='2026-01-01T00:00:00Z'))

        def result(request_id, charged, declared):
            body = dict(bytes=declared)
            insert('pdf_request_result', dict(run_id=rid, request_id=request_id, bytes=charged, result_json=canonical_json(body),
                                              result_sha256=digest(body), finished_at_utc='2026-01-01T00:00:01Z'))
        result('pdq_a', 500, 500)                                       # control: consistent projection accepted
        with self.assertRaises(FoundationError):
            result('pdq_b', 0, 500)                                     # charged bytes differ from the hashed result
        with self.assertRaises(FoundationError):
            result('pdq_c', 2000, 2000)                                 # more than the reservation


class TestIndependentReviewRound4(PdfCase):
    """Regressions for the fourth independent review (findings R4-01 to R4-05)."""

    def coherent_sleep(self):
        """Advance the synthetic UTC clock together with the monotonic one."""
        original = self.sleep

        def sleep(seconds):
            original(seconds)
            self.now += timedelta(seconds=seconds)
        self.sleep = sleep

    def test_r4_01_header_values_never_repeat_a_sensitive_value_of_any_hop(self):
        from reprovane.fulltext_acquisition.transport import retained_headers
        from reprovane.pdf_acquisition.engine import runtime_urls_from
        for index, marker in enumerate(('synthredirect42', 'abc', '123')):
            start = A + f'/start{index}.pdf?signature=' + ('SYNTHORIGINAL42' if index == 0 else marker)
            target = A + '/final.pdf?signature=' + marker if index == 0 else start
            name = f'header-{index}.csv'
            rid = self.make_run([row(f'W{index + 1}', start)], name=name)
            if start != target:
                self.web.add(start, (302, {'Location': target}, b''))
            headers = {'Content-Type': 'application/' + marker} if index != 2 else {'Content-Type': 'application/pdf', 'Retry-After': marker}
            self.web.add(target, (200, headers, PDF))
            self.downloader(rid, runtime_urls=runtime_urls_from(self.store, rid, [self.base / 'inputs' / name])).run()
            results = [r[0] for r in self.db.execute('SELECT result_json FROM pdf_request_result WHERE run_id=?', (rid,))]
            self.assertFalse(any(marker in s for s in results), marker)
            self.assertTrue(self.store.links(rid))
        self.assertEqual(retained_headers({'content-type': 'application/pdf; x=1', 'retry-after': '20'}, {'abc'}),
                         {'content-type': 'application/pdf', 'retry-after': '20'})

    def test_r4_02_provider_deadlines_govern_later_works_and_commands(self):
        self.coherent_sleep()
        p = policy(evidence_sources_enabled=['INPUT_ROW', 'OPENALEX'], retry_count=0)
        rid = self.make_run([row('W1'), row('W2')], p=p, name='provider-long.csv', discover=False)

        def success(wid):
            return (200, {'Content-Type': 'application/json'}, json.dumps(dict(id='https://openalex.org/' + wid)).encode())
        self.web.add('https://api.openalex.org/works/W1', (503, {'Retry-After': '120'}, b'busy'), success('W1'))
        self.web.add('https://api.openalex.org/works/W2', success('W2'))

        def discover(run):
            return self.store.discover(run, self.transport(run), online=True, clock=lambda: self.clock[0], sleep=self.sleep,
                                       now=lambda: self.now)
        self.assertEqual(discover(rid)['pending_lookup_works'], 2)
        self.assertEqual(self.web.requested('api.openalex.org'), ['https://api.openalex.org/works/W1'])
        self.assertIsNotNone(self.store.host_state('api.openalex.org')['cooldown_until'])
        self.sleep(1)
        discover(rid)                                     # still inside the recorded deadline: no request at all
        self.assertEqual(len(self.web.requested('api.openalex.org')), 1)
        self.sleep(120)
        self.assertEqual(discover(rid)['state'], 'ACQUIRING')
        self.assertEqual(len(self.web.requested('api.openalex.org')), 3)

    def test_r4_02_short_provider_deadline_survives_a_new_command(self):
        self.coherent_sleep()
        p = policy(evidence_sources_enabled=['INPUT_ROW', 'OPENALEX'], retry_count=0)
        rid = self.make_run([row('W3')], p=p, discover=False)
        url = 'https://api.openalex.org/works/W3'
        self.web.add(url, (429, {'Retry-After': '10'}, b'busy'),
                     (200, {'Content-Type': 'application/json'}, json.dumps(dict(id='https://openalex.org/W3')).encode()))
        web, times = self.web, []

        def tracked(target, *args):
            if target == url:
                times.append(self.clock[0])
            return web(target, *args)
        self.web = tracked
        for _ in range(2):
            self.store.discover(rid, self.transport(rid), online=True, clock=lambda: self.clock[0], sleep=self.sleep,
                                now=lambda: self.now)
            self.sleep(1)
        self.web = web
        self.assertEqual(len(times), 2)
        self.assertGreaterEqual(times[1] - times[0], 10)

    def test_r4_03_robots_redirect_hops_respect_the_responding_host(self):
        from reprovane.pdf_acquisition.politeness import utc_iso
        from pdf_acquisition_fixtures import ROBOTS_OK
        self.coherent_sleep()
        until = self.now + timedelta(seconds=60)
        self.store.host_event('b.example.org', 'COOLDOWN', 'synthetic existing Retry-After', until_utc=utc_iso(until))
        self.web.add(A + '/robots.txt', (302, {'Location': B + '/rules'}, b''))
        self.web.add(B + '/rules', ROBOTS_OK)
        self.web.add(A + '/paper.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/paper.pdf')], p=policy(retry_count=0))
        self.downloader(rid).run()
        self.assertEqual(self.web.requested('b.example.org'), [])        # never contacted during its cooldown
        C, D = 'https://c.example.org', 'https://d.example.org'
        self.web.add(C + '/robots.txt', (302, {'Location': D + '/rules'}, b''))
        self.web.add(D + '/rules', (429, {'Retry-After': '10'}, b'busy'))
        self.web.add(D + '/paper.pdf', PDF_OK)
        web, calls = self.web, []

        def tracked(url, *args):
            calls.append((url, self.clock[0]))
            return web(url, *args)
        self.web = tracked
        other = self.make_run([row('W2', C + '/paper.pdf'), row('W3', D + '/paper.pdf')], p=policy(retry_count=0), name='redirect.csv')
        self.downloader(other).run()
        self.web = web
        self.assertIsNone(self.store.host_state('c.example.org')['cooldown_until'])
        self.assertIsNotNone(self.store.host_state('d.example.org')['cooldown_until'])
        directive = next(t for u, t in calls if u == D + '/rules')
        later = [t for u, t in calls if u.startswith(D) and t > directive]
        self.assertTrue(later and min(later) - directive >= 10)

    def test_r4_04_direct_pdf_route_survives_when_the_same_url_is_a_landing_page(self):
        strict = policy(oa_evidence_policy='EXPLICIT_OPEN_REQUIRED')
        rid = self.make_run([row('W1', A + '/same.pdf', landing=A + '/same.pdf')], p=strict)
        self.assertEqual(sum(c['eligible'] for c in self.store.candidates(rid)), 1)
        export(self.store, rid, self.base / 'export')
        again = self.store.create_run(strict)['run_id']
        self.store.import_file(again, self.base / 'export' / 'pdf_items.jsonl')
        self.store.discover(again)
        self.assertEqual(sum(c['eligible'] for c in self.store.candidates(again)), 1)

    def test_r4_05_byte_projections_must_be_json_integers(self):
        from reprovane.core.provenance import canonical_json
        from reprovane.corpus_release.model import digest
        rid = self.make_run([row('W1', A + '/1.pdf')])
        request = dict(url=A + '/1.pdf', purpose='PDF', reserved_bytes=1000, lookup_identity='synthetic')

        def insert(table, values):
            with self.store.write():
                self.store._insert(table, values)
        for index, declared in enumerate((True, False, 1.0, 1)):
            request_id = f'pdq_{index}'
            insert('pdf_request', dict(run_id=rid, request_id=request_id, purpose='PDF', reserved_bytes=1000,
                                       request_json=canonical_json(request), request_sha256=digest(request),
                                       created_at_utc='2026-01-01T00:00:00Z'))
            body = dict(bytes=declared)
            values = dict(run_id=rid, request_id=request_id, bytes=int(declared), result_json=canonical_json(body),
                          result_sha256=digest(body), finished_at_utc='2026-01-01T00:00:01Z')
            if type(declared) is int:
                insert('pdf_request_result', values)                     # control
            else:
                with self.assertRaises(FoundationError, msg=repr(declared)):
                    insert('pdf_request_result', values)


class TestIndependentReviewRound5(PdfCase):
    """Regressions for the fifth independent review (findings R5-01 and R5-02)."""

    def test_r5_01_a_retry_after_that_repeats_a_private_value_is_never_echoed(self):
        from reprovane.pdf_acquisition.engine import runtime_urls_from
        cases = [('https://redirect-delay.example.org/start.pdf?signature=SYNTHORIGINAL42',
                  'https://redirect-delay.example.org/final.pdf?signature=87654321', '87654321'),
                 ('https://short-delay.example.org/start.pdf?signature=123', None, '123')]
        for index, (start, target, marker) in enumerate(cases):
            name = f'delay-{index}.csv'
            rid = self.make_run([row(f'W{index + 1}', start)], name=name)
            if target:
                self.web.add(start, (302, {'Location': target}, b''))
            self.web.add(target or start, (503, {'Retry-After': marker}, b'busy'))
            self.downloader(rid, runtime_urls=runtime_urls_from(self.store, rid, [self.base / 'inputs' / name])).run()
            host = start.split('/')[2]
            state = self.store.host_state(host)
            self.assertEqual((state['blocked'], state['reason']), (True, 'HTTP 503 Retry-After withheld (repeats a private value)'))
            # Every sink that holds response-derived text (a short marker also occurs by chance inside hashes).
            sinks = [r[0] or '' for r in self.db.execute('SELECT reason FROM pdf_host_event UNION ALL SELECT url FROM pdf_host_event')]
            sinks += [json.dumps(json.loads(r[0])['headers']) for r in self.db.execute('SELECT result_json FROM pdf_request_result')]
            self.assertFalse(any(marker in s for s in sinks), marker)
            out = self.base / f'export-{index}'
            export(self.store, rid, out)
            manifest = json.loads((out / 'pdf_manifest.json').read_text(encoding='utf-8'))
            self.assertNotIn(marker, json.dumps(manifest['host_states']))

    def test_r5_01_ordinary_retry_after_reasons_carry_no_number(self):
        rid = self.make_run([row('W1', A + '/1.pdf')], p=policy(retry_count=0))
        self.web.add(A + '/1.pdf', (429, {'Retry-After': '120'}, b'busy'))
        self.download(rid)
        state = self.store.host_state('a.example.org')
        self.assertEqual((state['reason'], state['cooldown_until']), (None, '2026-01-01T00:02:00.000000Z'))
        reasons = [r[0] for r in self.db.execute("SELECT reason FROM pdf_host_event WHERE host='a.example.org'")]
        self.assertEqual(reasons, ['HTTP 429 Retry-After'])

    def test_r5_02_an_absurd_provider_retry_after_blocks_instead_of_crashing(self):
        rid = self.make_run([row('W1')], p=policy(evidence_sources_enabled=['INPUT_ROW', 'OPENALEX'], retry_count=0), discover=False)
        self.web.add('https://api.openalex.org/works/W1', (503, {'Retry-After': '999999999999'}, b'busy'))
        result = self.store.discover(rid, self.transport(rid), online=True, clock=lambda: self.clock[0], sleep=self.sleep,
                                     now=lambda: self.now)
        self.assertEqual((result['state'], result['pending_lookup_works']), ('DISCOVERING', 1))
        state = self.store.host_state('api.openalex.org')
        self.assertEqual((state['blocked'], state['reason']), (True, 'HTTP 503 Retry-After exceeds max_cooldown_seconds (provider API)'))
        again = self.store.discover(rid, self.transport(rid), online=True, clock=lambda: self.clock[0], sleep=self.sleep, now=lambda: self.now)
        self.assertEqual(len(self.web.requested('api.openalex.org')), 1)       # blocked host: never asked again early
        # The lookup stays pending (no failure frozen as evidence) until an operator unblocks the provider host.
        self.assertEqual((again['state'], again['pending_lookup_works']), ('DISCOVERING', 1))
        self.assertEqual(self.table_count('pdf_evidence'), 0)
        self.store.host_event('api.openalex.org', 'UNBLOCK', 'operator decision', origin='OPERATOR')
        self.web.add('https://api.openalex.org/works/W1', (200, {'Content-Type': 'application/json'},
                                                           json.dumps(dict(id='https://openalex.org/W1')).encode()))
        final = self.store.discover(rid, self.transport(rid), online=True, clock=lambda: self.clock[0], sleep=self.sleep, now=lambda: self.now)
        self.assertEqual(final['state'], 'ACQUIRING')

    def test_r5_02_an_absurd_pdf_retry_after_blocks_the_host(self):
        rid = self.make_run([row('W1', A + '/1.pdf')], p=policy(retry_count=0))
        self.web.add(A + '/1.pdf', (429, {'Retry-After': '9' * 30}, b'busy'))
        self.download(rid)
        self.assertTrue(self.store.host_state('a.example.org')['blocked'])


class TestLegacyCompatibility(PdfCase):
    def legacy_folder(self):
        root = self.base / 'x_main_fulltext'
        (root / 'pdf').mkdir(parents=True)
        (root / 'pdf_manual').mkdir()
        (root / 'pdf' / 'W1.pdf').write_bytes(PDF)
        (root / 'pdf' / 'W4.pdf').write_bytes(PDF2)
        (root / 'pdf_manual' / 'W2.pdf').write_bytes(PDF2)
        state = dict(tool_version='4.1.0', papers=dict(
            W1=dict(status='DOWNLOADED', file='pdf/W1.pdf', sha256=hashlib.sha256(PDF).hexdigest(), url=A + '/1.pdf?token=leak', host='a.example.org'),
            W4=dict(status='DOWNLOADED', file='pdf/W4.pdf', sha256='0' * 64, url=A + '/4.pdf'),
            W3=dict(status='BOT_CHECK', detail='x')))
        (root / 'fetch_state.json').write_text(json.dumps(state), encoding='utf-8')
        (root / 'blocked_hosts.json').write_text(json.dumps({'blocked.example.org': dict(reason='HTTP 403 Forbidden', http_status=403)}), encoding='utf-8')
        (root / 'fetch_log.csv').write_text('time,paper\n', encoding='utf-8')
        return root

    def test_legacy_fetch_oa_folder_import_is_read_only(self):
        rid = self.make_run([row('W1'), row('W2'), row('W3'), row('W4')])
        root = self.legacy_folder()
        before = tree_hashes(root)
        result = import_legacy_fulltext(self.store, rid, root)
        self.assertEqual(tree_hashes(root), before)
        st = self.status(rid)
        self.assertEqual(st['W1']['status'], 'LEGACY_V4_DOWNLOADED')
        self.assertEqual(st['W2']['status'], 'MANUAL_IMPORTED')
        self.assertEqual(st['W4']['status'], 'NO_OA_URL')      # stored SHA no longer matches the file
        self.assertEqual(result['blocked_hosts_imported'], ['blocked.example.org'])
        self.assertTrue(self.store.host_state('blocked.example.org')['blocked'])
        self.assertNotIn('token=leak', json.dumps(result))
        self.assertTrue(self.store.verify(rid)['verified'])

    def test_main_and_excluded_never_become_screening_decisions(self):
        rid = self.store.create_run()['run_id']
        self.store.import_file(rid, write_csv(self.base / 'q_main.csv', [dict(row('W1'), main_corpus='True')]))
        self.store.import_file(rid, write_csv(self.base / 'q_excluded.csv', [dict(row('W2'), main_corpus='False',
                                                                                     main_exclusion_reasons='quartile')]))
        self.store.discover(rid)
        for table in ('screening_decision', 'screening_outcome', 'corpus_release_item'):
            self.assertEqual(self.table_count(table), 0)
        families = [json.loads(b['batch_json'])['detail']['file_family'] for b in self.store.batches(rid)]
        self.assertEqual(families, ['LEGACY_MAIN', 'LEGACY_EXCLUDED'])

    def test_legacy_fixture_raw_jsonl(self):
        fixture = Path(__file__).parent / 'fixtures' / 'legacy_v4_1_0' / 'pilot_raw.jsonl'
        rid = self.store.create_run()['run_id']
        batch = self.store.import_file(rid, fixture)
        self.assertEqual(batch['source_format'], 'LEGACY_V4_1_0_FLAT')
        self.assertGreaterEqual(batch['counts']['accepted'], 2)


class TestExportAndVerify(PdfCase):
    def prepared(self):
        self.web.add(A + '/1.pdf', PDF_OK)
        self.web.add(B + '/2.pdf', (403, {}, b'denied'))
        rid = self.make_run([row('W1', A + '/1.pdf'), row('W2', B + '/2.pdf', landing=B + '/landing'), row('W3', is_oa='False')])
        self.download(rid)
        return rid

    def test_export_files_reports_and_offline_verify(self):
        rid = self.prepared()
        out = self.base / 'export'
        result = export(self.store, rid, out)
        self.assertFalse(result['authoritative'])
        self.assertEqual(sorted(p.name for p in out.iterdir()), sorted(FILES))
        manual = (out / 'manual_download.csv').read_text(encoding='utf-8')
        self.assertIn('W2.pdf', manual)
        self.assertIn('BOT_CHECK', manual)
        summary = json.loads((out / 'fetch_summary.json').read_text(encoding='utf-8'))
        self.assertEqual((summary['downloaded'], summary['without_pdf'], summary['unique_works']), (1, 2, 3))
        self.assertEqual(summary['blocked_hosts'], ['b.example.org'])
        self.assertIn('a.example.org', summary['by_host'])
        self.assertIn('INPUT_ROW', summary['by_evidence_source'])
        self.assertIn("<a href='https://b.example.org/landing'", (out / 'manual_download.html').read_text(encoding='utf-8'))
        self.assertIn('N1  - Save as W2.pdf (BOT_CHECK)', (out / 'manual_download.ris').read_text(encoding='utf-8'))
        downloaded = (out / 'downloaded.csv').read_text(encoding='utf-8')
        self.assertIn(hashlib.sha256(PDF).hexdigest(), downloaded)
        self.assertIn('raw/sha256/', downloaded)
        self.assertTrue(verify_export(out)['verified'])
        for text in [p.read_text(encoding='utf-8') for p in out.iterdir()]:
            self.assertNotIn(str(self.base), text)

    def test_tampered_or_unexpected_export_fails(self):
        rid = self.prepared()
        out = self.base / 'export'
        export(self.store, rid, out)
        with self.assertRaises(ArtifactIntegrityError):
            verify_export(out, expected_manifest_sha256='0' * 64)
        (out / 'manual_download.csv').write_text('edited', encoding='utf-8')
        with self.assertRaises(ArtifactIntegrityError):
            verify_export(out)

    def test_close_freezes_manifest_and_authoritative_export(self):
        rid = self.prepared()
        closed = self.store.close(rid)
        self.assertEqual(closed['state'], 'CLOSED')
        self.store.host_event('a.example.org', 'BLOCK', 'later operator decision', origin='OPERATOR')
        self.assertTrue(self.store.verify(rid)['verified'])     # later host events do not alter a closed manifest
        result = export(self.store, rid, self.base / 'final')
        self.assertTrue(result['authoritative'])
        self.assertEqual(result['manifest_sha256'], self.store.get(rid)['manifest_sha256'])
        with self.assertRaises(ValidationError):
            self.store.import_identifiers(rid, ['W9'])
        self.assertEqual(self.download(rid)['works_attempted'], 0)

    def test_export_pdfs_uses_legacy_names(self):
        rid = self.prepared()
        out = self.base / 'pdfs'
        export_pdfs(self.store, rid, out)
        self.assertEqual([p.name for p in out.iterdir()], ['W1.pdf'])
        self.assertEqual((out / 'W1.pdf').read_bytes(), PDF)

    def test_orphan_quarantine(self):
        rid = self.prepared()
        stray = self.store.cas.put_bytes(b'unreferenced bytes')
        report = scan(self.db, self.ws)
        self.assertEqual([o['sha256'] for o in report['orphans']], [stray.artifact_sha256])
        moved = quarantine(self.db, self.ws, self.base / 'quarantine')
        self.assertEqual(len(moved['quarantined']), 1)
        self.assertEqual(scan(self.db, self.ws)['orphans'], [])
        self.assertTrue(self.store.verify(rid)['verified'])


class TestReviewBridge(FullCase):
    def test_review_manual_supplement_never_changes_sealed_evidence(self):
        manifest = self.completed()
        manual_item = manifest['manual_queue'][0]['corpus_item_id']
        release_before = self.releases.get(self.crid)['release_manifest_sha256']
        fulltext_before = self.full.get(self.fid)['manifest_sha256']
        store = PdfAcquisitionStore(self.db)
        folder = self.root / 'manual review'
        folder.mkdir()
        (folder / (manual_item + '.pdf')).write_bytes(PDF)
        (folder / 'W999.pdf').write_bytes(PDF2)
        result = manual_import(store, fulltext_run_id=self.fid, path=folder)
        self.assertEqual((result['counts']['linked'], result['counts']['reconciliation_required']), (1, 1))
        status = review_status(store, self.fid)
        row_ = next(r for r in status['items'] if r['corpus_item_id'] == manual_item)
        self.assertEqual((row_['stage11b_status'], row_['operational_status']), ('MANUAL_REQUIRED', 'MANUAL_SUPPLIED_UNVERIFIED'))
        self.assertIsNone(row_['selected_artifact_sha256'])
        self.assertEqual(self.full.get(self.fid)['manifest_sha256'], fulltext_before)
        self.assertEqual(self.releases.get(self.crid)['release_manifest_sha256'], release_before)
        self.assertTrue(self.full.verify(self.fid)['verified'])
        self.releases.verify(self.crid)

    def test_corpus_release_as_discovery_input_keeps_corpus_unchanged(self):
        self.setup_run()
        handoff = self.releases.handoff(self.crid)
        store = PdfAcquisitionStore(self.db)
        rid = store.create_run()['run_id']
        store.import_corpus_release(rid, self.crid)
        store.discover(rid)
        web = FakeWeb({'https://papers.example.org/1.pdf': PDF_OK, 'https://papers.example.org/2.pdf': PDF2_OK,
                       'https://papers.example.org/3.pdf': PDF_OK})
        from fulltext_acquisition_fixtures import public_dns
        from reprovane.pdf_acquisition.engine import Downloader, make_transport
        transport = make_transport(store.policy(rid), resolver=public_dns, opener=web, sleeper=lambda _: None)
        Downloader(store, rid, transport, clock=lambda: 0.0, sleep=lambda _: None).run()
        statuses = store.statuses(rid)
        self.assertEqual({s['status'] for s in statuses}, {'DOWNLOADED'})   # discovery may take submitted versions
        self.assertEqual({a['version_claim'] for s in statuses for a in s['artifacts']},
                         {'publishedVersion', 'acceptedVersion', 'submittedVersion'})
        self.assertEqual(self.releases.handoff(self.crid), handoff)
        self.assertTrue(store.verify(rid)['verified'])

    def polite_acquire(self, web, store):
        from fulltext_acquisition_fixtures import public_dns
        from reprovane.fulltext_acquisition.transport import SafeTransport
        from reprovane.pdf_acquisition.review import PoliteReviewTransport
        inner = SafeTransport(self.full.policy(self.fid), resolver=public_dns, opener=web, sleeper=lambda _: None)
        polite = PoliteReviewTransport(store, self.fid, inner, clock=lambda: 0.0, sleep=lambda _: None)
        self.full.acquire(self.fid, polite, expected_policy_sha256=self.full.get(self.fid)['policy_sha256'])
        return polite.summary()

    def test_review_acquire_respects_robots_txt(self):
        self.discovered()
        store = PdfAcquisitionStore(self.db)
        web = FakeWeb({'https://papers.example.org/robots.txt': (200, {'Content-Type': 'text/plain'}, b'User-agent: *\nDisallow: /2.pdf\n'),
                       'https://papers.example.org/1.pdf': PDF_OK, 'https://papers.example.org/2.pdf': PDF2_OK})
        summary = self.polite_acquire(web, store)
        self.assertEqual(web.requested('papers.example.org'), ['https://papers.example.org/1.pdf'])
        self.assertEqual(summary['robots_requests'], 1)
        rows = store._all('SELECT q.host, r.result_json FROM pdf_review_robots_request q JOIN pdf_review_robots_result r '
                          'ON r.request_id=q.request_id')
        self.assertEqual([(r['host'], json.loads(r['result_json'])['http_status']) for r in rows], [('papers.example.org', 200)])
        self.assertEqual(summary['ledger']['usage']['robots_requests'], 1)
        self.assertTrue(self.full.verify(self.fid)['verified'])
        self.full.finalize(self.fid)
        manifest = json.loads(self.full.get(self.fid)['manifest_json'])
        self.assertIn('ROBOTS_DISALLOWED', {r for item in manifest['manual_queue'] for r in item['reasons']})

    def test_review_acquire_never_contacts_a_blocked_host(self):
        self.discovered()
        store = PdfAcquisitionStore(self.db)
        store.host_event('papers.example.org', 'BLOCK', 'operator decision', origin='OPERATOR')
        web = FakeWeb({'https://papers.example.org/1.pdf': PDF_OK, 'https://papers.example.org/2.pdf': PDF2_OK})
        self.polite_acquire(web, store)
        self.assertEqual(web.calls, [])
        self.assertEqual({json.loads(r['result_json'])['failure_class'] for r in self.full.rows('fulltext_attempt_result', self.fid)},
                         {'HOST_BLOCKED'})
        self.assertTrue(self.full.verify(self.fid)['verified'])

    def test_r2_04_review_robots_requests_count_against_the_frozen_budget(self):
        from reprovane.pdf_acquisition.review import verify_review_politeness
        cap = dict(items=10, http_requests=1, bytes=500_000_000, artifacts=10, provider_lookups=10)
        self.discovered(protocol=review_policy(global_download_budget=cap))
        store = PdfAcquisitionStore(self.db)
        web = FakeWeb({'https://papers.example.org/1.pdf': PDF_OK, 'https://papers.example.org/2.pdf': PDF2_OK})
        summary = self.polite_acquire(web, store)
        self.assertEqual(len(web.calls), 1)                      # robots.txt used the only request
        self.assertEqual(web.calls[0]['url'], 'https://papers.example.org/robots.txt')
        self.assertEqual(summary['ledger']['usage']['http_requests'], 1)
        self.assertTrue(self.full.verify(self.fid)['verified'])
        failures = {json.loads(r['result_json'])['failure_class'] for r in self.full.rows('fulltext_attempt_result', self.fid)}
        self.assertEqual(failures, {'GLOBAL_BUDGET_EXHAUSTED'})
        # A reservation is durable before I/O: an interrupted robots request keeps its full charge.
        reserved = store._one('SELECT reserved_bytes FROM pdf_review_robots_request')['reserved_bytes']
        self.assertGreaterEqual(reserved, 65536)
        self.assertTrue(verify_review_politeness(store, self.full, self.fid)['verified'])

    def test_review_acquire_command_is_parsed_by_the_unified_cli(self):
        from reprovane.pdf_acquisition.__main__ import review_acquire_parser
        a = review_acquire_parser().parse_args(['--workspace', 'w s', '--run-id', 'ftr_x', '--expected-policy-sha256', 'a' * 64])
        self.assertEqual((a.run_id, a.expected_policy_sha256), ('ftr_x', 'a' * 64))

    def test_review_cli_pass_through(self):
        self.setup_run()
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.db.close()
            code = pdf_main(['review', 'inspect', '--workspace', str(self.root / 'research'), '--run-id', self.fid])
            self.db = Database(self.root / 'research')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['run_id'], self.fid)


class TestCommandLine(PdfCase):
    def run_cli(self, *args):
        self.db.close()
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                code = pdf_main([str(a) for a in args])
            return code, out.getvalue(), err.getvalue()
        finally:
            self.db = Database(self.ws)
            self.store = PdfAcquisitionStore(self.db)

    def test_offline_cli_workflow_with_spaces_and_unicode(self):
        source = write_csv(self.base / 'đầu vào' / 'x_main.csv', [row('W1', is_oa='False'), row('W2')])
        code, out, err = self.run_cli('import', '--workspace', self.ws, '--input', source, '--label', 'pilot run')
        self.assertEqual(code, 0, err)
        rid = json.loads(out)['run_id']
        self.assertEqual(self.run_cli('discover', '--workspace', self.ws, '--run-id', rid)[0], 0)
        code, out, err = self.run_cli('download', '--workspace', self.ws, '--run-id', rid, '--quiet')
        self.assertEqual(code, 0, err)
        folder = self.pdf_dir({'W2.pdf': PDF}, name='bản tải tay')
        code, out, _ = self.run_cli('manual-import', '--workspace', self.ws, '--run-id', rid, '--input', folder)
        self.assertEqual(json.loads(out)['counts']['linked'], 1)
        code, out, _ = self.run_cli('status', '--workspace', self.ws, '--run-id', rid)
        self.assertEqual(json.loads(out)['summary']['manually_imported'], 1)
        self.assertEqual(self.run_cli('verify', '--workspace', self.ws, '--run-id', rid)[0], 0)
        self.assertEqual(self.run_cli('close', '--workspace', self.ws, '--run-id', rid)[0], 0)
        out_dir = self.base / 'xuất báo cáo'
        self.assertEqual(self.run_cli('export', '--workspace', self.ws, '--run-id', rid, '--directory', out_dir)[0], 0)
        self.assertEqual(self.run_cli('verify-export', '--directory', out_dir)[0], 0)
        self.assertEqual(self.run_cli('runs', '--workspace', self.ws)[0], 0)

    def test_help_policy_and_errors(self):
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()):
            pdf_main(['--help'])
        self.assertEqual(ctx.exception.code, 0)
        code, out, _ = self.run_cli('policy', '--profile', 'review')
        self.assertEqual(json.loads(out)['profile'], 'REVIEW_FULLTEXT')
        code, _, err = self.run_cli('import', '--workspace', self.ws, '--input', self.base / 'missing.csv')
        self.assertEqual(code, 2)
        code, _, err = self.run_cli('status', '--workspace', self.ws, '--run-id', 'pdr_unknown')
        self.assertEqual(code, 2)

    def test_router_exposes_pdf_family(self):
        from reprovane.__main__ import ROUTES
        self.assertEqual(ROUTES['pdf'], 'pdf_acquisition')

    def test_every_documented_pdf_command_parses(self):
        import importlib.util
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('docs_audit_pdf', root / 'tools' / 'audit_documentation.py')
        audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit)
        count = 0
        for name in ('docs/PDF_ACQUISITION_USER_GUIDE.md', 'docs/PDF_LEGACY_PARITY_MATRIX.md', 'USER_GUIDE.md'):
            for _, text, args in audit.cli_commands(root / name):
                if args and args[0] == 'pdf':
                    with self.subTest(document=name, command=text):
                        self.assertTrue(audit.validate_command(args))
                    count += 1
        self.assertGreater(count, 25)


class TestWorkspaceIntegration(PdfCase):
    def test_doctor_reports_pdf_acquisition_without_writing(self):
        from reprovane.operations.workspace import doctor
        self.web.add(A + '/1.pdf', PDF_OK)
        rid = self.make_run([row('W1', A + '/1.pdf')])
        self.download(rid)
        self.store.close(rid)
        self.db.close()
        before = tree_hashes(self.ws)
        report = doctor(self.ws, full=True)
        self.assertEqual(tree_hashes(self.ws), before)
        self.assertEqual(report['schema_version'], 14)
        self.assertEqual(report['pdf_acquisition']['verified_closed_runs'], 1)
        self.db = Database(self.ws)

    def test_schema13_workspace_upgrades_on_first_pdf_use(self):
        from reprovane.legacy_migration import LegacyMigrationStore
        folder = self.base / 'schema13'
        with Database(folder) as db:
            LegacyMigrationStore(db)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 13)
        with Database(folder) as db:
            PdfAcquisitionStore(db)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 14)


if __name__ == '__main__':
    unittest.main()
