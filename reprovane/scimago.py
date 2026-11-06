from __future__ import annotations

import csv
import re
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .common import (
    has_status_suffix,
    int_or_none,
    locale_float,
    normalize_issn,
    normalize_quartile,
    safe_str,
    split_issns,
    title_exact_key,
    title_normalized_key,
    title_status_normalized_key,
)

# Matching priority shared by the latest snapshot, publication-year snapshots and the
# historical (any earlier snapshot) lookup. TITLE_STATUS_NORMALIZED is the lowest-priority
# rule: it ignores SCImago status labels such as " (discontinued)" appended to titles.
MATCH_METHODS = ("ISSN_EXACT", "ISSNL_EXACT", "TITLE_EXACT", "TITLE_NORMALIZED", "TITLE_STATUS_NORMALIZED")
TITLE_METHODS = ("TITLE_EXACT", "TITLE_NORMALIZED", "TITLE_STATUS_NORMALIZED")
# Non-success outcome: a title matched, but every title candidate lists ISSNs that contradict the row's ISSNs.
ISSN_CONFLICT_METHOD = "TITLE_MATCH_ISSN_CONFLICT"
_VALID_ISSN_RE = re.compile(r"^\d{7}[\dX]$")


@dataclass
class MatchResult:
    sourceid: str = ""
    method: str = "NO_MATCH"
    status: str = "UNMATCHED"
    candidates: Tuple[str, ...] = ()
    # Title candidates rejected because their ISSNs contradict the row's ISSNs.
    conflicts: Tuple[str, ...] = ()


@dataclass
class HistoricalMatch(MatchResult):
    years: Tuple[int, ...] = ()
    source_type: str = ""
    title: str = ""


@dataclass
class ScimagoRecord:
    sourceid: str
    year: int
    title: str
    source_type: str
    issn: str
    publisher: str
    open_access: str
    open_access_diamond: str
    sjr: Optional[float]
    best_quartile: str
    h_index: Optional[int]
    country: str
    region: str
    coverage: str
    categories: str
    areas: str
    rank: Optional[int]

    def as_metrics(self) -> Dict[str, Any]:
        return {
            "sourceid": self.sourceid,
            "year": self.year,
            "title": self.title,
            "type": self.source_type,
            "issn": self.issn,
            "publisher": self.publisher,
            "open_access": self.open_access,
            "open_access_diamond": self.open_access_diamond,
            "sjr": self.sjr,
            "best_quartile": self.best_quartile,
            "h_index": self.h_index,
            "country": self.country,
            "region": self.region,
            "coverage": self.coverage,
            "categories": self.categories,
            "areas": self.areas,
            "rank": self.rank,
        }


def _unique_headers(headers: List[str]) -> List[str]:
    counts: Dict[str, int] = defaultdict(int)
    result = []
    for header in headers:
        counts[header] += 1
        if counts[header] == 1:
            result.append(header)
        else:
            result.append(f"{header}__{counts[header]}")
    return result


def _row_dict(headers: List[str], row: List[str]) -> Dict[str, str]:
    if len(row) < len(headers):
        row = row + [""] * (len(headers) - len(row))
    return {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}


