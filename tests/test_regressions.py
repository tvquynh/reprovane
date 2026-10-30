"""Inherited scientific regression tests for Reprovane (stdlib unittest only).

Run from the project root:
    python -m unittest discover -s tests -v

Every test uses a tiny synthetic SCImago repository written to a temporary directory and, where
HTTP is needed, a local 127.0.0.1 server. No internet access and no real API key are used.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import http.server
import io
import json
import random
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
LEGACY_REFERENCE = ROOT / "tests" / "legacy_reference"
sys.path.insert(0, str(ROOT))

from reprovane import openalex, scimago_repo  # noqa: E402
from reprovane.common import normalize_issn, normalize_quartile, read_csv, redact_secrets, split_issns  # noqa: E402
from reprovane.pipeline import (  # noqa: E402
    FUTURE_YEAR_QUARTILE_REASON, admission_summary, apply_main_policy, build_summary, enrich_existing_file,
    quartile_filter_diagnostics, source_audit,
)
from reprovane.scimago import enrich_records, relevant_category_summary, source_history  # noqa: E402
from reprovane.venues import VenueTierError, VenueTiers  # noqa: E402

openalex.time.sleep = lambda *_: None
scimago_repo.time.sleep = lambda *_: None

HEADER = [
    "Rank", "Sourceid", "Title", "Type", "Issn", "Publisher", "Open Access", "Open Access Diamond", "SJR",
    "SJR Best Quartile", "H index", "Total Docs. (YEAR)", "Total Docs. (3years)", "Total Refs.",
    "Total Citations (3years)", "Citable Docs. (3years)", "Citations / Doc. (2years)", "Ref. / Doc.", "%Female",
    "Overton", "Country", "Region", "Publisher", "Coverage", "Categories", "Areas",
]
FIRST, LATEST = 2020, 2023

# sourceid -> (title by year, type, issn by year, quartile by year, categories)
SOURCES = {
    "100": ({y: "Alpha Journal" for y in range(2020, 2024)}, "journal", {y: "11111111" for y in range(2020, 2024)},
            {2020: "-", 2021: "Q2", 2022: "Q1", 2023: "Q1"}, "Information Systems ({q}); Management Information Systems (Q1)"),
    "200": ({2020: "Hist Journal (discontinued)", 2021: "Hist Journal (discontinued)"}, "journal",
            {2020: "2222222X", 2021: "2222222X"}, {2020: "Q3", 2021: "Q2"}, "Software ({q})"),
    "300": ({2020: "Old Name", 2021: "Old Name", 2022: "New Name", 2023: "New Name"}, "journal",
            {2020: "33333333", 2021: "33333333", 2022: "33333334", 2023: "33333334"},
            {2020: "Q4", 2021: "Q4", 2022: "Q3", 2023: "Q3"}, "Software ({q})"),
    "401": ({2023: "Shared One"}, "journal", {2023: "44444444"}, {2023: "Q1"}, "Software ({q})"),
    "402": ({2023: "Shared Two"}, "journal", {2023: "44444444"}, {2023: "Q2"}, "Software ({q})"),
    "500": ({y: "Conf Proc" for y in range(2020, 2024)}, "conference and proceedings",
            {y: "55555555" for y in range(2020, 2024)}, {y: "-" for y in range(2020, 2024)}, "Software"),
    "600": ({2023: "Reissued Proc"}, "conference and proceedings", {2023: "66666666"}, {2023: "Q3"}, "Software ({q})"),
    "601": ({2020: "Reissued Proc"}, "conference and proceedings", {2020: "66666666"}, {2020: "Q4"}, "Software ({q})"),
    "700": ({2021: "Mixed Journal", 2022: "Mixed Journal"}, "journal", {2021: "77777777", 2022: "77777777"},
            {2021: "Q2", 2022: "Q2"}, "Software ({q})"),
    "701": ({2022: "Mixed Journal B"}, "journal", {2022: "77777777"}, {2022: "Q3"}, "Software ({q})"),
    "800": ({2020: "Journal X", 2021: "Journal X", 2022: "Journal X Series"}, "journal",
            {2020: "88888888", 2021: "88888888", 2022: "88888889"}, {2020: "Q2", 2021: "Q2", 2022: "Q3"}, "Software ({q})"),
    # v3.0.5 ISSN-conflict fixtures: a source without ISSN, and two sources sharing a normalised title.
    "910": ({y: "NoIssn Proceedings" for y in range(2020, 2024)}, "conference and proceedings",
            {y: "" for y in range(2020, 2024)}, {y: "Q3" for y in range(2020, 2024)}, "Software ({q})"),
    "920": ({2023: "Twin Journal"}, "journal", {2023: "92092092"}, {2023: "Q2"}, "Software ({q})"),
    "921": ({2023: "Twin-Journal"}, "journal", {2023: ""}, {2023: "Q3"}, "Software ({q})"),
    "930": ({2023: "Alpha and Beta Review"}, "journal", {2023: "93093093"}, {2023: "Q1"}, "Software ({q})"),
}
TYPE_OVERRIDES = {("800", 2022): "book series"}


def build_repo(directory: Path) -> Dict:
    """Write a synthetic SCImago mirror (4 annual files + APC snapshot + manifest + checksums)."""
    directory.mkdir(parents=True, exist_ok=True)
    for year in range(FIRST, LATEST + 1):
        header = [h.replace("YEAR", str(year)) for h in HEADER]
        with (directory / f"scimagojr-{year}.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_MINIMAL)
            writer.writerow(header)
            rank = 0
            for sid, (titles, stype, issns, quartiles, cats) in SOURCES.items():
                if year not in titles:
                    continue
                rank += 1
                q = quartiles[year]
                sjr = "" if q == "-" else f"{int(sid) / 100 + year - 2000:.3f}".replace(".", ",")
                row = [str(rank), sid, titles[year], TYPE_OVERRIDES.get((sid, year), stype), issns[year], "Pub", "No", "No", sjr, q, "42",
                       "10", "30", "100", "50", "30", "1,5", "10", "", "0", "Viet Nam", "Asiatic Region", "Pub",
                       "2020-2023", cats.format(q=q if q != "-" else "Q4"), "Computer Science"]
                writer.writerow(row)
    with (directory / "journal-value.csv").open("w", encoding="utf-8", newline="") as fh:
        fh.write("Rank;Sourceid;Title;Type;Issn;Publisher;Open Access;Open Access Diamond;SJR;SJR Best Quartile;"
                 "Total Docs. (2023);Estimated APC (USD);Estimated value (USD)\n")
        fh.write('1;100;"Alpha Journal";journal;"11111111";No;No;"Pub";1,230;Q1;10;1500;1000;\n')
    manifest = {
        "schema_version": 1, "dataset": "synthetic", "updated": "2026-10-25", "base_url": "http://127.0.0.1:1/",
        "sjr": {"first_year": FIRST, "last_year": LATEST, "latest_snapshot_year": LATEST,
                "url_pattern": "scimagojr-{year}.csv", "delimiter": ";", "encoding": "utf-8"},
        "journal_value": {"year": LATEST, "file": "journal-value.csv"},
        "checksums": {"algorithm": "sha256", "file": "SHA256SUMS.txt"},
        "files": {"expected_total_data_files": LATEST - FIRST + 2},
        "policy": {"require_present_in_latest_scimago_for_main_corpus": True},
    }
    write_checksums(directory, manifest)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def write_checksums(directory: Path, manifest: Dict) -> None:
    names = [f"scimagojr-{y}.csv" for y in range(FIRST, LATEST + 1)] + [manifest["journal_value"]["file"]]
    lines = [f"{hashlib.sha256((directory / n).read_bytes()).hexdigest()}  {n}" for n in names]
    (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def paper(pid: str, year, source: str, issn: str = "", issn_l: str = "", stype: str = "journal", src_id: str = "") -> Dict:
    return {"openalex_id": pid, "doi": "", "title": f"Paper {pid}", "publication_year": str(year),
            "source": source, "source_openalex_id": src_id or f"S-{source}", "source_type": stype,
            "issn": issn, "issn_l": issn_l, "source_is_in_doaj": "False", "source_is_core": "False"}


def corpus() -> List[Dict]:
    return [
        paper("p01", 2021, "Alpha Journal", "1111-1111", "1111-1111"),
        paper("p02", 2020, "Alpha Journal", "1111-1111", "1111-1111"),
        paper("p03", 2026, "Alpha Journal", "1111-1111", "1111-1111"),
        paper("p04", 2021, "Hist Journal", "2222-222x", "2222-222x"),
        paper("p05", 2022, "Hist Journal", "2222-222X", "2222-222X"),
        paper("p06", 2026, "Hist Journal", "2222-222X", "2222-222X"),
        paper("p07", 2020, "Hist Journal", "", "", src_id="S-hist-title-only"),
        paper("p08", 2023, "Unknown Venue", "9999-9999", "9999-9999"),
        paper("p09", 2023, "Shared", "4444-4444", "4444-4444"),
        paper("p10", 2021, "Conf Proc", "5555-5555", "5555-5555", stype="conference"),
        paper("p11", 2020, "Old Name", "3333-3333", "3333-3333"),
        paper("p12", 2020, "Reissued Proc", "6666-6666", "6666-6666", stype="conference"),
        paper("p13", 2026, "Old Name", "3333-3333", "3333-3333", src_id="S-old-name-2"),
        paper("p14", 2021, "Mixed Journal", "7777-7777", "7777-7777", src_id="S-mixed"),
        paper("p15", 2022, "Mixed Journal", "7777-7777", "7777-7777", src_id="S-mixed"),
        paper("p16", "", "Alpha Journal", "1111-1111", "1111-1111"),
        paper("p17", 2021, "", "", "", stype=""),
    ]


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "scimago"
        self.manifest = build_repo(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def enrich(self, rows=None, categories=None):
        rows = rows if rows is not None else corpus()
        out = enrich_records(rows, self.dir, self.manifest, categories or [])
        return {r["openalex_id"]: r for r in out}, out


class TestNormalization(unittest.TestCase):
    def test_issn_normalization_handles_hyphen_case_and_lists(self):
        self.assertEqual(normalize_issn("1574-017x"), "1574017X")
        self.assertEqual(split_issns("1574-017x; 1875-905X, 1574017X|0000-0001"), ["1574017X", "1875905X", "00000001"])
        self.assertEqual(split_issns(""), [])

    def test_quartile_placeholder(self):
        self.assertEqual(normalize_quartile("-"), "")
        self.assertEqual(normalize_quartile(" q2 "), "Q2")

    def test_relevant_category_is_exact_not_substring(self):
        cats = "Information Systems and Management (Q1); Management Information Systems (Q1); Communication (Q1)"
        self.assertEqual(relevant_category_summary(cats, ["Information Systems", "Computer Networks and Communications"]), ("", ""))
        cats = "Information Systems (Q3); Management Information Systems (Q1)"
        self.assertEqual(relevant_category_summary(cats, ["information systems"]), ("Information Systems (Q3)", "Q3"))

    def test_redact_secrets(self):
        text = "GET /works?search=x&api_key=SECRET123&mailto=a@b.c failed; key SECRET123"
        redacted = redact_secrets(text, "SECRET123")
        self.assertNotIn("SECRET123", redacted)
        self.assertIn("api_key=REDACTED", redacted)


class TestEnrichment(RepoCase):
    def test_active_latest_uses_exact_publication_year_row(self):
        rows, _ = self.enrich()
        p01 = rows["p01"]
        self.assertEqual(p01["source_status"], "ACTIVE_LATEST")
        self.assertEqual(p01["scimago_sourceid"], "100")
        self.assertEqual(p01["ranking_basis"], "EXACT_PUBLICATION_YEAR")
        self.assertEqual(p01["sjr_year_at_publication"], 2021)
        self.assertEqual(p01["best_quartile_at_publication"], "Q2")  # 2021 row, not the latest Q1
        self.assertAlmostEqual(p01["sjr_at_publication"], 22.0)
        self.assertEqual(p01["best_quartile_latest"], "Q1")

    def test_dash_quartile_is_unavailable_not_a_value(self):
        rows, out = self.enrich()
        self.assertEqual(rows["p02"]["best_quartile_at_publication"], "")
        self.assertEqual(rows["p02"]["ranking_basis"], "EXACT_PUBLICATION_YEAR")
        main, excluded = apply_main_policy(out, self.manifest, source_types=["journal"], quartiles=["Q1"],
                                           quartile_basis="publication")
        self.assertIn("QUARTILE_UNAVAILABLE", rows["p02"]["main_exclusion_reasons"])
        summary = build_summary(out, main, excluded)
        self.assertNotIn("-", summary["publication_quartile_counts"])
        self.assertGreaterEqual(summary["publication_quartile_counts"]["NOT_ASSIGNED"], 1)

    def test_future_year_never_gets_publication_year_metrics(self):
        rows, _ = self.enrich()
        p03 = rows["p03"]
        self.assertEqual(p03["source_status"], "ACTIVE_LATEST")
        self.assertEqual(p03["ranking_basis"], "LATEST_AVAILABLE")
        self.assertIsNone(p03["sjr_year_at_publication"])
        self.assertIsNone(p03["sjr_at_publication"])
        self.assertEqual(p03["best_quartile_at_publication"], "")
        self.assertEqual(p03["best_quartile_latest"], "Q1")

    def test_future_year_without_latest_metadata_is_unavailable(self):
        rows, _ = self.enrich()
        self.assertEqual(rows["p06"]["ranking_basis"], "UNAVAILABLE")
        self.assertEqual(rows["p08"]["ranking_basis"], "UNAVAILABLE")

    def test_historical_only_from_publication_year(self):
        rows, _ = self.enrich()
        p04 = rows["p04"]
        self.assertEqual(p04["source_status"], "HISTORICAL_ONLY")
        self.assertEqual(p04["scimago_sourceid_historical"], "200")
        self.assertEqual(p04["best_quartile_at_publication"], "Q2")
        self.assertFalse(p04["in_scimago_latest"])
        self.assertNotIn("sjr_latest", p04)

    def test_historical_only_when_publication_year_absent_or_future(self):
        rows, _ = self.enrich()
        for pid in ("p05", "p06"):
            row = rows[pid]
            self.assertEqual(row["source_status"], "HISTORICAL_ONLY", pid)
            self.assertEqual(row["scimago_sourceid_historical"], "200")
            self.assertEqual(row["historical_match_method"], "ANY_EARLIER_SNAPSHOT_ISSNL_EXACT")
            self.assertEqual(row["historical_snapshot_years"], "2020-2021")
            self.assertEqual(row["ranking_basis"], "UNAVAILABLE")
            self.assertEqual(row["best_quartile_at_publication"], "")

    def test_historical_title_with_discontinued_suffix(self):
        rows, _ = self.enrich()
        p07 = rows["p07"]
        self.assertEqual(p07["source_status"], "HISTORICAL_ONLY")
        self.assertEqual(p07["publication_match_method"], "TITLE_STATUS_NORMALIZED")
        self.assertEqual(p07["best_quartile_at_publication"], "Q3")

    def test_unmatched_and_ambiguous_are_never_resolved_silently(self):
        rows, _ = self.enrich()
        self.assertEqual(rows["p08"]["source_status"], "UNMATCHED")
        p09 = rows["p09"]
        self.assertEqual(p09["source_status"], "AMBIGUOUS")
        self.assertEqual(p09["scimago_sourceid"], "")
        self.assertEqual(p09["latest_match_candidates"], "401; 402")
        self.assertEqual(p09["ranking_basis"], "UNAVAILABLE")
        self.assertEqual(rows["p15"]["source_status"], "AMBIGUOUS")  # two Sourceids share the ISSN in 2022

    def test_sourceid_present_in_latest_is_never_historical_only(self):
        rows, _ = self.enrich()
        p11, p13 = rows["p11"], rows["p13"]
        self.assertEqual(p11["source_status"], "ACTIVE_LATEST")
        self.assertEqual(p11["latest_match_method"], "SOURCEID_VIA_PUBLICATION_YEAR_ISSNL_EXACT")
        self.assertEqual(p11["scimago_title_latest"], "New Name")
        self.assertEqual(p11["best_quartile_at_publication"], "Q4")
        self.assertEqual(p13["source_status"], "ACTIVE_LATEST")
        self.assertEqual(p13["latest_match_method"], "SOURCEID_VIA_HISTORICAL_ISSNL_EXACT")
        self.assertEqual(p13["ranking_basis"], "LATEST_AVAILABLE")

    def test_publication_metrics_from_other_sourceid_are_flagged(self):
        rows, out = self.enrich()
        p12 = rows["p12"]
        self.assertEqual(p12["scimago_sourceid"], "600")
        self.assertEqual(p12["scimago_sourceid_at_publication"], "601")
        self.assertTrue(p12["publication_sourceid_differs"])
        self.assertEqual(build_summary(out, [], [])["papers_with_publication_sourceid_differing_from_identity"], 1)
        self.assertFalse(rows["p01"]["publication_sourceid_differs"])

    def test_missing_year_and_missing_source(self):
        rows, _ = self.enrich()
        self.assertEqual(rows["p16"]["ranking_basis"], "UNAVAILABLE")
        self.assertEqual(rows["p17"]["source_status"], "UNMATCHED")

    def test_reenrichment_ignores_stale_enrichment_columns(self):
        _, first = self.enrich()
        snapshot = {r["openalex_id"]: {k: str(v) for k, v in r.items()} for r in first}
        stale = [dict(r) for r in first]
        for row in stale:  # values an older run / older SCImago release might have left behind
            row.update({"scimago_sourceid_historical": "401", "sjr_latest": "9.9", "best_quartile_latest": "Q1",
                        "scimago_type_latest": "journal", "historical_snapshot_years": "1999", "source_status": "ACTIVE_LATEST"})
        _, second = self.enrich(stale)
        for row in second:
            self.assertEqual({k: str(v) for k, v in row.items()}, snapshot[row["openalex_id"]], row["openalex_id"])
        ambiguous = {r["openalex_id"]: r for r in second}["p09"]
        self.assertEqual((ambiguous["source_status"], ambiguous["ranking_basis"]), ("AMBIGUOUS", "UNAVAILABLE"))
        self.assertNotIn("sjr_latest", ambiguous)

    def test_historical_result_does_not_depend_on_other_rows(self):
        row_a = paper("a", 2026, "Journal X", "8888-8888", "8888-8888", src_id="S-x")
        row_b = paper("b", 2026, "Other", "8888-8889", "8888-8889", src_id="S-other")
        alone, _ = self.enrich([dict(row_a)])
        together, _ = self.enrich([dict(row_a), dict(row_b)])
        for field in ("source_status", "historical_snapshot_years", "scimago_type_historical"):
            self.assertEqual(alone["a"][field], together["a"][field], field)
        self.assertEqual(alone["a"]["historical_snapshot_years"], "2020-2021")
        self.assertEqual(alone["a"]["scimago_type_historical"], "journal")
        self.assertEqual(together["b"]["scimago_type_historical"], "book series")

    def test_relevant_category_quartile_uses_exact_category(self):
        rows, _ = self.enrich(categories=["Information Systems"])
        self.assertEqual(rows["p01"]["relevant_category_quartile_at_publication"], "Q2")
        self.assertEqual(rows["p01"]["relevant_categories_at_publication"], "Information Systems (Q2)")


class TestMainPolicyAndReporting(RepoCase):
    def test_partition_journal_only_and_all_source_types(self):
        _, out = self.enrich()
        main, excluded = apply_main_policy(out, self.manifest, source_types=["journal"])
        ids_main = {r["openalex_id"] for r in main}
        self.assertEqual(len(main) + len(excluded), len(out))
        self.assertFalse(ids_main & {r["openalex_id"] for r in excluded})
        self.assertTrue(all(r["source_status"] == "ACTIVE_LATEST" for r in main))
        self.assertTrue(all((r.get("scimago_type_latest") or r["source_type"]) == "journal" for r in main))
        self.assertNotIn("p10", ids_main)
        _, out = self.enrich()
        main_all, _ = apply_main_policy(out, self.manifest, source_types=[])
        self.assertIn("p10", {r["openalex_id"] for r in main_all})
        self.assertEqual(len(main_all), sum(1 for r in out if r["source_status"] == "ACTIVE_LATEST"))

    def test_include_historical_only_does_not_admit_unmatched_or_ambiguous(self):
        _, out = self.enrich()
        main, _ = apply_main_policy(out, self.manifest, source_types=["journal"], include_historical_only=True)
        statuses = {r["source_status"] for r in main}
        self.assertEqual(statuses, {"ACTIVE_LATEST", "HISTORICAL_ONLY"})
        self.assertIn("p04", {r["openalex_id"] for r in main})
        _, out = self.enrich()
        main_any, _ = apply_main_policy(out, self.manifest, source_types=["journal"], any_source_status=True)
        self.assertTrue({"UNMATCHED", "AMBIGUOUS"} <= {r["source_status"] for r in main_any})

    def test_source_audit_aggregates_and_is_order_independent(self):
        _, out = self.enrich()
        audit = {a["source_openalex_id"]: a for a in source_audit(out)}
        self.assertEqual(audit["S-mixed"]["source_status"], "MIXED")
        self.assertEqual(audit["S-mixed"]["paper_status_counts"], "AMBIGUOUS:1; HISTORICAL_ONLY:1")
        self.assertEqual(audit["S-Hist Journal"]["paper_count"], 3)
        rows = corpus()
        random.Random(7).shuffle(rows)
        _, shuffled = self.enrich(rows)
        key = lambda a: a["source_openalex_id"]  # noqa: E731
        self.assertEqual(sorted(source_audit(out), key=key), sorted(source_audit(shuffled), key=key))

    def test_source_audit_descriptive_fields_are_order_independent(self):
        p1 = paper("h1", 2022, "Journal X Series", "8888-8889", "8888-8889", src_id="S-shared")
        p2 = paper("u1", 2023, "Unknown Venue", "9999-9999", "9999-9999", src_id="S-shared")
        _, forward = self.enrich([dict(p1), dict(p2)])
        _, backward = self.enrich([dict(p2), dict(p1)])
        a, b = source_audit(forward), source_audit(backward)
        self.assertEqual(a, b)
        self.assertEqual(a[0]["source_class"], "BOOK_SERIES | JOURNAL")
        self.assertEqual(a[0]["source_status"], "MIXED")
        self.assertEqual(build_summary(forward, [], [])["unique_source_class_counts"],
                         build_summary(backward, [], [])["unique_source_class_counts"])

    def test_enrich_file_outputs_stable_schema_and_metadata(self):
        tmp = Path(self.tmp.name)
        src = tmp / "in.csv"
        rows = [paper("x1", 2023, "Unknown Venue", "9999-9999")]
        with src.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        summary = enrich_existing_file(src, tmp / "out", self.dir, self.manifest, source_types=["journal"])
        self.assertEqual(summary["main_corpus_records"], 0)
        header_main = (tmp / "out_main.csv").read_text(encoding="utf-8-sig").splitlines()[0]
        header_enriched = (tmp / "out_enriched.csv").read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertEqual(header_main, header_enriched)
        meta = json.loads((tmp / "out_run_metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["command"], "enrich")
        self.assertEqual(len(meta["input_sha256"]), 64)
        self.assertEqual(len(read_csv(tmp / "out_excluded.csv")), 1)


class TestInspectSource(RepoCase):
    def test_active_source_history_is_per_year(self):
        match, history, apc, status = source_history(self.dir, self.manifest, "Alpha Journal")
        self.assertEqual((status, match.sourceid), ("ACTIVE_LATEST", "100"))
        self.assertEqual([h["year"] for h in history], [2020, 2021, 2022, 2023])
        self.assertEqual([h["best_quartile"] for h in history], ["", "Q2", "Q1", "Q1"])
        self.assertEqual(len({h["sjr"] for h in history if h["sjr"] is not None}), 3)
        self.assertEqual(apc["estimated_apc_usd"], 1500.0)

    def test_historical_only_source_found_with_trajectory(self):
        match, history, apc, status = source_history(self.dir, self.manifest, "Hist Journal")
        self.assertEqual((status, match.sourceid), ("HISTORICAL_ONLY", "200"))
        self.assertEqual(match.method, "HISTORICAL_TITLE_STATUS_NORMALIZED")
        self.assertEqual([h["year"] for h in history], [2020, 2021])
        self.assertIsNone(apc)
        match, _, _, status = source_history(self.dir, self.manifest, "", "2222-222x")
        self.assertEqual((status, match.method), ("HISTORICAL_ONLY", "HISTORICAL_ISSN_EXACT"))

    def test_old_title_of_latest_source_is_active(self):
        match, history, _, status = source_history(self.dir, self.manifest, "Old Name")
        self.assertEqual((status, match.sourceid), ("ACTIVE_LATEST", "300"))
        self.assertEqual(match.method, "SOURCEID_VIA_HISTORICAL_TITLE_EXACT")
        self.assertEqual(len(history), 4)

    def test_ambiguous_and_unmatched(self):
        self.assertEqual(source_history(self.dir, self.manifest, "", "4444-4444")[3], "AMBIGUOUS")
        self.assertEqual(source_history(self.dir, self.manifest, "", "7777-7777")[3], "AMBIGUOUS")
        self.assertEqual(source_history(self.dir, self.manifest, "No Such Venue")[3], "UNMATCHED")


class _StaticHandler(http.server.SimpleHTTPRequestHandler):
    hits: List[str] = []

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        super().do_GET()


@contextlib.contextmanager
def serve(directory: Path):
    handler = type("Handler", (_StaticHandler,), {"hits": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: handler(*a, directory=str(directory), **k))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", handler
    finally:
        server.shutdown()
        server.server_close()


class TestRepositoryIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.remote = Path(self.tmp.name) / "remote"
        self.cache = Path(self.tmp.name) / "cache"
        self.manifest = build_repo(self.remote)

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, base_url):
        self.manifest["base_url"] = base_url
        (self.remote / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def sync(self, base, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return scimago_repo.sync_repository(self.cache, base + "manifest.json", verbose=True, **kw)

    def test_cold_warm_corrupt_and_offline(self):
        with serve(self.remote) as (base, _):
            self.publish(base)
            state = self.sync(base)["state"]
            self.assertEqual((state["downloaded_files"], state["reused_files"], state["integrity_verified"]), (5, 0, True))
            state = self.sync(base)["state"]
            self.assertEqual((state["downloaded_files"], state["reused_files"]), (0, 5))
            self.assertTrue(scimago_repo.validate_cached_repository(self.cache, base + "manifest.json")["ok"])

            target = self.cache / "scimagojr-2021.csv"
            target.write_bytes(target.read_bytes().replace(b"Alpha", b"Alphb"))
            result = scimago_repo.validate_cached_repository(self.cache, offline=True)
            self.assertFalse(result["ok"])
            self.assertEqual([r["file"] for r in result["files"] if not r["sha256_ok"]], ["scimagojr-2021.csv"])
            with self.assertRaises(scimago_repo.RepositoryError):
                scimago_repo.sync_repository(self.cache, offline=True, verbose=False)
            state = self.sync(base)["state"]
            self.assertEqual(state["downloaded_files"], 1)
            self.assertTrue(scimago_repo.validate_cached_repository(self.cache, offline=True)["ok"])

    def test_remote_bytes_not_matching_checksum_are_rejected(self):
        with serve(self.remote) as (base, _):
            self.publish(base)
            self.sync(base)
            good = (self.cache / "scimagojr-2022.csv").read_bytes()
            (self.cache / "scimagojr-2022.csv").write_bytes(b"corrupt")
            (self.remote / "scimagojr-2022.csv").write_bytes(b"tampered remote bytes")
            with self.assertRaisesRegex(scimago_repo.RepositoryError, "SHA-256 mismatch"):
                self.sync(base)
            self.assertEqual((self.cache / "scimagojr-2022.csv").read_bytes(), b"corrupt")
            self.assertFalse((self.cache / "scimagojr-2022.csv.part").exists())
            self.assertFalse(scimago_repo.validate_cached_repository(self.cache, offline=True)["ok"])
            (self.remote / "scimagojr-2022.csv").write_bytes(good)
            self.assertEqual(self.sync(base)["state"]["downloaded_files"], 1)

    def test_missing_remote_file_fails_fast(self):
        with serve(self.remote) as (base, handler):
            self.publish(base)
            (self.remote / "scimagojr-2020.csv").unlink()
            with self.assertRaisesRegex(scimago_repo.RepositoryError, "not found \\(HTTP 404\\)"):
                self.sync(base)
            self.assertEqual(sum(1 for p in handler.hits if p.startswith("/scimagojr-2020.csv")), 1)

    def test_offline_without_cache_and_unsafe_names(self):
        with self.assertRaises(scimago_repo.RepositoryError):
            scimago_repo.sync_repository(self.cache, offline=True, verbose=False)
        bad = json.loads(json.dumps(self.manifest))
        bad["journal_value"]["file"] = "../outside.csv"
        self.assertTrue(any("Unsafe" in e for e in scimago_repo.validate_manifest(bad)))


class _OpenAlexHandler(http.server.BaseHTTPRequestHandler):
    works = [{"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.1/{i}", "title": f"T{i}",
              "publication_year": 2024, "primary_location": {"source": {"display_name": "Alpha Journal"}}}
             for i in range(1, 4)]
    status = 200
    calls: List[str] = []

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        from urllib.parse import parse_qs, urlparse
        type(self).calls.append(self.path)
        if type(self).status != 200:
            body = b'{"error": "bad request"}'
            self.send_response(type(self).status)
        else:
            qs = parse_qs(urlparse(self.path).query)
            start = 0 if qs["cursor"][0] == "*" else int(qs["cursor"][0])
            per_page = int(qs["per_page"][0])
            chunk = self.works[start:start + per_page]
            nxt = str(start + per_page) if start + per_page < len(self.works) else None
            body = json.dumps({"meta": {"count": len(self.works), "next_cursor": nxt}, "results": chunk}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextlib.contextmanager
def fake_openalex(status=200):
    handler = type("H", (_OpenAlexHandler,), {"calls": [], "status": status})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    old = openalex.OPENALEX_WORKS_URL
    openalex.OPENALEX_WORKS_URL = f"http://127.0.0.1:{server.server_port}/works"
    try:
        yield handler
    finally:
        openalex.OPENALEX_WORKS_URL = old
        server.shutdown()
        server.server_close()


class TestOpenAlex(unittest.TestCase):
    FAKE_KEY = "FAKEKEY_NEVER_PRINT_42"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def download(self, limit, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return openalex.download_queries(["q"], 2020, 2026, self.FAKE_KEY, "", self.dir / "raw.jsonl",
                                             self.dir / "ckpt.json", max_per_query=limit, **kw)

    def test_api_key_never_appears_in_errors(self):
        for status in (400, 403):
            with fake_openalex(status=status):
                client = openalex.OpenAlexClient(api_key=self.FAKE_KEY)
                with self.assertRaises(RuntimeError) as ctx:
                    client.request({"search": "x", "api_key": self.FAKE_KEY})
                self.assertNotIn(self.FAKE_KEY, str(ctx.exception))
                self.assertIsNone(ctx.exception.__cause__)
        old_retries, openalex.MAX_RETRIES = openalex.MAX_RETRIES, 2
        try:
            client = openalex.OpenAlexClient(api_key=self.FAKE_KEY, base_url="http://127.0.0.1:9/works")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(openalex.requests.RequestException) as ctx:
                client.request({"search": "x", "api_key": self.FAKE_KEY})
            self.assertNotIn(self.FAKE_KEY, str(ctx.exception) + out.getvalue())
            self.assertIsNone(ctx.exception.__cause__)
            self.assertTrue(ctx.exception.__suppress_context__)
        finally:
            openalex.MAX_RETRIES = old_retries

    def test_stop_by_limit_is_success_not_failure_and_resume_exhausts(self):
        with fake_openalex():
            rows, summary = self.download(2)
            state = summary["queries"][0]
            self.assertEqual(len(rows), 2)
            self.assertEqual((state["stopped_by_limit"], state["has_more_results"], state["query_exhausted"],
                              state["run_completed_successfully"]), (True, True, False, True))
            rows, summary = self.download(10)
            state = summary["queries"][0]
            self.assertEqual(len(rows), 3)
            self.assertEqual((state["stopped_by_limit"], state["query_exhausted"], state["has_more_results"]), (False, True, False))
        ckpt = json.loads((self.dir / "ckpt.json").read_text(encoding="utf-8"))
        self.assertNotIn(self.FAKE_KEY, json.dumps(ckpt))
        self.assertNotIn(self.FAKE_KEY, (self.dir / "raw.jsonl").read_text(encoding="utf-8"))

    def test_legacy_checkpoint_at_limit_needs_no_network(self):
        sig = {"query": "q", "from_year": 2020, "to_year": 2026, "require_abstract": False}
        (self.dir / "ckpt.json").write_text(json.dumps({"version": 1, "queries": {"q": {
            "signature": sig, "cursor": "abc", "retrieved": 2, "page": 1, "matches_total": 9, "done": False}}}), encoding="utf-8")
        with fake_openalex() as handler:
            _, summary = self.download(2)
            self.assertEqual(handler.calls, [])
        state = summary["queries"][0]
        self.assertEqual((state["stopped_by_limit"], state["has_more_results"], state["run_completed_successfully"]), (True, True, True))
        self.assertEqual(json.loads((self.dir / "ckpt.json").read_text(encoding="utf-8"))["version"], 2)

    def test_dedup_doi_and_openalex_id_are_aliases(self):
        store = openalex.RecordStore()
        self.assertTrue(store.add({"openalex_id": "W1", "doi": "", "title": "A", "matched_query": "q1"}))
        self.assertFalse(store.add({"openalex_id": "W1", "doi": "https://doi.org/10.5/ABC", "title": "A", "matched_query": "q2"}))
        self.assertFalse(store.add({"openalex_id": "", "doi": "10.5/abc", "title": "A", "matched_query": "q3"}))
        self.assertEqual(len(store), 1)
        record = next(iter(store.values()))
        self.assertEqual(record["doi"], "10.5/abc")
        self.assertEqual(record["matched_query"], "q1 || q2 || q3")
        self.assertTrue(store.add({"openalex_id": "W2", "doi": "10.5/xyz", "title": "B"}))
        self.assertIsNone(store.add({"openalex_id": "", "doi": "", "title": ""}))

    def test_search_field_title_and_abstract_sends_a_filter_instead_of_search(self):
        from urllib.parse import parse_qsl, urlparse
        with fake_openalex() as handler:
            _, summary = self.download(10, require_abstract=True)
            default_calls = [parse_qsl(urlparse(c).query) for c in handler.calls]
            handler.calls.clear()
            _, summary_ta = self.download(10, require_abstract=True, no_resume=True, search_field="title_and_abstract")
            ta_calls = [parse_qsl(urlparse(c).query) for c in handler.calls]
        # the default request is the one 3.1 sent: same parameters in the same order
        self.assertEqual([k for k, _ in default_calls[0]], ["search", "filter", "per_page", "cursor", "api_key"])
        self.assertEqual(dict(default_calls[0])["search"], "q")
        self.assertNotIn("title_and_abstract", dict(default_calls[0])["filter"])
        self.assertEqual(summary["search_field"], "search")
        for call in ta_calls:
            params = dict(call)
            self.assertNotIn("search", params)
            self.assertEqual(params["filter"].split(","), ["from_publication_date:2020-01-01", "to_publication_date:2026-12-31",
                                                           "has_abstract:true", "title_and_abstract.search:q"])
        self.assertEqual(summary_ta["search_field"], "title_and_abstract")
        self.assertEqual(summary_ta["unique_records"], 3)
        ckpt = json.loads((self.dir / "ckpt.json").read_text(encoding="utf-8"))
        self.assertEqual(ckpt["queries"]["q"]["signature"]["search_field"], "title_and_abstract")

    def test_changing_the_search_field_needs_a_new_checkpoint(self):
        with fake_openalex() as handler:
            self.download(2, search_field="title_and_abstract")
            calls = len(handler.calls)
            with self.assertRaises(RuntimeError) as ctx:
                self.download(10)
            self.assertIn("Checkpoint parameters changed", str(ctx.exception))
            self.assertEqual(len(handler.calls), calls)
            self.download(2, no_resume=True)
        sig = json.loads((self.dir / "ckpt.json").read_text(encoding="utf-8"))["queries"]["q"]["signature"]
        self.assertNotIn("search_field", sig)  # the default keeps the 3.1 signature, so old checkpoints resume

    def test_title_and_abstract_query_with_comma_or_pipe_is_refused_before_any_request(self):
        self.assertIsNone(openalex.search_query_problem('malware AND ("concept drift" OR drift)', "title_and_abstract"))
        self.assertIsNone(openalex.search_query_problem("drift, malware | x", "search"))
        self.assertIn("','", openalex.search_query_problem("drift, malware", "title_and_abstract"))
        self.assertIn("'|'", openalex.search_query_problem("drift | malware", "title_and_abstract"))
        self.assertIn("unknown search field", openalex.search_query_problem("q", "fulltext"))
        with fake_openalex() as handler:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError) as ctx:
                openalex.download_queries(["fine", "drift, malware"], 2020, 2026, self.FAKE_KEY, "", self.dir / "raw.jsonl",
                                          self.dir / "ckpt.json", search_field="title_and_abstract")
            self.assertEqual(handler.calls, [])
        self.assertIn("drift, malware", str(ctx.exception))
        self.assertFalse((self.dir / "ckpt.json").exists())
        self.assertFalse((self.dir / "raw.jsonl").exists())


class TestIssnConflict(RepoCase):
    """v3.0.5: a title match must never override contradictory ISSN evidence (generic, fixture-driven)."""

    def one(self, row, categories=None):
        rows, out = self.enrich([row], categories)
        return rows[row["openalex_id"]], out

    def test_1_6_latest_title_with_wrong_issn_does_not_match(self):
        row, out = self.one(paper("c1", 2021, "Alpha Journal", "9999-9999", "9999-9999"))
        self.assertEqual(row["source_status"], "UNMATCHED")
        self.assertEqual(row["latest_match_method"], "TITLE_MATCH_ISSN_CONFLICT")
        self.assertEqual(row["latest_match_candidates"], "100")
        self.assertEqual(row["scimago_sourceid"], "")
        self.assertNotIn("sjr_latest", row)
        self.assertNotIn("best_quartile_latest", row)
        self.assertEqual((row["sjr_at_publication"], row["best_quartile_at_publication"]), (None, ""))
        self.assertEqual(row["ranking_basis"], "UNAVAILABLE")
        self.assertEqual(row["publication_match_method"], "TITLE_MATCH_ISSN_CONFLICT")
        self.assertEqual(row["historical_match_method"], "ANY_EARLIER_SNAPSHOT_TITLE_MATCH_ISSN_CONFLICT")
        self.assertEqual((row["match_diagnostic"], row["issn_conflict_candidates"]), ("ISSN_CONFLICT", "100"))
        main, _ = apply_main_policy(out, self.manifest, source_types=[], include_historical_only=True)
        self.assertEqual(main, [])
        audit = source_audit(out)[0]
        self.assertEqual((audit["latest_match_method"], audit["match_diagnostic"]), ("TITLE_MATCH_ISSN_CONFLICT", "ISSN_CONFLICT"))
        self.assertEqual(build_summary(out, [], [])["match_diagnostic_counts"], {"ISSN_CONFLICT": 1})

    def test_2_title_without_issn_still_matches(self):
        row, _ = self.one(paper("c2", 2021, "Alpha Journal", "", ""))
        self.assertEqual((row["source_status"], row["latest_match_method"], row["scimago_sourceid"]), ("ACTIVE_LATEST", "TITLE_EXACT", "100"))
        self.assertEqual(row["match_diagnostic"], "")

    def test_3_issn_wins_over_different_title(self):
        row, _ = self.one(paper("c3", 2021, "Alpha Journal (Online Edition)", "1111-1111", ""))
        self.assertEqual((row["source_status"], row["latest_match_method"], row["scimago_sourceid"]), ("ACTIVE_LATEST", "ISSN_EXACT", "100"))
        self.assertEqual(row["best_quartile_at_publication"], "Q2")

    def test_4_discontinued_title_with_correct_issn_is_historical(self):
        row, _ = self.one(paper("c4", 2021, "Hist Journal", "2222-222X", ""))
        self.assertEqual((row["source_status"], row["scimago_sourceid_historical"]), ("HISTORICAL_ONLY", "200"))
        self.assertEqual(row["match_diagnostic"], "")

    def test_5_historical_title_with_wrong_issn_does_not_match(self):
        for year in (2021, 2026):
            row, _ = self.one(paper(f"c5-{year}", year, "Hist Journal", "9999-9999", ""))
            self.assertEqual(row["source_status"], "UNMATCHED", year)
            self.assertNotIn("scimago_sourceid_historical", row)
            self.assertEqual(row["historical_match_method"], "ANY_EARLIER_SNAPSHOT_TITLE_MATCH_ISSN_CONFLICT")
            self.assertEqual(row["historical_match_candidates"], "200")
            self.assertEqual((row["best_quartile_at_publication"], row["ranking_basis"]), ("", "UNAVAILABLE"))
            self.assertEqual(row["match_diagnostic"], "ISSN_CONFLICT")

    def test_7_candidate_without_issn_is_not_a_conflict(self):
        row, _ = self.one(paper("c7", 2021, "NoIssn Proceedings", "9999-9999", "9999-9999", stype="conference"))
        self.assertEqual((row["source_status"], row["latest_match_method"], row["scimago_sourceid"]), ("ACTIVE_LATEST", "TITLE_EXACT", "910"))
        self.assertEqual(row["match_diagnostic"], "")

    def test_8_ambiguous_title_is_never_resolved_silently(self):
        row, _ = self.one(paper("c8", 2023, "Twin Journal.", "", ""))
        self.assertEqual((row["source_status"], row["latest_match_method"], row["latest_match_candidates"]), ("AMBIGUOUS", "TITLE_NORMALIZED", "920; 921"))
        # With an ISSN, the contradicting candidate is excluded; the remaining one (no ISSN) is unique.
        row, _ = self.one(paper("c8b", 2023, "Twin Journal.", "9999-9999", ""))
        self.assertEqual((row["source_status"], row["scimago_sourceid"]), ("ACTIVE_LATEST", "921"))
        self.assertEqual((row["match_diagnostic"], row["issn_conflict_candidates"]), ("ISSN_CONFLICT", "920"))

    def test_ampersand_title_matches_and_title(self):
        row, _ = self.one(paper("c10", 2023, "Alpha & Beta Review", "", ""))
        self.assertEqual((row["source_status"], row["latest_match_method"], row["scimago_sourceid"]), ("ACTIVE_LATEST", "TITLE_NORMALIZED", "930"))
        row, _ = self.one(paper("c10b", 2023, "Alpha & Beta Review", "9999-9999", ""))
        self.assertEqual((row["source_status"], row["latest_match_method"]), ("UNMATCHED", "TITLE_MATCH_ISSN_CONFLICT"))

    def test_invalid_issn_is_not_identifier_evidence(self):
        row, _ = self.one(paper("c9", 2021, "Alpha Journal", "12-34", ""))
        self.assertEqual((row["source_status"], row["latest_match_method"]), ("ACTIVE_LATEST", "TITLE_EXACT"))

    def test_inspect_source_applies_the_same_rule(self):
        match, history, _, status = source_history(self.dir, self.manifest, "Alpha Journal", "9999-9999")
        self.assertEqual((status, match.method, match.candidates, history), ("UNMATCHED", "TITLE_MATCH_ISSN_CONFLICT", ("100",), []))
        match, _, _, status = source_history(self.dir, self.manifest, "Hist Journal", "9999-9999")
        self.assertEqual((status, match.method, match.candidates), ("UNMATCHED", "TITLE_MATCH_ISSN_CONFLICT", ("200",)))
        match, _, _, status = source_history(self.dir, self.manifest, "Alpha Journal", "1111-1111")
        self.assertEqual((status, match.sourceid, match.method), ("ACTIVE_LATEST", "100", "ISSN_EXACT"))
        match, _, _, status = source_history(self.dir, self.manifest, "Hist Journal")
        self.assertEqual((status, match.sourceid), ("HISTORICAL_ONLY", "200"))

    def test_no_personal_email_or_key_in_source(self):
        import re
        sources = [LEGACY_REFERENCE / "literature_pipeline.py", LEGACY_REFERENCE / "config.example.json", *sorted((ROOT / "reprovane").glob("*.py"))]
        emails = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        for path in sources:
            text = path.read_text(encoding="utf-8")
            found = [e for e in emails.findall(text) if not e.endswith(("example.org", "example.com"))]
            self.assertEqual(found, [], path.name)
        self.assertEqual(json.loads((LEGACY_REFERENCE / "config.example.json").read_text(encoding="utf-8"))["openalex"], {"api_key": "", "email": ""})


_COPY_COUNTER = [0]


def make_project_copy(base: Path, config=None) -> Path:
    """Fresh copy of the CLI (script + reprovane, never an existing config.json); optional config.json."""
    import shutil
    proj = base / f"proj{_COPY_COUNTER[0]}"
    _COPY_COUNTER[0] += 1
    proj.mkdir(parents=True)
    shutil.copy2(LEGACY_REFERENCE / "literature_pipeline.py", proj / "literature_pipeline.py")
    shutil.copytree(ROOT / "reprovane", proj / "reprovane", ignore=shutil.ignore_patterns("__pycache__"))
    if config is not None:
        text = config if isinstance(config, str) else json.dumps(config)
        (proj / "config.json").write_text(text, encoding="utf-8")
    return proj


def load_cli(proj: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"lp_copy_{proj.name}", proj / "literature_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(proj: Path, argv: List[str]):
    from unittest import mock
    cli = load_cli(proj)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:  # argparse errors
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class TestConfig(unittest.TestCase):
    FAKE_KEY = "CFGKEY_NEVER_PRINT_987654"

    def setUp(self):
        from unittest import mock
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repo = self.base / "scimago_remote"
        self.manifest = build_repo(self.repo)
        # No ambient credential may leak into these tests.
        self.env = mock.patch.dict("os.environ", {}, clear=False)
        self.env.start()
        import os
        os.environ.pop("OPENALEX_API_KEY", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def parsed(self, proj, argv):
        cli = load_cli(proj)
        args = cli.build_parser().parse_args(argv)
        from reprovane.config import load_config
        config = load_config(args.config, proj)
        sources = cli.resolve_settings(args, config)
        return args, sources

    def test_without_config_file_defaults_apply_and_offline_commands_work(self):
        proj = make_project_copy(self.base)
        args, sources = self.parsed(proj, ["run", "--query", "x"])
        self.assertEqual((args.from_year, args.to_year, args.require_abstract, args.max_per_query), (2010, 2026, False, None))
        self.assertEqual(args.cache_dir, Path("data") / "scimago")
        self.assertEqual(sources["cache_dir"], "default")
        self.assertEqual(sources["api_key"], "not configured")
        code, out, _ = run_cli(proj, ["validate-repo", "--offline", "--cache-dir", str(self.repo)])
        self.assertEqual(code, 0)
        self.assertIn("Config: none", out)
        code, out, _ = run_cli(proj, ["inspect-source", "--offline", "--cache-dir", str(self.repo), "--title", "Alpha Journal"])
        self.assertEqual(code, 0)
        self.assertIn("source_status: ACTIVE_LATEST", out)

    def test_run_without_key_fails_fast_with_clear_message(self):
        proj = make_project_copy(self.base, {"openalex": {"api_key": ""}})
        cache = self.base / "must_not_be_created"
        with fake_openalex() as handler:
            code, out, err = run_cli(proj, ["run", "--query", "x", "--cache-dir", str(cache), "--output", str(self.base / "o" / "x")])
            self.assertEqual(handler.calls, [])
        self.assertEqual(code, 2)
        self.assertIn("needs an OpenAlex API key", err)
        self.assertIn("config.json", err)
        self.assertFalse(cache.exists())
        self.assertFalse((self.base / "o").exists())
        proj_none = make_project_copy(self.base)
        code, _, err = run_cli(proj_none, ["run", "--query", "x", "--cache-dir", str(cache)])
        self.assertEqual(code, 2)
        self.assertIn("needs an OpenAlex API key", err)

    def test_precedence_cli_over_config_over_environment_over_default(self):
        import os
        cfg = {"openalex": {"api_key": self.FAKE_KEY, "email": "cfg@example.org"},
               "scimago": {"cache_dir": "cache/scimago"}, "run": {"from_year": 2015, "require_abstract": True}}
        proj = make_project_copy(self.base, cfg)
        os.environ["OPENALEX_API_KEY"] = "ENVKEY_SHOULD_LOSE_123"
        args, sources = self.parsed(proj, ["run", "--query", "x", "--from-year", "2018"])
        self.assertEqual((args.from_year, sources["from_year"]), (2018, "cli"))
        self.assertEqual((args.to_year, sources["to_year"]), (2026, "default"))
        self.assertEqual((args.require_abstract, sources["require_abstract"]), (True, "config.json"))
        self.assertEqual((args.cache_dir, sources["cache_dir"]), (proj / "cache" / "scimago", "config.json"))
        self.assertEqual((args.email, args.api_key, args.api_key_source), ("cfg@example.org", self.FAKE_KEY, "config.json"))
        args, _ = self.parsed(proj, ["run", "--query", "x", "--api-key", "CLIKEY_WINS_456", "--cache-dir", "elsewhere"])
        self.assertEqual((args.api_key_source, args.cache_dir), ("cli", Path("elsewhere")))
        proj_env = make_project_copy(self.base, {"openalex": {"api_key": ""}})
        args, _ = self.parsed(proj_env, ["run", "--query", "x"])
        self.assertEqual((args.api_key, args.api_key_source), ("ENVKEY_SHOULD_LOSE_123", "environment"))

    def test_run_with_config_key_end_to_end_and_key_never_written(self):
        with serve(self.repo) as (base_url, _), fake_openalex() as handler:
            self.manifest["base_url"] = base_url
            (self.repo / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
            cfg = {"openalex": {"api_key": self.FAKE_KEY, "email": ""},
                   "scimago": {"manifest_url": base_url + "manifest.json", "cache_dir": "cache/scimago"},
                   "run": {"max_per_query": 2}}
            proj = make_project_copy(self.base, cfg)
            out_prefix = self.base / "out" / "x"
            code, out, err = run_cli(proj, ["run", "--query", "q", "--output", str(out_prefix)])
            self.assertEqual(code, 0, err)
            self.assertTrue(handler.calls)
            self.assertTrue(all(self.FAKE_KEY in c for c in handler.calls))  # the key really was sent to OpenAlex
        self.assertTrue((proj / "cache" / "scimago" / "scimagojr-2023.csv").exists())
        meta = json.loads(Path(str(out_prefix) + "_run_metadata.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["openalex_api_key_configured"], meta["openalex_api_key_source"]), (True, "config.json"))
        self.assertIs(meta["openalex_api_key_stored_in_output"], False)
        self.assertNotIn("api_key_configured", meta)  # v3.0.4 names replaced, not duplicated
        self.assertEqual(meta["openalex_email"], "")  # no built-in personal default
        self.assertEqual(Path(meta["config_file"]).resolve(), (proj / "config.json").resolve())
        self.assertEqual(meta["settings_sources"]["cache_dir"], "config.json")
        self.assertEqual(meta["max_per_query"], 2)
        self.assertIn("api_key: configured (source: config.json; value not shown)".replace("api_key", "OpenAlex API key"), out)
        self.assertNotIn(self.FAKE_KEY, out + err)
        for path in (self.base / "out").iterdir():
            self.assertNotIn(self.FAKE_KEY, path.read_text(encoding="utf-8-sig"), path.name)

    def test_invalid_config_is_rejected_without_echoing_values(self):
        planted = "PLANTED_VALUE_NEVER_ECHO_55"
        cases = [
            ('{"openalex": {"api_key": "' + planted + '",}}', "invalid JSON"),
            (json.dumps({"openalex": {planted: "x"}}), "unknown key in section 'openalex'"),
            (json.dumps({planted: {}}), "unknown top-level section"),
            (json.dumps({"run": {"from_year": "2015"}}), "'run.from_year' must be an integer"),
            (json.dumps({"run": {"from_year": True}}), "'run.from_year' must be an integer"),
            (json.dumps({"openalex": {"api_key": 12345}}), "'openalex.api_key' must be a string"),
            (json.dumps({"run": {"search_field": planted}}), "'run.search_field' must be one of: search, title_and_abstract"),
            (json.dumps([1, 2]), "top level must be a JSON object"),
        ]
        for text, expected in cases:
            proj = make_project_copy(self.base, text)
            code, out, err = run_cli(proj, ["validate-repo", "--offline", "--cache-dir", str(self.repo)])
            self.assertEqual(code, 2, expected)
            self.assertIn(expected, err)
            self.assertNotIn(planted, out + err)
        proj = make_project_copy(self.base)
        code, _, err = run_cli(proj, ["validate-repo", "--config", str(self.base / "missing.json"), "--offline"])
        self.assertEqual(code, 2)
        self.assertIn("Config file not found", err)

    def test_example_config_is_valid_and_has_no_key(self):
        from reprovane.config import load_config
        example = json.loads((LEGACY_REFERENCE / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(example["openalex"]["api_key"], "")
        proj = make_project_copy(self.base, (LEGACY_REFERENCE / "config.example.json").read_text(encoding="utf-8"))
        config = load_config(None, proj)
        self.assertEqual(config.get("openalex", "api_key"), None)
        self.assertIn("config.json", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())

    def test_search_field_precedence_and_run_end_to_end(self):
        proj = make_project_copy(self.base, {"run": {"search_field": "title_and_abstract"}})
        args, sources = self.parsed(proj, ["run", "--query", "x"])
        self.assertEqual((args.search_field, sources["search_field"]), ("title_and_abstract", "config.json"))
        args, sources = self.parsed(proj, ["run", "--query", "x", "--search-field", "search"])
        self.assertEqual((args.search_field, sources["search_field"]), ("search", "cli"))
        args, sources = self.parsed(make_project_copy(self.base, {"run": {"search_field": ""}}), ["run", "--query", "x"])
        self.assertEqual((args.search_field, sources["search_field"]), ("search", "default"))
        code, _, err = run_cli(proj, ["run", "--query", "x", "--search-field", "fulltext"])
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

        with serve(self.repo) as (base_url, _), fake_openalex() as handler:
            self.manifest["base_url"] = base_url
            (self.repo / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
            cfg = {"openalex": {"api_key": self.FAKE_KEY},
                   "scimago": {"manifest_url": base_url + "manifest.json", "cache_dir": "cache/scimago"}}
            proj = make_project_copy(self.base, cfg)
            cache = proj / "cache" / "scimago"
            code, _, err = run_cli(proj, ["run", "--query", "q, r", "--search-field", "title_and_abstract",
                                          "--output", str(self.base / "bad" / "x")])
            self.assertEqual(code, 2)
            self.assertIn("ERROR (query, --search-field title_and_abstract)", err)
            self.assertEqual(handler.calls, [])
            self.assertFalse(cache.exists())
            self.assertFalse((self.base / "bad").exists())
            out_prefix = self.base / "out" / "x"
            code, out, err = run_cli(proj, ["run", "--query", "q", "--search-field", "title_and_abstract",
                                            "--output", str(out_prefix)])
            self.assertEqual(code, 0, err)
            self.assertTrue(handler.calls)
            from urllib.parse import parse_qsl, urlparse
            for call in handler.calls:
                params = dict(parse_qsl(urlparse(call).query))
                self.assertNotIn("search", params)
                self.assertTrue(params["filter"].endswith(",title_and_abstract.search:q"), params["filter"])
        self.assertIn("OpenAlex search field: title_and_abstract (title and abstract only)", out)
        meta = json.loads(Path(str(out_prefix) + "_run_metadata.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["search_field"], meta["settings_sources"]["search_field"]), ("title_and_abstract", "cli"))
        summary = json.loads(Path(str(out_prefix) + "_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["openalex"]["search_field"], "title_and_abstract")


PDF_BYTES = b"%PDF-1.4\n" + b"0" * 2000 + b"\n%%EOF\n"
TITLE_CHALLENGE = (403, {"Content-Type": "text/html"},
                   b"<html><head><title>Just a moment...</title></head><body>checking</body></html>")
HEADER_CHALLENGE = (403, {"Content-Type": "text/html", "cf-mitigated": "challenge"}, b"<html><body>wait</body></html>")


class _RouteHandler(http.server.BaseHTTPRequestHandler):
    routes: Dict[str, tuple] = {}
    hits: List[str] = []
    agents: List[str] = []

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        type(self).agents.append(self.headers.get("User-Agent", ""))
        status, headers, body = type(self).routes.get(self.path, (404, {"Content-Type": "text/plain"}, b"not found"))
        self.send_response(status)
        for key, value in headers.items():
            if key != "Content-Length":
                self.send_header(key, value)
        length = headers.get("Content-Length", str(len(body)))
        if length != "omit":
            self.send_header("Content-Length", length)
        self.end_headers()
        self.wfile.write(body)


@contextlib.contextmanager
def route_server(routes: Dict[str, tuple]):
    handler = type("R", (_RouteHandler,), {"routes": dict(routes), "hits": [], "agents": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", handler
    finally:
        server.shutdown()
        server.server_close()


def oa_row(pid, pdf="", oa="", is_oa=True, landing=""):
    return {"openalex_id": pid, "doi": f"10.9/{pid.lower()}" if pid else "", "title": f"Paper {pid}",
            "publication_year": 2024, "source": "Alpha Journal", "authors": "Doe, J.; Roe, R.", "is_open_access": is_oa,
            "oa_status": "gold" if is_oa else "closed", "best_oa_pdf_url": pdf, "oa_url": oa,
            "best_oa_landing_page": landing}


class TestFetchOpenAccess(unittest.TestCase):
    """fetch-oa: polite download, bot-checked hosts are never requested again, the rest goes to the manual list."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.out = self.dir / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def write_corpus(self, rows, name="x_main.csv") -> Path:
        path = self.dir / name
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def fetch(self, path, **kw):
        from reprovane.fulltext import fetch_open_access
        kw.setdefault("delay", 0)
        with contextlib.redirect_stdout(io.StringIO()):
            return fetch_open_access(path, self.out, verbose=True, **kw)

    def manual(self):
        return {r["paper"]: r for r in read_csv(self.out / "manual_download.csv")}

    def blocked(self):
        return json.loads((self.out / "blocked_hosts.json").read_text(encoding="utf-8"))

    @contextlib.contextmanager
    def hosts(self):
        with route_server({}) as (b, hb), route_server({}) as (c, hc), route_server({}) as (a, ha):
            ha.routes.update({
                "/p1.pdf": (200, {"Content-Type": "application/pdf"}, PDF_BYTES),
                "/p5.pdf": (200, {"Content-Type": "application/octet-stream"}, PDF_BYTES),
                "/landing.html": (200, {"Content-Type": "text/html"}, b"<html><title>Article</title>" + b" " * 30000),
                "/to-b": (302, {"Location": f"{b}/b2.pdf"}, b""),
                "/closed.pdf": (200, {}, PDF_BYTES),
            })
            hb.routes.update({"/b1.pdf": TITLE_CHALLENGE, "/b2.pdf": (200, {}, PDF_BYTES), "/b3.pdf": (200, {}, PDF_BYTES)})
            hc.routes.update({
                "/robots.txt": (200, {"Content-Type": "text/plain"}, b"User-agent: *\nAllow: /\nDisallow: /private/\n"),
                "/private/c1.pdf": (200, {}, PDF_BYTES),
                "/c2.pdf": (429, {"Retry-After": "3600"}, b"slow down"),
            })
            yield (a, ha), (b, hb), (c, hc)

    def standard_corpus(self, a, b, c) -> Path:
        return self.write_corpus([
            oa_row("W1", f"{a}/p1.pdf"),                       # downloaded
            oa_row("W2", f"{b}/b1.pdf"),                       # challenge page: host b blocked for good
            oa_row("W3", f"{b}/b3.pdf"),                       # host b already blocked: never requested
            oa_row("W4", f"{a}/to-b"),                         # redirect into blocked host b: not followed
            oa_row("W5", f"{a}/landing.html", f"{a}/p5.pdf"),  # not a PDF, then the second OA link works
            oa_row("W6", f"{c}/private/c1.pdf"),               # robots.txt disallows the path (Allow: / comes first)
            oa_row("W7", f"{c}/c2.pdf"),                       # HTTP 429: host c paused for this run only
            oa_row("W8", is_oa=False),                         # closed access: manual list, no request
            oa_row("W9", f"{a}/gone.pdf"),                     # HTTP 404
            oa_row("W10", f"{a}/closed.pdf", is_oa=False),     # marked closed although a link is given: no request
            oa_row("W1", f"{a}/p1.pdf"),                       # duplicate record
        ])

    def test_bot_checked_hosts_are_never_requested_again(self):
        from reprovane.fulltext import USER_AGENT, host_of
        with self.hosts() as ((a, ha), (b, hb), (c, hc)):
            path = self.standard_corpus(a, b, c)
            summary = self.fetch(path)
            self.assertEqual((summary["papers"], summary["downloaded"], summary["manual_list"]), (10, 2, 8))
            for pid in ("W1", "W5"):
                self.assertEqual((self.out / "pdf" / f"{pid}.pdf").read_bytes(), PDF_BYTES)
            reasons = {k: v["reason"] for k, v in self.manual().items()}
            self.assertEqual(reasons, {"W2": "BOT_CHECK", "W3": "HOST_BLOCKED", "W4": "HOST_BLOCKED",
                                       "W6": "ROBOTS_DISALLOWED", "W7": "RATE_LIMITED", "W8": "NOT_OPEN_ACCESS",
                                       "W9": "HTTP_404", "W10": "NOT_OPEN_ACCESS"})
            self.assertEqual(hb.hits, ["/robots.txt", "/b1.pdf"])   # neither b3 nor the redirect target b2
            self.assertEqual(hc.hits, ["/robots.txt", "/c2.pdf"])   # the disallowed path was never requested
            self.assertNotIn("/closed.pdf", ha.hits)
            self.assertEqual(set(ha.agents + hb.agents + hc.agents), {USER_AGENT})
            self.assertEqual(sorted(self.blocked()), [host_of(b)])
            self.assertIn("Just a moment", self.blocked()[host_of(b)]["reason"])
            self.assertEqual(summary["paused_hosts_this_run"], [host_of(c)])
            self.assertIn("W3.pdf", (self.out / "manual_download.html").read_text(encoding="utf-8"))
            ris = (self.out / "manual_download.ris").read_text(encoding="utf-8")
            self.assertEqual(ris.count("TY  - "), 8)
            self.assertIn("DO  - 10.9/w8", ris)
            answered = [r for r in read_csv(self.out / "fetch_log.csv") if r["http_status"]]
            self.assertEqual(len(answered), summary["requests_sent_this_run"])   # every request is in the log

            # Second run: only the rate-limited paper is tried again (one robots.txt + one PDF request).
            before_a, before_b = list(ha.hits), list(hb.hits)
            summary = self.fetch(path)
            self.assertEqual(summary["requests_sent_this_run"], 2)
            self.assertEqual((ha.hits, hb.hits), (before_a, before_b))
            self.assertEqual(hc.hits[-2:], ["/robots.txt", "/c2.pdf"])

            # A PDF saved by hand in pdf_manual/ is registered and leaves the manual list.
            (self.out / "pdf_manual" / "W3.pdf").write_bytes(PDF_BYTES)
            summary = self.fetch(path)
            self.assertEqual((summary["manually_downloaded"], summary["manual_list"]), (1, 7))
            self.assertNotIn("W3", self.manual())
            self.assertEqual(hb.hits, ["/robots.txt", "/b1.pdf"])

            # Removing a host from blocked_hosts.json is the only way to try it again.
            (self.out / "blocked_hosts.json").write_text("{}", encoding="utf-8")
            self.fetch(path)
            self.assertEqual(hb.hits.count("/b1.pdf"), 2)
            log = read_csv(self.out / "fetch_log.csv")
            self.assertEqual(sum(1 for r in log if r["outcome"] == "BOT_CHECK" and r["paper"] == "W2"), 2)

    def test_limit_and_retry_failed(self):
        with self.hosts() as ((a, ha), (b, hb), (c, hc)):
            path = self.standard_corpus(a, b, c)
            summary = self.fetch(path, limit=1)
            self.assertEqual(summary["downloaded"], 1)
            self.assertEqual(self.manual()["W2"]["reason"], "NOT_ATTEMPTED")
            self.fetch(path)
            self.assertEqual(ha.hits.count("/gone.pdf"), 1)
            self.fetch(path)
            self.assertEqual(ha.hits.count("/gone.pdf"), 1)            # permanent failure not retried
            self.fetch(path, retry_failed=True)
            self.assertEqual(ha.hits.count("/gone.pdf"), 2)            # ... unless asked
            self.assertEqual(hb.hits.count("/b3.pdf"), 0)              # a blocked host stays untouched

    def test_bot_check_detection(self):
        from reprovane.fulltext import detect_bot_check
        big = b" " * 30000
        self.assertEqual(detect_bot_check(200, {}, b"<title>Article</title><script src='https://js.captcha-delivery.com/t.js'>" + big), "")
        self.assertEqual(detect_bot_check(200, {}, b"<title>Breaking text CAPTCHA with deep learning</title>"), "")
        self.assertEqual(detect_bot_check(200, {}, b"<title>Social bot detection on Twitter: a survey</title>"), "")
        self.assertEqual(detect_bot_check(404, {}, b"<title>Not found</title>"
                                           b"<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script>"), "")
        self.assertIn("Client Challenge", detect_bot_check(200, {}, b"<html><title>Client Challenge</title></html>"))
        self.assertIn("Attention Required", detect_bot_check(403, {}, b"<title>Attention Required! | Cloudflare</title>"))
        self.assertIn("challenge-platform/h/", detect_bot_check(503, {}, b"<script src='/cdn-cgi/challenge-platform/h/b/x'>"))
        self.assertIn("Cloudflare challenge", detect_bot_check(*HEADER_CHALLENGE))
        self.assertEqual(detect_bot_check(403, {}, b""), "HTTP 403 Forbidden")
        self.assertEqual(detect_bot_check(403, {}, b"", plain_403=False), "")
        self.assertIn("tdm-reservation", detect_bot_check(200, {"TDM-Reservation": "1"}, b""))
        self.assertEqual(detect_bot_check(404, {}, b"<html><title>Page not found</title></html>"), "")
        self.assertEqual(detect_bot_check(429, {}, b"slow down"), "")    # a rate limit, handled separately

    def test_robots_rules_follow_rfc_9309(self):
        from reprovane.fulltext import Robots
        rules = Robots("User-agent: *\nAllow: /\nDisallow: /private/\n")
        self.assertFalse(rules.allowed("http://h/private/x.pdf"))
        self.assertTrue(rules.allowed("http://h/public/x.pdf"))
        rules = Robots("User-agent: *\nDisallow: /*.pdf$\n")
        self.assertFalse(rules.allowed("http://h/a/b.pdf"))
        self.assertTrue(rules.allowed("http://h/a/b.pdf?download=1"))
        self.assertTrue(rules.allowed("http://h/a/b.html"))
        rules = Robots("User-agent: Reprovane\nDisallow: /\n\nUser-agent: *\nAllow: /\nCrawl-delay: 7\n")
        self.assertFalse(rules.allowed("http://h/x.pdf"))
        self.assertTrue(rules.allowed("http://h/robots.txt"))
        self.assertTrue(Robots("User-agent: *\nDisallow:\n").allowed("http://h/x.pdf"))
        self.assertEqual(Robots("User-agent: *\nCrawl-delay: 5\n").crawl_delay(), 5.0)

    def test_robots_redirect_unreachable_and_crawl_delay(self):
        from reprovane import fulltext
        with route_server({}) as (d, hd), route_server({}) as (e, he):
            hd.routes.update({
                "/robots.txt": (301, {"Location": "/robots-real.txt"}, b""),
                "/robots-real.txt": (200, {}, b"User-agent: *\nDisallow: /private/\nCrawl-delay: 3\n"),
                "/private/x.pdf": (200, {}, PDF_BYTES), "/ok.pdf": (200, {}, PDF_BYTES), "/ok2.pdf": (200, {}, PDF_BYTES),
            })
            he.routes.update({"/robots.txt": (503, {}, b"down"), "/p.pdf": (200, {}, PDF_BYTES), "/q.pdf": (200, {}, PDF_BYTES)})
            path = self.write_corpus([oa_row("W1", f"{d}/private/x.pdf"), oa_row("W2", f"{e}/p.pdf"),
                                      oa_row("W3", f"{e}/q.pdf"), oa_row("W4", f"{d}/ok.pdf"), oa_row("W5", f"{d}/ok2.pdf")])
            sleeps = []
            original = fulltext.time.sleep
            fulltext.time.sleep = sleeps.append
            try:
                self.fetch(path, delay=1)
            finally:
                fulltext.time.sleep = original
            reasons = {k: v["reason"] for k, v in self.manual().items()}
            self.assertEqual(reasons, {"W1": "ROBOTS_DISALLOWED", "W2": "ROBOTS_UNREACHABLE", "W3": "ROBOTS_UNREACHABLE"})
            self.assertNotIn("/private/x.pdf", hd.hits)
            self.assertEqual(he.hits, ["/robots.txt"])                  # an unreadable robots.txt stops the host
            self.assertTrue(sleeps and max(sleeps) > 2.5)              # Crawl-delay 3 wins over --delay 1
            self.assertEqual(self.blocked(), {})                       # nothing here is a bot check

    def test_redirect_chains_and_rights_reservation(self):
        from reprovane import fulltext
        from reprovane.fulltext import host_of
        with route_server({}) as (f, hf), route_server({}) as (g, hg), route_server({}) as (h, hh), \
                route_server({}) as (r, hr), route_server({}) as (q, hq):
            hf.routes.update({"/gate": TITLE_CHALLENGE})
            hq.routes.update({"/gate": HEADER_CHALLENGE})
            hg.routes.update({"/a": (302, {"Location": f"{f}/gate"}, b""), "/b.pdf": (200, {}, PDF_BYTES)})
            hh.routes.update({"/t": (302, {"Location": f"{h}/t.pdf", "tdm-reservation": "1"}, b""), "/t.pdf": (200, {}, PDF_BYTES)})
            hr.routes.update({"/doi/1": (302, {"Location": f"{q}/gate"}, b""), "/doi/2": (200, {}, PDF_BYTES)})
            path = self.write_corpus([oa_row("W1", f"{g}/a"), oa_row("W2", f"{g}/b.pdf"), oa_row("W3", f"{h}/t"),
                                      oa_row("W4", f"{r}/doi/1"), oa_row("W5", f"{r}/doi/2")])
            original = set(fulltext.RESOLVER_HOSTS)
            fulltext.RESOLVER_HOSTS.add(host_of(r))
            try:
                summary = self.fetch(path)
            finally:
                fulltext.RESOLVER_HOSTS.clear()
                fulltext.RESOLVER_HOSTS.update(original)
            blocked = self.blocked()
            self.assertIn(host_of(g), blocked)                         # g redirected to a bot check
            self.assertIn(host_of(f), blocked)
            self.assertIn(host_of(q), blocked)                         # reached through the resolver
            self.assertIn("tdm-reservation", blocked[host_of(h)]["reason"])
            self.assertNotIn(host_of(r), blocked)                      # a resolver is never blocked
            self.assertEqual(hg.hits, ["/robots.txt", "/a"])           # W2 on g is not requested
            self.assertNotIn("/t.pdf", hh.hits)                        # the reserved redirect is not followed
            self.assertEqual(hr.hits, ["/robots.txt", "/doi/1", "/doi/2"])   # it only redirected: not paused
            reasons = {k: v["reason"] for k, v in self.manual().items()}
            self.assertEqual(reasons["W2"], "HOST_BLOCKED")
            self.assertNotIn("W5", reasons)
            self.assertEqual(summary["downloaded"], 1)

    def test_resolver_redirect_to_a_bot_check_does_not_pause_the_resolver(self):
        # Found in the first corpus run: a DOI that led to one publisher's bot check paused doi.org,
        # so every later DOI link of that run was skipped, including those of other publishers.
        from reprovane import fulltext
        from reprovane.fulltext import host_of
        with route_server({}) as (r, hr), route_server({}) as (p, hp), route_server({}) as (o, ho):
            hp.routes.update({"/gate": TITLE_CHALLENGE, "/other.pdf": (200, {}, PDF_BYTES)})
            ho.routes.update({"/ok.pdf": (200, {}, PDF_BYTES)})
            hr.routes.update({
                "/doi/1": (302, {"Location": f"{p}/gate"}, b""),        # publisher p serves a bot check
                "/doi/2": (302, {"Location": f"{o}/ok.pdf"}, b""),      # another publisher is still reached
                "/doi/3": (302, {"Location": f"{p}/other.pdf"}, b""),   # p is blocked: not requested
                "/doi/4": TITLE_CHALLENGE,                              # the resolver's own bot check pauses it
                "/doi/5": (302, {"Location": f"{o}/ok.pdf"}, b""),
            })
            path = self.write_corpus([oa_row(f"W{i}", f"{r}/doi/{i}") for i in range(1, 6)])
            original = set(fulltext.RESOLVER_HOSTS)
            fulltext.RESOLVER_HOSTS.add(host_of(r))
            try:
                summary = self.fetch(path)
            finally:
                fulltext.RESOLVER_HOSTS.clear()
                fulltext.RESOLVER_HOSTS.update(original)
            self.assertEqual(sorted(self.blocked()), [host_of(p)])
            self.assertEqual(hp.hits, ["/robots.txt", "/gate"])
            self.assertEqual(ho.hits, ["/robots.txt", "/ok.pdf"])
            self.assertEqual(hr.hits, ["/robots.txt", "/doi/1", "/doi/2", "/doi/3", "/doi/4"])
            reasons = {k: v["reason"] for k, v in self.manual().items()}
            self.assertEqual(reasons, {"W1": "BOT_CHECK", "W3": "HOST_BLOCKED", "W4": "BOT_CHECK", "W5": "HOST_PAUSED"})
            self.assertEqual(summary["downloaded"], 1)
            self.assertEqual(summary["paused_hosts_this_run"], [host_of(r)])

    def test_lower_case_header_names(self):
        # Found in the first live run: doi.org and others send "location" in lower case.
        with route_server({}) as (s, hs):
            hs.routes.update({
                "/robots.txt": (301, {"location": "/robots-real.txt"}, b""),
                "/robots-real.txt": (200, {}, b"User-agent: *\nDisallow: /private/\n"),
                "/r": (308, {"location": "/ok.pdf"}, b""), "/ok.pdf": (200, {}, PDF_BYTES),
                "/private/x.pdf": (200, {}, PDF_BYTES),
                "/busy.pdf": (503, {"retry-after": "60"}, b"busy"),
            })
            path = self.write_corpus([oa_row("W1", f"{s}/r"), oa_row("W2", f"{s}/private/x.pdf")])
            summary = self.fetch(path)
            self.assertEqual(summary["downloaded"], 1)
            self.assertEqual(self.manual()["W2"]["reason"], "ROBOTS_DISALLOWED")
            self.assertNotIn("/private/x.pdf", hs.hits)
        with route_server({}) as (s, hs):
            hs.routes.update({"/a.pdf": (503, {"retry-after": "60"}, b"busy"), "/b.pdf": (200, {}, PDF_BYTES)})
            self.out = self.dir / "out2"
            path = self.write_corpus([oa_row("W1", f"{s}/a.pdf"), oa_row("W2", f"{s}/b.pdf")], name="y_main.csv")
            self.fetch(path)
            self.assertEqual(hs.hits, ["/robots.txt", "/a.pdf"])     # lower-case Retry-After still pauses the host

    def test_interruption_keeps_blocked_hosts_and_state(self):
        from reprovane.fulltext import Fetcher, host_of
        with self.hosts() as ((a, ha), (b, hb), (c, hc)):
            path = self.standard_corpus(a, b, c)
            original = Fetcher.fetch

            def interrupt(fetcher, paper, url, destination):
                if paper == "W3":
                    raise KeyboardInterrupt
                return original(fetcher, paper, url, destination)

            Fetcher.fetch = interrupt
            try:
                with self.assertRaises(KeyboardInterrupt):
                    self.fetch(path)
            finally:
                Fetcher.fetch = original
            self.assertEqual(sorted(self.blocked()), [host_of(b)])
            papers = json.loads((self.out / "fetch_state.json").read_text(encoding="utf-8"))["papers"]
            self.assertEqual((papers["W1"]["status"], papers["W2"]["status"]), ("DOWNLOADED", "BOT_CHECK"))
            self.assertEqual(self.manual()["W3"]["reason"], "NOT_ATTEMPTED")
            self.fetch(path)
            self.assertEqual(hb.hits, ["/robots.txt", "/b1.pdf"])       # the block survived the interruption
            self.assertEqual(ha.hits.count("/p1.pdf"), 1)

    def test_broken_responses_do_not_crash_or_count_as_downloads(self):
        import socket
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead = f"http://127.0.0.1:{probe.getsockname()[1]}"
        n = len(PDF_BYTES)
        with route_server({}) as (s, hs):
            hs.routes.update({
                "/cut.pdf": (200, {"Content-Length": "5000"}, PDF_BYTES[:300]),
                "/noeof.pdf": (200, {"Content-Length": "omit"}, PDF_BYTES[:300]),
                "/dup.pdf": (200, {"Content-Length": f"{n}, {n}"}, PDF_BYTES),
                "/gone.pdf": (404, {}, b"no"),
            })
            with route_server({}) as (busy, hbusy):
                hbusy.routes.update({"/1.pdf": (503, {"Retry-After": "120"}, b"busy"), "/2.pdf": (200, {}, PDF_BYTES)})
                path = self.write_corpus([
                    oa_row("W1", f"{s}/cut.pdf"), oa_row("W2", f"{s}/noeof.pdf"), oa_row("W3", f"{s}/dup.pdf"),
                    oa_row("W4", f"{busy}/1.pdf"), oa_row("W5", f"{busy}/2.pdf"),
                    oa_row("W6", f"{dead}/x.pdf", f"{s}/gone.pdf"),
                ])
                summary = self.fetch(path)
                reasons = {k: v["reason"] for k, v in self.manual().items()}
                self.assertEqual(reasons, {"W1": "NETWORK_ERROR", "W2": "TRUNCATED", "W4": "HTTP_5XX", "W5": "HTTP_5XX",
                                           "W6": "ROBOTS_UNREACHABLE"})
                self.assertEqual(summary["downloaded"], 1)
                self.assertEqual(list((self.out / "pdf").glob("*.part")), [])
                self.assertEqual(hbusy.hits, ["/robots.txt", "/1.pdf"])  # Retry-After pauses the host for this run
                # Next run: only temporary failures are retried; the 404 link of W6 is not.
                self.fetch(path)
                self.assertEqual(hs.hits.count("/gone.pdf"), 1)
                self.assertEqual((hbusy.hits.count("/1.pdf"), hbusy.hits.count("/2.pdf")), (2, 0))
                self.assertEqual(self.blocked(), {})

    def test_manual_list_is_escaped_and_keeps_rows_without_identifier(self):
        from reprovane.fulltext import _say, host_of
        rows = [oa_row("W1", "javascript:alert(1)", landing="javascript:alert(2)"),
                oa_row("", "", is_oa=False)]
        rows[0]["title"] = "<script>alert(1)</script>\nsecond line"
        path = self.write_corpus(rows)
        summary = self.fetch(path)
        self.assertEqual(summary["manual_list"], 2)
        page = (self.out / "manual_download.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("javascript:", page)
        ris = (self.out / "manual_download.ris").read_text(encoding="utf-8")
        self.assertEqual(ris.count("TY  - "), 2)
        self.assertIn("TI  - <script>alert(1)</script> second line", ris)
        self.assertTrue(any(k.startswith("row_") for k in self.manual()))
        self.assertEqual(host_of("https://Example.org:443/a"), "example.org")
        self.assertEqual(host_of("http://example.org:8080/a"), "example.org:8080")
        stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        with contextlib.redirect_stdout(stream):
            _say("bot-check page 'Vui lòng chờ'")                    # must not raise on a non-UTF-8 console

    def test_cli_needs_no_key_and_validates_options(self):
        proj = make_project_copy(self.dir)
        with self.hosts() as ((a, ha), (b, hb), (c, hc)):
            path = self.standard_corpus(a, b, c)
            for bad in (["--delay", "0.5"], ["--timeout", "0"], ["--max-mb", "0"], ["--limit", "0"]):
                code, _, err = run_cli(proj, ["fetch-oa", "--input", str(path), *bad])
                self.assertEqual(code, 2, bad)
            code, out, err = run_cli(proj, ["fetch-oa", "--input", str(path), "--delay", "1"])
            self.assertEqual(code, 0, err)
            self.assertIn("FETCH-OA SUMMARY", out)
            self.assertIn("to download by hand: 8", out)
            self.assertIn("paused for this run only", out)
            self.assertTrue((self.dir / "x_main_fulltext" / "pdf" / "W1.pdf").exists())


VENUE_LIST = (
    "# example list; comment and blank lines are ignored\n"
    "tier\tvenue\topenalex_source_id\tissn\ttitle_contains\n"
    "\n"
    "A\tAlpha Journal\t\t1111-1111\t\n"
    "B\tHist Journal\thttps://openalex.org/S200\t2222-222x\t\n"
    "A\tACM CCS\t\t\tcomputer and communications security\n"
    "B\tACM AsiaCCS\t\t\tasia conference on computer and communications security\n"
    "B\tRAID\t\t\tresearch in attacks intrusions and defenses; raid\n"
    "C\tComputers & Security\t\t\tcomputers & security\n"
    "B\tShared One\t\t4444-4444\t\n"
    "C\tShared Two\t\t4444-4445\t\n"
)


class TestQualitySignals(RepoCase):
    def write_list(self, text, name="venues.tsv"):
        path = Path(self.tmp.name) / name
        path.write_text(text, encoding="utf-8-sig")
        return path

    def test_work_record_keeps_retraction_and_normalized_citations(self):
        work = {"id": "https://openalex.org/W9", "title": "T", "cited_by_count": 40, "fwci": 2.5, "is_retracted": True,
                "citation_normalized_percentile": {"value": 0.97, "is_in_top_1_percent": False, "is_in_top_10_percent": True}}
        record = openalex.work_to_record(work, "q")
        self.assertEqual((record["fwci"], record["citation_normalized_percentile"], record["citation_top_1_percent"],
                          record["citation_top_10_percent"], record["is_retracted"]), (2.5, 0.97, False, True, True))
        bare = openalex.work_to_record({"id": "https://openalex.org/W8", "title": "U"}, "q")
        self.assertEqual([bare[f] for f in ("fwci", "citation_normalized_percentile", "is_retracted")], [None, None, None])

    def test_merge_keeps_a_retraction_and_fills_missing_metrics(self):
        store = openalex.RecordStore()
        store.add({"openalex_id": "W1", "doi": "", "title": "A", "is_retracted": False, "fwci": None})
        store.add({"openalex_id": "W1", "doi": "", "title": "A", "is_retracted": True, "fwci": 1.2})
        store.add({"openalex_id": "W1", "doi": "", "title": "A", "is_retracted": False, "fwci": 9.9})
        record = next(iter(store.values()))
        self.assertEqual((record["is_retracted"], record["fwci"]), (True, 1.2))
        legacy = openalex.RecordStore()                    # a row written by v3.0.5 has no such keys
        legacy.add({"openalex_id": "W2", "doi": "", "title": "B"})
        legacy.add({"openalex_id": "W2", "doi": "", "title": "B", "is_retracted": False, "fwci": 0.4})
        record = next(iter(legacy.values()))
        self.assertEqual((record["is_retracted"], record["fwci"]), (False, 0.4))

    def test_exclude_retracted_is_opt_in_and_reported(self):
        rows = corpus()
        rows[0]["is_retracted"] = "True"                    # p01, ACTIVE_LATEST journal, as read back from CSV
        rows[1]["is_retracted"] = "False"
        _, out = self.enrich(rows)
        main, _ = apply_main_policy(out, self.manifest, source_types=["journal"])
        self.assertIn("p01", {r["openalex_id"] for r in main})
        _, out = self.enrich(rows)
        main, excluded = apply_main_policy(out, self.manifest, source_types=["journal"], exclude_retracted=True)
        self.assertNotIn("p01", {r["openalex_id"] for r in main})
        self.assertIn("RETRACTED", {r["openalex_id"]: r for r in excluded}["p01"]["main_exclusion_reasons"])
        summary = build_summary(out, main, excluded)
        self.assertEqual((summary["papers_retracted"], summary["papers_without_retraction_flag"]), (1, len(rows) - 2))
        self.assertNotIn("paper_venue_tier_counts", summary)

    def test_venue_matching_order_longest_phrase_and_ambiguity(self):
        tiers = VenueTiers.load(self.write_list(VENUE_LIST))
        self.assertEqual(len(tiers.venues), 8)
        match = lambda **row: tiers.match(row)  # noqa: E731
        self.assertEqual(match(source="Alpha Journal", issn_l="1111-1111"), ("A", "Alpha Journal", "issn"))
        # The source ID decides before the ISSN, and a lower-case x in the ISSN still matches.
        self.assertEqual(match(source_openalex_id="S200", issn_l="1111-1111"), ("B", "Hist Journal", "openalex_source_id"))
        self.assertEqual(match(source="Hist", issn="2222-222x"), ("B", "Hist Journal", "issn"))
        self.assertEqual(match(source="Proceedings of the 2017 ACM SIGSAC Conference on Computer and Communications Security"),
                         ("A", "ACM CCS", "title_contains"))
        self.assertEqual(match(source="Proceedings of the 2019 ACM Asia Conference on Computer and Communications Security"),
                         ("B", "ACM AsiaCCS", "title_contains"))
        self.assertEqual(match(source="Computers & Security")[:2], ("C", "Computers & Security"))
        self.assertEqual(match(source="Computers and Security")[:2], ("C", "Computers & Security"))
        self.assertEqual(match(source="RAID 2020")[:2], ("B", "RAID"))
        self.assertEqual(match(source="Raiders of Computing"), ("UNLISTED", "", ""))      # whole words only
        self.assertEqual(match(source="Unknown Venue", issn_l="9999-9999"), ("UNLISTED", "", ""))
        self.assertEqual(match(issn_l="4444-4444", issn="4444-4445"), ("AMBIGUOUS", "Shared One | Shared Two", "issn"))
        # A whole-name rule decides before phrases and does not catch longer names that start the same way.
        exact = VenueTiers.load(self.write_list(
            "tier\tvenue\ttitle_exact\ttitle_contains\n"
            "A\tICML\tInternational Conference on Machine Learning\t\n"
            "C\tAny machine learning venue\t\tmachine learning\n", "exact.tsv"))
        self.assertEqual(exact.match({"source": "International conference on machine learning"}),
                         ("A", "ICML", "title_exact"))
        self.assertEqual(exact.match({"source": "International Conference on Machine Learning and Cybernetics"}),
                         ("C", "Any machine learning venue", "title_contains"))

    def test_venue_list_errors_name_the_line(self):
        header = "tier\tvenue\tissn\ttitle_contains\n"
        bad = {
            "tier\tvenue\tisn\n": "line 1",                                          # misspelled column
            "tier\tvenue\n": "at least one of",
            header + "\tAlpha\t1111-1111\t\n": "line 2",                             # empty tier
            header + "UNLISTED\tAlpha\t1111-1111\t\n": "reserved",
            header + "A\tAlpha\t1111-11\t\n": "not a valid ISSN",
            header + "A\tAlpha\t\t\n": "give an openalex_source_id",
            header + "A\tAlpha\t1111-1111\t\nB\tBeta\t11111111\t\n": "line 3",       # same ISSN twice
            header + "A\tAlpha\t\talpha journal\nB\tBeta\t\tAlpha  Journal\n": "already listed",
            header + "A\tAlpha\t1111-1111\t\tstray\n": "more cells",
            header: "no venues",
            "tier\tvenue\topenalex_source_id\nA\tAlpha\tW123\n": "not an OpenAlex source ID",
        }
        for text, expected in bad.items():
            with self.assertRaises(VenueTierError, msg=text) as ctx:
                VenueTiers.load(self.write_list(text))
            self.assertIn(expected, str(ctx.exception), text)
        csv_list = self.write_list('tier,venue,issn\nA,"Alpha, the journal",1111-1111\n', "venues.csv")
        self.assertEqual(VenueTiers.load(csv_list).match({"issn": "1111-1111"})[:2], ("A", "Alpha, the journal"))

    def test_enrich_with_venue_list_end_to_end_and_reenrichment(self):
        tmp = Path(self.tmp.name)
        src = tmp / "in.csv"
        rows = corpus()
        rows[0]["is_retracted"] = True
        fields = list(rows[0])
        with src.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        venue_file = self.write_list(VENUE_LIST)
        proj = make_project_copy(tmp)
        code, out, err = run_cli(proj, ["enrich", "--offline", "--cache-dir", str(self.dir), "--input", str(src),
                                        "--output", str(tmp / "t"), "--venue-tiers", str(venue_file), "--exclude-retracted"])
        self.assertEqual(code, 0, err)
        self.assertIn("paper venue tier:", out)
        self.assertIn("retracted (OpenAlex is_retracted): 1 (excluded from the main corpus)", out)
        enriched = {r["openalex_id"]: r for r in read_csv(tmp / "t_enriched.csv")}
        self.assertEqual((enriched["p01"]["venue_tier"], enriched["p01"]["venue_tier_match"]), ("A", "issn"))
        self.assertEqual(enriched["p08"]["venue_tier"], "UNLISTED")
        self.assertEqual(enriched["p09"]["venue_tier"], "B")
        header = lambda name: (tmp / name).read_text(encoding="utf-8-sig").splitlines()[0]  # noqa: E731
        self.assertEqual(header("t_main.csv"), header("t_enriched.csv"))
        self.assertIn("venue_tier", header("t_source_audit.csv"))
        meta = json.loads((tmp / "t_run_metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["venue_tiers"]["sha256"], hashlib.sha256(venue_file.read_bytes()).hexdigest())
        self.assertTrue(meta["policy"]["exclude_retracted"])
        summary = json.loads((tmp / "t_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["paper_venue_tier_counts"]["A"], 4)             # p01, p02, p03, p16 (Alpha Journal)
        # Re-enriching the enriched file without a list drops the old tiers instead of keeping stale values.
        code, _, err = run_cli(proj, ["enrich", "--offline", "--cache-dir", str(self.dir),
                                      "--input", str(tmp / "t_enriched.csv"), "--output", str(tmp / "u")])
        self.assertEqual(code, 0, err)
        self.assertNotIn("venue_tier", header("u_enriched.csv"))
        self.assertNotIn("venue_tier", header("u_source_audit.csv"))

    def test_bad_venue_list_stops_before_any_network_access(self):
        tmp = Path(self.tmp.name)
        venue_file = self.write_list("tier\tvenue\n")
        proj = make_project_copy(tmp, {"openalex": {"api_key": "FAKEKEY_NEVER_PRINT_42"}})
        cache = tmp / "must_not_be_created"
        with fake_openalex() as handler:
            code, _, err = run_cli(proj, ["run", "--query", "x", "--cache-dir", str(cache), "--venue-tiers", str(venue_file)])
            self.assertEqual(handler.calls, [])
        self.assertEqual(code, 2)
        self.assertIn("ERROR (venue list)", err)
        self.assertFalse(cache.exists())
        code, _, err = run_cli(proj, ["enrich", "--cache-dir", str(cache), "--input", str(venue_file),
                                      "--output", str(tmp / "v"), "--venue-tiers", str(tmp / "missing.tsv")])
        self.assertEqual(code, 2)
        self.assertIn("cannot read the venue list", err)
        self.assertFalse(cache.exists())


MANIFEST_2025 = {"sjr": {"first_year": 2010, "last_year": 2025, "latest_snapshot_year": 2025},
                 "policy": {"require_present_in_latest_scimago_for_main_corpus": True}}


def screened_row(pid: str, year: int, status: str = "ACTIVE_LATEST", basis: str = "", q_pub: str = "",
                 q_latest: str = "Q1") -> Dict:
    """An enriched row as enrich_records writes it when the SCImago data end at 2025."""
    if not basis:
        basis = "LATEST_AVAILABLE" if year > 2025 and status == "ACTIVE_LATEST" else "EXACT_PUBLICATION_YEAR"
    return {"openalex_id": pid, "publication_year": str(year), "source": "Alpha Journal", "source_type": "journal",
            "source_status": status, "scimago_type_latest": "journal", "ranking_basis": basis,
            "best_quartile_at_publication": q_pub, "best_quartile_latest": q_latest,
            "relevant_category_quartile_at_publication": q_pub, "relevant_category_quartile_latest": q_latest}


class TestFutureYearQuartile(RepoCase):
    """A publication-basis quartile filter and papers published after the last SCImago year."""

    @staticmethod
    def screen(rows, **policy):
        main, excluded = apply_main_policy(rows, MANIFEST_2025, **policy)
        return main, excluded, quartile_filter_diagnostics(excluded, MANIFEST_2025, **policy)

    def test_a_future_year_paper_fails_publication_basis_with_a_precise_reason(self):
        for mode in ("best", "relevant-category"):
            row = screened_row("p2026", 2026)
            main, excluded, diag = self.screen([row], quartiles=["Q1"], quartile_basis="publication", quartile_mode=mode)
            self.assertEqual(main, [], mode)
            reasons = excluded[0]["main_exclusion_reasons"].split("; ")
            self.assertIn(FUTURE_YEAR_QUARTILE_REASON, reasons)
            self.assertIn("QUARTILE_UNAVAILABLE", reasons)  # kept for compatibility
            self.assertEqual((row["best_quartile_at_publication"], row["ranking_basis"]), ("", "LATEST_AVAILABLE"))
            self.assertEqual(diag["future_year_papers_without_publication_quartile"], 1)

    def test_b_same_paper_passes_with_latest_basis(self):
        for policy in ({"quartile_basis": "latest"}, {}):  # latest is also the default basis
            main, _, diag = self.screen([screened_row("p2026", 2026)], quartiles=["Q1"], **policy)
            self.assertEqual([(r["openalex_id"], r["main_exclusion_reasons"]) for r in main], [("p2026", "")], policy)
            self.assertEqual((diag["future_year_papers_without_publication_quartile"], diag["basis"]), (0, "latest"))

    def test_c_publication_year_within_scimago_passes_normally(self):
        main, _, diag = self.screen([screened_row("p2025", 2025, q_pub="Q1")], quartiles=["Q1"], quartile_basis="publication")
        self.assertEqual([(r["openalex_id"], r["main_exclusion_reasons"]) for r in main], [("p2025", "")])
        self.assertEqual((diag["future_year_papers_without_publication_quartile"], diag["scimago_latest_year"]), (0, 2025))

    def test_e_summary_counts_only_the_affected_papers(self):
        rows = [screened_row("a", 2026), screened_row("b", 2026, q_latest="Q2"), screened_row("c", 2027, q_latest=""),
                screened_row("d", 2026, status="HISTORICAL_ONLY", basis="UNAVAILABLE", q_latest=""),
                screened_row("e", 2025, q_pub="Q3"), screened_row("f", 2024)]
        main, excluded, diag = self.screen([dict(r) for r in rows], quartiles=["Q1"], quartile_basis="publication",
                                           include_historical_only=True)
        reasons = {r["openalex_id"]: r["main_exclusion_reasons"] for r in excluded}
        self.assertEqual(main, [])
        self.assertEqual(diag, {"quartile_filter": ["Q1"], "basis": "publication", "mode": "best",
                                "scimago_latest_year": 2025, "future_year_papers_without_publication_quartile": 3})
        self.assertEqual(sorted(k for k, v in reasons.items() if FUTURE_YEAR_QUARTILE_REASON in v), ["a", "b", "c"])
        self.assertEqual(reasons["d"], "QUARTILE_UNAVAILABLE")  # no latest metadata: the generic reason only
        self.assertEqual(reasons["e"], "QUARTILE_Q3_NOT_SELECTED")
        self.assertEqual(reasons["f"], "QUARTILE_UNAVAILABLE")  # within the SCImago years, no quartile assigned
        for policy in ({}, {"quartiles": ["Q1"], "quartile_basis": "latest"}):
            _, _, diag = self.screen([dict(r) for r in rows], include_historical_only=True, **policy)
            self.assertEqual(diag["future_year_papers_without_publication_quartile"], 0, policy)

    def test_d_cli_default_is_latest_and_warns_only_for_publication_basis(self):
        tmp = Path(self.tmp.name)
        src = tmp / "in.csv"
        rows = corpus()
        with src.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        proj = make_project_copy(tmp)
        runs = {}
        for name, extra in (("none", []), ("pub", ["--quartile", "Q1", "--quartile-basis", "publication"]),
                            ("default", ["--quartile", "Q1"])):
            code, out, err = run_cli(proj, ["enrich", "--offline", "--cache-dir", str(self.dir), "--input", str(src),
                                            "--output", str(tmp / name)] + extra)
            self.assertEqual(code, 0, err)
            summary = json.loads((tmp / f"{name}_summary.json").read_text(encoding="utf-8"))
            enriched = {r["openalex_id"]: r for r in read_csv(tmp / f"{name}_enriched.csv")}
            runs[name] = (out, summary["quartile_filter_diagnostics"], enriched)
        # The synthetic SCImago data end at 2023; p03 (latest Q1) and p13 (latest Q3) are 2026 papers of ACTIVE_LATEST sources.
        out, diag, enriched = runs["pub"]
        self.assertIn("WARNING: SCImago data ends at 2023. 2 papers published after 2023 have no publication-year quartile", out)
        self.assertEqual(diag["future_year_papers_without_publication_quartile"], 2)
        self.assertEqual(sorted(k for k, r in enriched.items() if FUTURE_YEAR_QUARTILE_REASON in r["main_exclusion_reasons"]),
                         ["p03", "p13"])
        self.assertEqual(enriched["p03"]["best_quartile_at_publication"], "")
        for name in ("none", "default"):
            out, diag, enriched = runs[name]
            self.assertNotIn("SCImago data ends at", out)
            self.assertEqual(diag["future_year_papers_without_publication_quartile"], 0)
            self.assertFalse(any(FUTURE_YEAR_QUARTILE_REASON in r["main_exclusion_reasons"] for r in enriched.values()))
        # The default basis is latest: the 2026 paper p03 (latest Q1) is kept, p13 (latest Q3) is not selected.
        default_rows = runs["default"][2]
        self.assertEqual(default_rows["p03"]["main_corpus"], "True")
        self.assertEqual(default_rows["p13"]["main_exclusion_reasons"], "QUARTILE_Q3_NOT_SELECTED")
        self.assertEqual(runs["none"][1], {"quartile_filter": [], "basis": "latest", "mode": "best",
                                           "scimago_latest_year": 2023, "future_year_papers_without_publication_quartile": 0})
        for name, basis in (("pub", "publication"), ("default", "latest")):
            meta = json.loads((tmp / f"{name}_run_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual((meta["scimago_last_year"], meta["policy"]["quartiles"], meta["policy"]["quartile_basis"]),
                             (2023, ["Q1"], basis))

    def test_run_prints_the_same_warning(self):
        tmp = Path(self.tmp.name)
        with serve(self.dir) as (base_url, _), fake_openalex():
            self.manifest["base_url"] = base_url
            (self.dir / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
            cfg = {"openalex": {"api_key": "FAKEKEY_NEVER_PRINT_42"},
                   "scimago": {"manifest_url": base_url + "manifest.json", "cache_dir": "cache/scimago"}}
            proj = make_project_copy(tmp, cfg)
            out_prefix = tmp / "run" / "x"
            code, out, err = run_cli(proj, ["run", "--query", "q", "--quartile", "Q1", "--quartile-basis", "publication",
                                            "--output", str(out_prefix)])
            default_prefix = tmp / "run" / "default"
            code_default, out_default, err_default = run_cli(proj, ["run", "--query", "q", "--quartile", "Q1",
                                                                    "--output", str(default_prefix)])
        self.assertEqual(code, 0, err)
        # the three fake OpenAlex works are 2024 papers in Alpha Journal (latest Q1)
        self.assertIn("WARNING: SCImago data ends at 2023. 3 papers published after 2023 have", out)
        summary = json.loads(Path(str(out_prefix) + "_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["quartile_filter_diagnostics"]["future_year_papers_without_publication_quartile"], 3)
        self.assertEqual(summary["main_corpus_records"], 0)
        # with the default (latest) basis the same three papers are kept and no warning is printed
        self.assertEqual(code_default, 0, err_default)
        self.assertNotIn("SCImago data ends at", out_default)
        summary = json.loads(Path(str(default_prefix) + "_summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["main_corpus_records"], summary["quartile_filter_diagnostics"]["basis"]), (3, "latest"))


ADMIT_LIST = (
    "tier\tvenue\tissn\ttitle_contains\n"
    "A\tConf Proc\t\tconf proc\n"
    "A\tUnknown Venue\t9999-9999\t\n"
    "B\tAlpha Journal\t1111-1111\t\n"
    "C\tOld Name\t\told name\n"
)


def admitted_row(pid: str, tier: str, status: str = "UNMATCHED", stype: str = "conference", q_latest: str = "",
                 **extra) -> Dict:
    """An enriched row with a venue tier, as apply_main_policy receives it."""
    row = {"openalex_id": pid, "publication_year": "2024", "source": f"Venue {pid}", "source_type": stype,
           "source_status": status, "scimago_type_latest": "journal" if stype == "journal" else "",
           "ranking_basis": "EXACT_PUBLICATION_YEAR", "best_quartile_at_publication": q_latest,
           "best_quartile_latest": q_latest, "venue_tier": tier}
    row.update(extra)
    return row


class TestAdmissionAndPreprints(RepoCase):
    """--admit-tier waives the venue filters for listed venues; preprints are listed for manual screening."""

    def write_list(self, text=ADMIT_LIST, name="admit.tsv"):
        path = Path(self.tmp.name) / name
        path.write_text(text, encoding="utf-8-sig")
        return path

    def test_admit_tier_waives_only_the_venue_filters(self):
        rows = [admitted_row("conf_a", "A"),
                admitted_row("conf_a_retracted", "A", is_retracted=True),
                admitted_row("conf_c", "C"),
                admitted_row("journal_a", "A", status="ACTIVE_LATEST", stype="journal", q_latest="Q1"),
                admitted_row("ambiguous", "AMBIGUOUS")]
        policy = {"quartiles": ["Q1"], "source_types": ["journal"], "exclude_retracted": True}
        main, excluded = apply_main_policy([dict(r) for r in rows], MANIFEST_2025, admit_tiers=["A"], **policy)
        by_id = {r["openalex_id"]: r for r in main + excluded}
        self.assertEqual(sorted(r["openalex_id"] for r in main), ["conf_a", "journal_a"])
        venue_reasons = "SOURCE_STATUS_UNMATCHED; SOURCE_TYPE_FILTER; QUARTILE_UNAVAILABLE"
        self.assertEqual((by_id["conf_a"]["main_admission_tier"], by_id["conf_a"]["main_waived_reasons"]), ("A", venue_reasons))
        self.assertEqual(by_id["conf_a"]["main_exclusion_reasons"], "")
        # the retraction filter is not a venue filter: still excluded, with the venue reasons recorded as waived
        self.assertEqual(by_id["conf_a_retracted"]["main_exclusion_reasons"], "RETRACTED")
        self.assertEqual(by_id["conf_a_retracted"]["main_waived_reasons"], venue_reasons)
        self.assertEqual((by_id["conf_c"]["main_admission_tier"], by_id["conf_c"]["main_exclusion_reasons"]), ("", venue_reasons))
        self.assertEqual((by_id["journal_a"]["main_admission_tier"], by_id["journal_a"]["main_waived_reasons"]), ("A", ""))
        self.assertEqual(by_id["ambiguous"]["main_admission_tier"], "")
        # APC, DOAJ and CWTS Core are user requirements, not venue filters: they are not waived
        main, excluded = apply_main_policy([admitted_row("conf_a", "A")], MANIFEST_2025, admit_tiers=["A"],
                                           max_apc=1000, require_doaj=True, require_cwts_core=True)
        self.assertEqual(main, [])
        self.assertEqual(excluded[0]["main_exclusion_reasons"], "APC_UNAVAILABLE; NOT_IN_DOAJ; NOT_IN_CWTS_CORE")
        self.assertEqual(excluded[0]["main_waived_reasons"], "SOURCE_STATUS_UNMATCHED")  # no source-type filter here
        summary = admission_summary(main + excluded, ["A"])
        self.assertEqual(summary, {"admit_tiers": ["A"], "papers_in_admitted_tiers": 1,
                                   "papers_admitted_by_waiving_venue_filters": {}})
        # without --admit-tier nothing changes and no admission column is added
        main, excluded = apply_main_policy([dict(r) for r in rows], MANIFEST_2025, **policy)
        self.assertEqual(sorted(r["openalex_id"] for r in main), ["journal_a"])
        self.assertFalse(any("main_admission_tier" in r or "main_waived_reasons" in r for r in main + excluded))

    def test_admit_tier_is_checked_before_any_network_access(self):
        tmp = Path(self.tmp.name)
        venue_file = self.write_list()
        proj = make_project_copy(tmp)
        cache = tmp / "must_not_be_created"
        cases = [([], "needs a venue list"),
                 (["--venue-tiers", str(venue_file)], "tier Z is not in"),
                 (["--venue-tiers", str(venue_file), "--admit-tier", "UNLISTED"], "cannot be admitted")]
        for extra, expected in cases:
            argv = ["enrich", "--cache-dir", str(cache), "--input", str(venue_file), "--output", str(tmp / "x")] + extra
            if "--admit-tier" not in extra:
                argv += ["--admit-tier", "Z" if extra else "A"]
            code, _, err = run_cli(proj, argv)
            self.assertEqual(code, 2, expected)
            self.assertIn(expected, err)
            self.assertFalse(cache.exists())
        with fake_openalex() as handler:
            cfg = {"openalex": {"api_key": "FAKEKEY_NEVER_PRINT_42"}}
            code, _, err = run_cli(make_project_copy(tmp, cfg), ["run", "--query", "q", "--cache-dir", str(cache),
                                                                 "--admit-tier", "A"])
            self.assertEqual(handler.calls, [])
        self.assertEqual(code, 2)
        self.assertIn("needs a venue list", err)
        self.assertFalse(cache.exists())

    def test_screening_defaults_come_from_config(self):
        tmp = Path(self.tmp.name)
        self.write_list()
        proj = make_project_copy(tmp, {"screening": {"venue_tiers": "../admit.tsv", "admit_tiers": ["A", "B"]}})
        cli = load_cli(proj)
        from reprovane.config import load_config
        config = load_config(None, proj)
        for argv, expected, source in ((["enrich", "--input", "x", "--output", "y"], ["A", "B"], "config.json"),
                                       (["enrich", "--input", "x", "--output", "y", "--admit-tier", "B"], ["B"], "cli"),
                                       (["enrich", "--input", "x", "--output", "y", "--no-admit-tiers"], [], "cli")):
            args = cli.build_parser().parse_args(argv)
            sources = cli.resolve_settings(args, config)
            self.assertEqual((args.admit_tiers, sources["admit_tiers"]), (expected, source), argv)
            self.assertEqual(args.venue_tiers.resolve(), (tmp / "admit.tsv").resolve())
        planted = "PLANTED_TIER_NEVER_ECHO_77"
        for screening, expected in (({"admit_tiers": planted}, "'screening.admit_tiers' must be a list of strings"),
                                    ({"admit_tiers": [planted, ""]}, "must be a list of non-empty strings"),
                                    ({"venue_tiers": 5}, "'screening.venue_tiers' must be a string")):
            code, out, err = run_cli(make_project_copy(tmp, {"screening": screening}),
                                     ["validate-repo", "--offline", "--cache-dir", str(self.dir)])
            self.assertEqual(code, 2, expected)
            self.assertIn(expected, err)
            self.assertNotIn(planted, out + err)

    def test_enrich_end_to_end_admission_and_preprints(self):
        tmp = Path(self.tmp.name)
        rows = corpus()
        # pre1 is recognised by its repository source, pre2 by its OpenAlex type
        rows.append(dict(paper("pre1", 2024, "Zenodo", stype="repository"), type="dataset",
                         cited_by_count="5", citation_normalized_percentile="0.42", citation_top_10_percent="False"))
        rows.append(dict(paper("pre2", 2023, "arXiv (Cornell University)", stype=""), type="preprint",
                         cited_by_count="90", citation_normalized_percentile="0.97", citation_top_10_percent="True"))
        fields = list(dict.fromkeys(k for r in rows for k in r))
        src = tmp / "in.csv"
        with src.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        venue_file = self.write_list()
        proj = make_project_copy(tmp)
        base = ["enrich", "--offline", "--cache-dir", str(self.dir), "--input", str(src)]
        code, out, err = run_cli(proj, base + ["--output", str(tmp / "a"), "--venue-tiers", str(venue_file),
                                               "--admit-tier", "A", "--quartile", "Q1"])
        self.assertEqual(code, 0, err)
        enriched = {r["openalex_id"]: r for r in read_csv(tmp / "a_enriched.csv")}
        main_ids = {r["openalex_id"] for r in read_csv(tmp / "a_main.csv")}
        # p10 (Conf Proc: a conference without quartile) and p08 (Unknown Venue: not in SCImago) enter as tier A
        self.assertTrue({"p08", "p10"} <= main_ids)
        self.assertEqual((enriched["p10"]["main_admission_tier"], enriched["p10"]["main_waived_reasons"]),
                         ("A", "SOURCE_TYPE_FILTER; QUARTILE_UNAVAILABLE"))
        self.assertEqual(enriched["p08"]["main_waived_reasons"], "SOURCE_STATUS_UNMATCHED; QUARTILE_UNAVAILABLE")
        self.assertNotIn("p11", main_ids)  # Old Name is tier C, not admitted
        self.assertIn("admitted by venue tier A (venue filters waived): {'A': 2}", out)
        summary = json.loads((tmp / "a_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["admission_by_venue_tier"]["papers_admitted_by_waiving_venue_filters"], {"A": 2})
        self.assertEqual(summary["preprints"], {"papers": 2, "in_main_corpus": 0, "citation_top_10_percent": 1})
        preprints = read_csv(tmp / "a_preprints.csv")
        self.assertEqual([r["openalex_id"] for r in preprints], ["pre2", "pre1"])  # most cited first
        self.assertEqual(list(preprints[0]), list(read_csv(tmp / "a_enriched.csv")[0]))  # same columns as enriched
        self.assertIn("preprints (OpenAlex type preprint or a repository source): 2", out)
        meta = json.loads((tmp / "a_run_metadata.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["policy"]["admit_tiers"], meta["outputs"]["preprints_csv"]), (["A"], str(tmp / "a_preprints.csv")))
        # re-enriching without --admit-tier drops the admission columns; the preprint list is still written
        code, out, err = run_cli(proj, base[:-1] + [str(tmp / "a_enriched.csv"), "--output", str(tmp / "b")])
        self.assertEqual(code, 0, err)
        header = (tmp / "b_enriched.csv").read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertNotIn("main_admission_tier", header)
        self.assertNotIn("admission_by_venue_tier", json.loads((tmp / "b_summary.json").read_text(encoding="utf-8")))
        self.assertEqual(len(read_csv(tmp / "b_preprints.csv")), 2)


if __name__ == "__main__":
    unittest.main()