def iter_scimago_records(path: Path, year: int, delimiter: str = ";", encoding: str = "utf-8") -> Iterable[ScimagoRecord]:
    with path.open("r", encoding=encoding + "-sig" if encoding.lower() == "utf-8" else encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        raw_headers = next(reader)
        headers = _unique_headers(raw_headers)
        for row in reader:
            if not row:
                continue
            data = _row_dict(headers, row)
            sourceid = safe_str(data.get("Sourceid")).strip()
            if not sourceid:
                continue
            publisher = safe_str(data.get("Publisher")).strip() or safe_str(data.get("Publisher__2")).strip()
            yield ScimagoRecord(
                sourceid=sourceid,
                year=year,
                title=safe_str(data.get("Title")).strip(),
                source_type=safe_str(data.get("Type")).strip(),
                issn=safe_str(data.get("Issn")).strip(),
                publisher=publisher,
                open_access=safe_str(data.get("Open Access")).strip(),
                open_access_diamond=safe_str(data.get("Open Access Diamond")).strip(),
                sjr=locale_float(data.get("SJR")),
                # SCImago exports "-" when no quartile is assigned; keep that as unavailable, not as a quartile value.
                best_quartile=normalize_quartile(data.get("SJR Best Quartile")),
                h_index=int_or_none(data.get("H index")),
                country=safe_str(data.get("Country")).strip(),
                region=safe_str(data.get("Region")).strip(),
                coverage=safe_str(data.get("Coverage")).strip(),
                categories=safe_str(data.get("Categories")).strip(),
                areas=safe_str(data.get("Areas")).strip(),
                rank=int_or_none(data.get("Rank")),
            )


def _query_keys(source_name: str, issn: str, issn_l: str) -> Dict[str, List[str]]:
    """Return the lookup keys for each match method, in MATCH_METHODS order."""
    issn_l_norm = normalize_issn(issn_l)
    exact = title_exact_key(source_name)
    normalized = title_normalized_key(source_name)
    status_normalized = title_status_normalized_key(source_name)
    return {
        "ISSN_EXACT": [x for x in split_issns(issn) if x != issn_l_norm],
        "ISSNL_EXACT": [issn_l_norm] if issn_l_norm else [],
        "TITLE_EXACT": [exact] if exact else [],
        "TITLE_NORMALIZED": [normalized] if normalized else [],
        "TITLE_STATUS_NORMALIZED": [status_normalized] if status_normalized else [],
    }


def identifier_issns(*values: str) -> Set[str]:
    """Valid ISSNs (7 digits + check digit or X) in the given fields; only these count as identifier evidence."""
    result: Set[str] = set()
    for value in values:
        result.update(issn for issn in split_issns(value) if _VALID_ISSN_RE.match(issn))
    return result


def _resolve_candidates(
    candidates: Set[str],
    method: str,
    query_issns: Set[str],
    issns_of,
) -> MatchResult:
    """Decide one matching level (the first level that produced any candidate).

    Title levels apply the ISSN-conflict rule. When the row has valid ISSNs and a title candidate
    lists ISSNs, none of which is shared (identifier conflict), that candidate is rejected.
    Candidates without ISSNs, and rows without ISSNs, carry no contradicting evidence and stay
    eligible. If every candidate is rejected the result is UNMATCHED / TITLE_MATCH_ISSN_CONFLICT and
    weaker title levels are not tried, so a title can never override contradictory ISSN evidence.
    More than one remaining candidate is AMBIGUOUS, never silently resolved.
    """
    conflicts: Tuple[str, ...] = ()
    if method in TITLE_METHODS and query_issns:
        rejected = set()
        for sid in candidates:
            candidate_issns = issns_of(sid)
            if candidate_issns and not (candidate_issns & query_issns):
                rejected.add(sid)
        conflicts = tuple(sorted(rejected))
        candidates = candidates - rejected
        if not candidates:
            return MatchResult(method=ISSN_CONFLICT_METHOD, status="UNMATCHED", candidates=conflicts, conflicts=conflicts)
    ordered = tuple(sorted(candidates))
    if len(ordered) == 1:
        return MatchResult(sourceid=ordered[0], method=method, status="MATCHED", candidates=ordered, conflicts=conflicts)
    return MatchResult(sourceid="", method=method, status="AMBIGUOUS", candidates=ordered, conflicts=conflicts)


def _record_title_keys(title: str) -> Tuple[str, str, str]:
    normalized = title_normalized_key(title)
    status_normalized = title_status_normalized_key(title) if has_status_suffix(title) else normalized
    return title_exact_key(title), normalized, status_normalized


class SourceIndex:
    def __init__(self, records: Iterable[ScimagoRecord]):
        self.by_sourceid: Dict[str, ScimagoRecord] = {}
        self.issn_index: Dict[str, Set[str]] = defaultdict(set)
        self.title_exact_index: Dict[str, Set[str]] = defaultdict(set)
        self.title_normalized_index: Dict[str, Set[str]] = defaultdict(set)
        self.title_status_index: Dict[str, Set[str]] = defaultdict(set)
        for record in records:
            self.by_sourceid[record.sourceid] = record
            for issn in split_issns(record.issn):
                self.issn_index[issn].add(record.sourceid)
            exact, normalized, status_normalized = _record_title_keys(record.title)
            if exact:
                self.title_exact_index[exact].add(record.sourceid)
            if normalized:
                self.title_normalized_index[normalized].add(record.sourceid)
            if status_normalized:
                self.title_status_index[status_normalized].add(record.sourceid)

    @classmethod
    def from_file(cls, path: Path, year: int, delimiter: str = ";", encoding: str = "utf-8") -> "SourceIndex":
        return cls(iter_scimago_records(path, year, delimiter, encoding))

    def match(self, source_name: str, issn: str, issn_l: str) -> MatchResult:
        indexes = {
            "ISSN_EXACT": self.issn_index,
            "ISSNL_EXACT": self.issn_index,
            "TITLE_EXACT": self.title_exact_index,
            "TITLE_NORMALIZED": self.title_normalized_index,
            "TITLE_STATUS_NORMALIZED": self.title_status_index,
        }
        query_issns = identifier_issns(issn, issn_l)
        for method, values in _query_keys(source_name, issn, issn_l).items():
            candidates: Set[str] = set()
            for value in values:
                candidates.update(indexes[method].get(value, set()))
            if candidates:
                return _resolve_candidates(
                    candidates, method, query_issns, lambda sid: identifier_issns(self.by_sourceid[sid].issn)
                )
        return MatchResult()


def _sjr_files(manifest: Dict[str, Any]) -> Tuple[str, str, int, int, int, str]:
    cfg = manifest["sjr"]
    return (
        cfg.get("delimiter", ";"),
        cfg.get("encoding", "utf-8"),
        int(cfg["first_year"]),
        int(cfg["last_year"]),
        int(cfg["latest_snapshot_year"]),
        cfg["url_pattern"],
    )


def scan_historical_sources(
    cache_dir: Path,
    manifest: Dict[str, Any],
    queries: List[Tuple[str, str, str]],
    years: Iterable[int],
    collect_records: bool = False,
) -> Tuple[Dict[Tuple[str, str, str], HistoricalMatch], Dict[Tuple[int, str], ScimagoRecord]]:
    """Match (source_name, issn, issn_l) queries against several annual snapshots in one streaming pass.

    Candidates are pooled across all scanned years per method, and the first method (in
    MATCH_METHODS order) with any candidate decides: one Sourceid -> MATCHED, more -> AMBIGUOUS.
    Title levels apply the same ISSN-conflict rule as SourceIndex.match; a candidate's ISSNs are
    the union over the scanned years in which it matched the query.
    Each annual file is read once regardless of the number of queries, and no full index is built.
    With collect_records=True the matching records are also returned keyed by (year, sourceid).
    """
    delimiter, encoding, _, _, _, pattern = _sjr_files(manifest)
    unique_queries = list(dict.fromkeys(queries))
    keyed: Dict[str, Dict[str, Set[int]]] = {method: defaultdict(set) for method in MATCH_METHODS}
    for qi, (name, issn, issn_l) in enumerate(unique_queries):
        for method, values in _query_keys(name, issn, issn_l).items():
            for value in values:
                keyed[method][value].add(qi)

    found: List[Dict[str, Set[str]]] = [defaultdict(set) for _ in unique_queries]
    # Years/title/type are tracked per (query, Sourceid) so one row's result never depends on
    # which other rows happen to be in the same corpus.
    seen: List[Dict[str, Dict[str, Any]]] = [{} for _ in unique_queries]
    collected: Dict[Tuple[int, str], ScimagoRecord] = {}
    if any(keyed[method] for method in MATCH_METHODS):
        for year in years:
            for record in iter_scimago_records(cache_dir / pattern.format(year=year), year, delimiter, encoding):
                hits: List[Tuple[int, str]] = []
                for value in split_issns(record.issn):
                    for method in ("ISSN_EXACT", "ISSNL_EXACT"):
                        hits.extend((qi, method) for qi in keyed[method].get(value, ()))
                exact, normalized, status_normalized = _record_title_keys(record.title)
                for method, key in (
                    ("TITLE_EXACT", exact),
                    ("TITLE_NORMALIZED", normalized),
                    ("TITLE_STATUS_NORMALIZED", status_normalized),
                ):
                    if key:
                        hits.extend((qi, method) for qi in keyed[method].get(key, ()))
                if not hits:
                    continue
                record_issns = identifier_issns(record.issn)
                for qi, method in hits:
                    found[qi][method].add(record.sourceid)
                    info = seen[qi].setdefault(record.sourceid, {"years": set(), "year": -1, "issns": set()})
                    info["years"].add(year)
                    info["issns"].update(record_issns)
                    if year > info["year"]:
                        info.update(year=year, title=record.title, type=record.source_type)
                if collect_records:
                    collected[(year, record.sourceid)] = record

    results: Dict[Tuple[str, str, str], HistoricalMatch] = {}
    for qi, query in enumerate(unique_queries):
        result = HistoricalMatch()
        query_issns = identifier_issns(query[1], query[2])
        for method in MATCH_METHODS:
            candidates = found[qi].get(method)
            if not candidates:
                continue
            level = _resolve_candidates(set(candidates), method, query_issns, lambda sid: seen[qi][sid]["issns"])
            if level.status == "MATCHED":
                info = seen[qi][level.sourceid]
                result = HistoricalMatch(
                    sourceid=level.sourceid,
                    method=method,
                    status="MATCHED",
                    candidates=level.candidates,
                    conflicts=level.conflicts,
                    years=tuple(sorted(info["years"])),
                    source_type=info.get("type", ""),
                    title=info.get("title", ""),
                )
            else:
                result = HistoricalMatch(
                    method=level.method, status=level.status, candidates=level.candidates, conflicts=level.conflicts
                )
            break
        results[query] = result
    return results, collected


def fetch_source_records(
    cache_dir: Path,
    manifest: Dict[str, Any],
    wanted: Dict[int, Set[str]],
    known: Optional[Dict[Tuple[int, str], ScimagoRecord]] = None,
    in_memory: Optional[Dict[int, Dict[str, ScimagoRecord]]] = None,
) -> Dict[Tuple[int, str], ScimagoRecord]:
    """Return {(year, sourceid): record} for the wanted pairs, reading each needed annual file at most once."""
    delimiter, encoding, _, _, _, pattern = _sjr_files(manifest)
    found = dict(known or {})
    for year in sorted(wanted):
        missing = {sid for sid in wanted[year] if (year, sid) not in found}
        if not missing:
            continue
        if in_memory and year in in_memory:
            for sid in missing:
                if sid in in_memory[year]:
                    found[(year, sid)] = in_memory[year][sid]
            continue
        with closing(iter_scimago_records(cache_dir / pattern.format(year=year), year, delimiter, encoding)) as rows:
            for record in rows:
                if record.sourceid in missing:
                    found[(year, record.sourceid)] = record
                    missing.discard(record.sourceid)
                    if not missing:
                        break
    return found


def compact_years(years: Iterable[int]) -> str:
    """Render years as compact ranges, e.g. [2010, 2011, 2012, 2015] -> '2010-2012; 2015'."""
    ordered = sorted(set(years))
    parts: List[str] = []
    start = prev = None
    for year in ordered:
        if start is None:
            start = prev = year
        elif year == prev + 1:
            prev = year
        else:
            parts.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = year
    if start is not None:
        parts.append(str(start) if start == prev else f"{start}-{prev}")
    return "; ".join(parts)


CATEGORY_RE = re.compile(r"^\s*(.*?)\s*\((Q[1-4])\)\s*$", re.IGNORECASE)


def parse_categories(value: str) -> List[Tuple[str, str]]:
    result: List[Tuple[str, str]] = []
    for part in safe_str(value).split(";"):
        part = part.strip()
        if not part:
            continue
        match = CATEGORY_RE.match(part)
        if match:
            result.append((match.group(1).strip(), match.group(2).upper()))
        else:
            result.append((part, ""))
    return result


def _best_quartile(values: Iterable[str]) -> str:
    qs = [q.upper() for q in values if q and q.upper() in {"Q1", "Q2", "Q3", "Q4"}]
    if not qs:
        return ""
    return min(qs, key=lambda q: int(q[1]))


def relevant_category_summary(categories: str, requested: List[str]) -> Tuple[str, str]:
    """Match requested SCImago categories by case/punctuation-insensitive equality.

    Substring matching was removed in v3.0.4: it made "Information Systems" match
    "Management Information Systems" and "Computer Networks and Communications" match
    "Communication", which assigned quartiles from unrelated categories.
    """
    if not requested:
        return "", ""
    parsed = parse_categories(categories)
    requested_keys = {title_normalized_key(item) for item in requested if title_normalized_key(item)}
    matches = [(category, quartile) for category, quartile in parsed if title_normalized_key(category) in requested_keys]
    if not matches:
        return "", ""
    text = "; ".join(f"{name} ({q})" if q else name for name, q in matches)
    return text, _best_quartile(q for _, q in matches)


def load_apc_snapshot(path: Path, year: int, delimiter: str = ";", encoding: str = "utf-8") -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    with path.open("r", encoding=encoding + "-sig" if encoding.lower() == "utf-8" else encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        headers = next(reader, [])
        for row in reader:
            if not row or len(row) < 12:
                continue
            # SCImago Journal Value 2025 export observed in this project has a header/data shift:
            # header says Publisher at col 5, but data rows are Open Access, Diamond, Publisher.
            shifted = len(row) >= 13 and row[5].strip() in {"Yes", "No"} and row[6].strip() in {"Yes", "No"}
            if shifted:
                sourceid = row[1].strip()
                data = {
                    "sourceid": sourceid,
                    "title": row[2].strip(),
                    "type": row[3].strip(),
                    "issn": row[4].strip(),
                    "open_access": row[5].strip(),
                    "open_access_diamond": row[6].strip(),
                    "publisher": row[7].strip(),
                    "sjr": locale_float(row[8]),
                    "best_quartile": normalize_quartile(row[9]),
                    "total_docs": int_or_none(row[10]),
                    "estimated_apc_usd": locale_float(row[11]),
                    "estimated_value_usd": locale_float(row[12]) if len(row) > 12 else None,
                    "apc_data_year": year,
                }
            else:
                unique = _unique_headers(headers)
                mapped = _row_dict(unique, row)
                sourceid = safe_str(mapped.get("Sourceid")).strip()
                data = {
                    "sourceid": sourceid,
                    "title": safe_str(mapped.get("Title")).strip(),
                    "type": safe_str(mapped.get("Type")).strip(),
                    "issn": safe_str(mapped.get("Issn")).strip(),
                    "publisher": safe_str(mapped.get("Publisher")).strip(),
                    "open_access": safe_str(mapped.get("Open Access")).strip(),
                    "open_access_diamond": safe_str(mapped.get("Open Access Diamond")).strip(),
                    "sjr": locale_float(mapped.get("SJR")),
                    "best_quartile": normalize_quartile(mapped.get("SJR Best Quartile")),
                    "total_docs": int_or_none(mapped.get(f"Total Docs. ({year})")),
                    "estimated_apc_usd": locale_float(mapped.get("Estimated APC (USD)")),
                    "estimated_value_usd": locale_float(mapped.get("Estimated value (USD)")),
                    "apc_data_year": year,
                }
            if sourceid:
                result[sourceid] = data
    return result


def _apply_latest(
    row: Dict[str, Any],
    source: ScimagoRecord,
    latest_year: int,
    apc: Dict[str, Dict[str, Any]],
    apc_year: int,
    relevant_categories: List[str],
) -> None:
    row["scimago_sourceid"] = source.sourceid
    row["source_status"] = "ACTIVE_LATEST"
    row["in_scimago_latest"] = True
    row["latest_scimago_year"] = latest_year
    row["scimago_title_latest"] = source.title
    row["scimago_type_latest"] = source.source_type
    row["scimago_issn_latest"] = source.issn
    row["scimago_publisher_latest"] = source.publisher
    row["sjr_latest"] = source.sjr
    row["best_quartile_latest"] = source.best_quartile
    row["h_index_latest"] = source.h_index
    row["categories_latest"] = source.categories
    row["areas_latest"] = source.areas
    row["scimago_country_latest"] = source.country
    row["scimago_region_latest"] = source.region
    row["scimago_coverage_latest"] = source.coverage
    # Informational only: SCImago itself labels some titles "(discontinued)". Not used for screening.
    row["scimago_discontinued_label_latest"] = has_status_suffix(source.title)
    matches, q = relevant_category_summary(source.categories, relevant_categories)
    row["relevant_categories_latest"] = matches
    row["relevant_category_quartile_latest"] = q
    if source.sourceid in apc:
        row["estimated_apc_usd"] = apc[source.sourceid].get("estimated_apc_usd")
        row["estimated_value_usd"] = apc[source.sourceid].get("estimated_value_usd")
    else:
        row["estimated_apc_usd"] = None
        row["estimated_value_usd"] = None
    row["apc_data_year"] = apc_year


def _mark_historical(row: Dict[str, Any], sourceid: str, method: str, source_type: str, years: str) -> None:
    row["source_status"] = "HISTORICAL_ONLY"
    row["scimago_sourceid_historical"] = sourceid
    row["historical_match_method"] = method
    row["scimago_type_historical"] = source_type
    row["historical_snapshot_years"] = years


def _apply_publication_metrics(
    row: Dict[str, Any],
    source: Optional[ScimagoRecord],
    year: int,
    relevant_categories: List[str],
) -> None:
    if source:
        identity = safe_str(row.get("scimago_sourceid")) or safe_str(row.get("scimago_sourceid_historical"))
        row["sjr_year_at_publication"] = year
        row["sjr_at_publication"] = source.sjr
        row["best_quartile_at_publication"] = source.best_quartile
        row["h_index_at_publication"] = source.h_index
        row["categories_at_publication"] = source.categories
        row["areas_at_publication"] = source.areas
        matches, q = relevant_category_summary(source.categories, relevant_categories)
        row["relevant_categories_at_publication"] = matches
        row["relevant_category_quartile_at_publication"] = q
        row["scimago_sourceid_at_publication"] = source.sourceid
        row["scimago_type_at_publication"] = source.source_type
        row["publication_sourceid_differs"] = bool(identity and identity != source.sourceid)
        row["ranking_basis"] = "EXACT_PUBLICATION_YEAR"
    else:
        row["sjr_year_at_publication"] = None
        row["sjr_at_publication"] = None
        row["best_quartile_at_publication"] = ""
        row["h_index_at_publication"] = None
        row["categories_at_publication"] = ""
        row["areas_at_publication"] = ""
        row["relevant_categories_at_publication"] = ""
        row["relevant_category_quartile_at_publication"] = ""
        row["scimago_sourceid_at_publication"] = ""
        row["scimago_type_at_publication"] = ""
        row["publication_sourceid_differs"] = False
        row["ranking_basis"] = "UNAVAILABLE"


# Every column written by enrichment/screening. They are removed before enriching so that a
# previously enriched file (e.g. *_enriched.csv or *_main.csv) is re-enriched from scratch and
# stale values from an older run or SCImago release can never survive or steer matching.
ENRICHMENT_FIELDS = (
    "latest_match_method", "latest_match_candidates", "scimago_sourceid", "source_status", "in_scimago_latest",
    "latest_scimago_year", "scimago_title_latest", "scimago_type_latest", "scimago_issn_latest",
    "scimago_publisher_latest", "sjr_latest", "best_quartile_latest", "h_index_latest", "categories_latest",
    "areas_latest", "scimago_country_latest", "scimago_region_latest", "scimago_coverage_latest",
    "scimago_discontinued_label_latest", "relevant_categories_latest", "relevant_category_quartile_latest",
    "estimated_apc_usd", "estimated_value_usd", "apc_data_year",
    "publication_match_method", "publication_match_candidates", "sjr_year_at_publication", "sjr_at_publication",
    "best_quartile_at_publication", "h_index_at_publication", "categories_at_publication", "areas_at_publication",
    "relevant_categories_at_publication", "relevant_category_quartile_at_publication",
    "scimago_sourceid_at_publication", "scimago_type_at_publication", "publication_sourceid_differs", "ranking_basis",
    "scimago_sourceid_historical", "historical_match_method", "historical_match_candidates",
    "historical_snapshot_years", "scimago_type_historical",
    "issn_conflict_candidates", "match_diagnostic",
    "source_class", "main_corpus", "main_exclusion_reasons",
    "venue_tier", "venue_tier_venue", "venue_tier_match",
    "main_admission_tier", "main_waived_reasons",
)


def _note_conflicts(row: Dict[str, Any], conflicts: Tuple[str, ...]) -> None:
    """Record title candidates rejected for contradicting ISSNs (any matching stage)."""
    if not conflicts:
        return
    merged = set(filter(None, safe_str(row.get("issn_conflict_candidates")).split("; "))) | set(conflicts)
    row["issn_conflict_candidates"] = "; ".join(sorted(merged))
    row["match_diagnostic"] = "ISSN_CONFLICT"


def _publication_year(row: Dict[str, Any]) -> Optional[int]:
    try:
        return int(row.get("publication_year"))
    except (TypeError, ValueError):
        return None


def enrich_records(
    records: List[Dict[str, Any]],
    cache_dir: Path,
    manifest: Dict[str, Any],
    relevant_categories: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    relevant_categories = relevant_categories or []
    delimiter, encoding, first_year, last_year, latest_year, pattern = _sjr_files(manifest)
    for row in records:
        for field in ENRICHMENT_FIELDS:
            row.pop(field, None)
        row["issn_conflict_candidates"] = ""
        row["match_diagnostic"] = ""

    latest_path = cache_dir / pattern.format(year=latest_year)
    latest_index = SourceIndex.from_file(latest_path, latest_year, delimiter, encoding)

    apc_cfg = manifest.get("journal_value") or {}
    apc_year = int(apc_cfg.get("year", latest_year))
    apc_file = cache_dir / safe_str(apc_cfg.get("file"))
    apc = load_apc_snapshot(apc_file, apc_year, delimiter, encoding) if apc_file.exists() else {}

    # Match against latest snapshot first; this directly supports the agreed main-corpus policy.
    for row in records:
        match = latest_index.match(safe_str(row.get("source")), safe_str(row.get("issn")), safe_str(row.get("issn_l")))
        row["latest_match_method"] = match.method
        row["latest_match_candidates"] = "; ".join(match.candidates)
        _note_conflicts(row, match.conflicts)
        if match.status == "MATCHED":
            _apply_latest(row, latest_index.by_sourceid[match.sourceid], latest_year, apc, apc_year, relevant_categories)
        else:
            row["scimago_sourceid"] = ""
            row["source_status"] = "AMBIGUOUS" if match.status == "AMBIGUOUS" else "UNMATCHED"
            row["in_scimago_latest"] = False
            row["latest_scimago_year"] = latest_year
            row["scimago_discontinued_label_latest"] = False

    # Historical matching/metrics are processed one publication year at a time to keep memory bounded.
    grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for row in records:
        year = _publication_year(row)
        if year is None or year < first_year or year > last_year:
            # No exact-year snapshot exists; never borrow a quartile from another year.
            _apply_publication_metrics(row, None, 0, relevant_categories)
            continue
        grouped[year].append(row)

    for year, year_rows in sorted(grouped.items()):
        year_path = cache_dir / pattern.format(year=year)
        # The latest snapshot is already indexed; do not parse the same file twice.
        year_index = latest_index if year == latest_year else SourceIndex.from_file(year_path, year, delimiter, encoding)
        for row in year_rows:
            source: Optional[ScimagoRecord] = None
            sourceid = safe_str(row.get("scimago_sourceid"))
            if sourceid and sourceid in year_index.by_sourceid:
                source = year_index.by_sourceid[sourceid]
                row["publication_match_method"] = "SOURCEID_HISTORY"
            elif row.get("source_status") != "AMBIGUOUS":
                historical_match = year_index.match(
                    safe_str(row.get("source")), safe_str(row.get("issn")), safe_str(row.get("issn_l"))
                )
                row["publication_match_method"] = historical_match.method
                row["publication_match_candidates"] = "; ".join(historical_match.candidates)
                _note_conflicts(row, historical_match.conflicts)
                if historical_match.status == "MATCHED":
                    source = year_index.by_sourceid[historical_match.sourceid]
                    if row.get("source_status") == "UNMATCHED":
                        latest_source = latest_index.by_sourceid.get(source.sourceid)
                        if latest_source:
                            # Same SCImago Sourceid is present in the latest snapshot under another ISSN/title.
                            _apply_latest(row, latest_source, latest_year, apc, apc_year, relevant_categories)
                            row["latest_match_method"] = f"SOURCEID_VIA_PUBLICATION_YEAR_{historical_match.method}"
                            row["latest_match_candidates"] = source.sourceid
                        else:
                            _mark_historical(
                                row, source.sourceid, f"PUBLICATION_YEAR_{historical_match.method}",
                                source.source_type, str(year),
                            )
                elif historical_match.status == "AMBIGUOUS" and row.get("source_status") == "UNMATCHED":
                    row["source_status"] = "AMBIGUOUS"
            _apply_publication_metrics(row, source, year, relevant_categories)

    # Sources still UNMATCHED may exist in other earlier snapshots (the publication year may be
    # absent from SCImago, later than the latest snapshot, or missing). HISTORICAL_ONLY means
    # "matched in an earlier SCImago snapshot but absent from latest", so look across all of them.
    pending = [
        row for row in records
        if row.get("source_status") == "UNMATCHED"
        and (safe_str(row.get("source")) or safe_str(row.get("issn")) or safe_str(row.get("issn_l")))
    ]
    if pending:
        def query_of(item: Dict[str, Any]) -> Tuple[str, str, str]:
            return safe_str(item.get("source")), safe_str(item.get("issn")), safe_str(item.get("issn_l"))

        results, collected = scan_historical_sources(
            cache_dir, manifest, [query_of(row) for row in pending], range(first_year, latest_year), collect_records=True
        )
        wanted: Dict[int, Set[str]] = defaultdict(set)
        for row in pending:
            result = results[query_of(row)]
            row["historical_match_candidates"] = "; ".join(result.candidates)
            _note_conflicts(row, result.conflicts)
            if result.method == ISSN_CONFLICT_METHOD:
                # Stays UNMATCHED: an earlier snapshot matched only by title, contradicted by ISSN.
                row["historical_match_method"] = f"ANY_EARLIER_SNAPSHOT_{ISSN_CONFLICT_METHOD}"
            if result.status == "MATCHED":
                latest_source = latest_index.by_sourceid.get(result.sourceid)
                if latest_source:
                    _apply_latest(row, latest_source, latest_year, apc, apc_year, relevant_categories)
                    row["latest_match_method"] = f"SOURCEID_VIA_HISTORICAL_{result.method}"
                    row["latest_match_candidates"] = result.sourceid
                else:
                    _mark_historical(
                        row, result.sourceid, f"ANY_EARLIER_SNAPSHOT_{result.method}",
                        result.source_type, compact_years(result.years),
                    )
                year = _publication_year(row)
                if year is not None and first_year <= year <= last_year:
                    wanted[year].add(result.sourceid)
            elif result.status == "AMBIGUOUS":
                row["source_status"] = "AMBIGUOUS"
                row["historical_match_method"] = f"ANY_EARLIER_SNAPSHOT_{result.method}"
        if wanted:
            fetched = fetch_source_records(
                cache_dir, manifest, wanted, known=collected, in_memory={latest_year: latest_index.by_sourceid}
            )
            for row in pending:
                result = results[query_of(row)]
                if result.status != "MATCHED":
                    continue
                year = _publication_year(row)
                sourceid = result.sourceid
                if year in wanted and (year, sourceid) in fetched:
                    _apply_publication_metrics(row, fetched[(year, sourceid)], year, relevant_categories)
                    row["publication_match_method"] = "SOURCEID_HISTORY"

    # Future publication years: latest metadata may be shown only as latest/current metadata, and
    # only when such metadata exists (ACTIVE_LATEST). Never label it as a publication-year metric.
    for row in records:
        year = _publication_year(row)
        if year is not None and year > last_year and row.get("source_status") == "ACTIVE_LATEST":
            row["ranking_basis"] = "LATEST_AVAILABLE"

    return records


def source_history(
    cache_dir: Path,
    manifest: Dict[str, Any],
    title: str = "",
    issn: str = "",
) -> Tuple[MatchResult, List[Dict[str, Any]], Optional[Dict[str, Any]], str]:
    """Return SCImago trajectory for latest or historical-only sources.

    The latest snapshot is searched first, then all earlier snapshots in one streaming pass
    using the same matching rules as enrichment. Ambiguous matches are never silently accepted.
    """
    delimiter, encoding, first_year, last_year, latest_year, pattern = _sjr_files(manifest)

    latest = SourceIndex.from_file(cache_dir / pattern.format(year=latest_year), latest_year, delimiter, encoding)
    latest_match = latest.match(title, issn, "")
    match = latest_match
    collected: Dict[Tuple[int, str], ScimagoRecord] = {}

    if latest_match.status == "MATCHED":
        source_status = "ACTIVE_LATEST"
    elif latest_match.status == "AMBIGUOUS":
        return latest_match, [], None, "AMBIGUOUS"
    else:
        query = (title, issn, "")
        results, collected = scan_historical_sources(
            cache_dir, manifest, [query], range(latest_year - 1, first_year - 1, -1), collect_records=True
        )
        historical = results[query]
        if historical.status == "MATCHED":
            if historical.sourceid in latest.by_sourceid:
                # Matched by an older title/ISSN, but the same Sourceid is present in the latest snapshot.
                method = f"SOURCEID_VIA_HISTORICAL_{historical.method}"
                source_status = "ACTIVE_LATEST"
            else:
                method = f"HISTORICAL_{historical.method}"
                source_status = "HISTORICAL_ONLY"
            match = MatchResult(
                sourceid=historical.sourceid, method=method, status="MATCHED", candidates=historical.candidates
            )
        elif historical.status == "AMBIGUOUS":
            match = MatchResult(
                sourceid="",
                method=f"HISTORICAL_{historical.method}_AMBIGUOUS",
                status="AMBIGUOUS",
                candidates=historical.candidates,
            )
            return match, [], None, "AMBIGUOUS"
        else:
            conflicts = tuple(sorted(set(latest_match.conflicts) | set(historical.conflicts)))
            if conflicts:
                # A title matched (latest and/or earlier snapshots) but the given ISSN contradicts it.
                conflict = MatchResult(method=ISSN_CONFLICT_METHOD, status="UNMATCHED", candidates=conflicts, conflicts=conflicts)
                return conflict, [], None, "UNMATCHED"
            return latest_match, [], None, "UNMATCHED"

    # Years already seen during the historical scan are reused; only the remaining annual files are
    # streamed, each with an early exit once the Sourceid is found.
    wanted = {year: {match.sourceid} for year in range(first_year, last_year + 1)}
    fetched = fetch_source_records(cache_dir, manifest, wanted, known=collected, in_memory={latest_year: latest.by_sourceid})
    history = [
        fetched[(year, match.sourceid)].as_metrics()
        for year in range(first_year, last_year + 1)
        if (year, match.sourceid) in fetched
    ]

    apc_cfg = manifest.get("journal_value") or {}
    apc_path = cache_dir / safe_str(apc_cfg.get("file"))
    apc_data = load_apc_snapshot(apc_path, int(apc_cfg.get("year", latest_year)), delimiter, encoding) if apc_path.exists() else {}
    return match, history, apc_data.get(match.sourceid), source_status
