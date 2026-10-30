from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .common import (
    read_csv,
    read_jsonl,
    safe_str,
    sha256_file,
    union_fieldnames,
    write_csv,
    write_json,
    write_jsonl,
)
from .openalex import download_queries, search_query_problem
from .scimago import enrich_records
from .scimago_repo import DEFAULT_MANIFEST_URL, sync_repository
from .venues import TIER_FIELDS as VENUE_TIER_FIELDS
from .venues import VenueTiers, admit_tier_problem


def load_queries(query_values: Optional[List[str]], query_file: Optional[Path]) -> List[str]:
    queries: List[str] = []
    if query_values:
        queries.extend(query_values)
    if query_file:
        with query_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                value = line.strip()
                if value and not value.startswith("#"):
                    queries.append(value)
    result = []
    seen = set()
    for query in queries:
        query = query.strip()
        if query and query not in seen:
            seen.add(query)
            result.append(query)
    return result


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    return safe_str(value).strip().casefold() in {"true", "1", "yes", "y"}


def _quartile_from_row(row: Dict[str, Any], basis: str, mode: str) -> str:
    suffix = "latest" if basis == "latest" else "at_publication"
    if mode == "relevant-category":
        return safe_str(row.get(f"relevant_category_quartile_{suffix}"))
    return safe_str(row.get(f"best_quartile_{suffix}"))


# Added next to QUARTILE_UNAVAILABLE when a publication-basis quartile filter meets a paper published after the
# last SCImago year: no publication-year quartile can exist, and the latest quartile is deliberately not used.
FUTURE_YEAR_QUARTILE_REASON = "QUARTILE_PUBLICATION_YEAR_NOT_AVAILABLE"


# Reasons set by the venue filters (SCImago status, source type, quartile). A paper whose venue tier is admitted with
# --admit-tier has them waived; the APC, DOAJ, CWTS Core and retraction filters still apply.
VENUE_FILTER_REASON_PREFIXES = ("SOURCE_STATUS_", "SOURCE_TYPE_FILTER", "QUARTILE_")
ADMISSION_FIELDS = ("main_admission_tier", "main_waived_reasons")


def is_preprint(row: Dict[str, Any]) -> bool:
    """OpenAlex types the work as a preprint, or its source is a repository (arXiv, Zenodo, a university archive)."""
    return (safe_str(row.get("type")).casefold() == "preprint"
            or safe_str(row.get("source_type")).casefold() == "repository")


def _preprint_order(row: Dict[str, Any]) -> Tuple[int, float, float, str]:
    """Most cited first: top-10% flag, then citation percentile, then citation count."""
    def number(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return -1.0
    return (-int(_truthy(row.get("citation_top_10_percent"))), -number(row.get("citation_normalized_percentile")),
            -number(row.get("cited_by_count")), safe_str(row.get("title")))


def _scimago_last_year(manifest: Dict[str, Any]) -> Optional[int]:
    value = (manifest.get("sjr") or {}).get("last_year")
    return int(value) if value not in (None, "") else None


def _published_after_scimago(row: Dict[str, Any], last_year: Optional[int]) -> bool:
    """A paper published after the last SCImago year whose source is in the latest snapshot (LATEST_AVAILABLE)."""
    if row.get("ranking_basis") != "LATEST_AVAILABLE":
        return False
    try:
        year = int(row.get("publication_year"))
    except (TypeError, ValueError):
        return False
    return last_year is None or year > last_year


def _observed_source_type(row: Dict[str, Any]) -> str:
    """SCImago latest type, else SCImago historical type, else the OpenAlex source type."""
    return safe_str(
        row.get("scimago_type_latest") or row.get("scimago_type_historical") or row.get("source_type")
    ).strip()


def classify_source(row: Dict[str, Any]) -> str:
    """Return a human-auditable source class without inferring quality."""
    scimago_type = safe_str(row.get("scimago_type_latest") or row.get("scimago_type_historical")).strip().casefold()
    openalex_type = safe_str(row.get("source_type")).strip().casefold()
    observed = scimago_type or openalex_type
    source_name = safe_str(row.get("source")).strip()

    if not observed and not source_name:
        return "NO_SOURCE"
    if "conference" in observed or "proceedings" in observed:
        return "CONFERENCE"
    if observed == "journal" or "journal" in observed:
        return "JOURNAL"
    if "repository" in observed:
        return "REPOSITORY"
    if "ebook" in observed:
        return "EBOOK_PLATFORM"
    if "book series" in observed:
        return "BOOK_SERIES"
    if observed == "book" or observed.startswith("book "):
        return "BOOK"
    if not observed:
        return "NO_SOURCE_TYPE"
    return "OTHER"


def apply_main_policy(
    rows: List[Dict[str, Any]],
    manifest: Dict[str, Any],
    include_historical_only: bool = False,
    source_types: Optional[List[str]] = None,
    quartiles: Optional[List[str]] = None,
    quartile_basis: str = "latest",
    quartile_mode: str = "best",
    max_apc: Optional[float] = None,
    require_doaj: bool = False,
    require_cwts_core: bool = False,
    any_source_status: bool = False,
    exclude_retracted: bool = False,
    admit_tiers: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    admit = {tier for tier in (admit_tiers or []) if tier}
    source_types_norm = {x.casefold() for x in (source_types or []) if x}
    quartiles_norm = {x.upper() for x in (quartiles or []) if x}
    last_year = _scimago_last_year(manifest)
    default_require_latest = bool(
        (manifest.get("policy") or {}).get("require_present_in_latest_scimago_for_main_corpus", True)
    )
    # Allowed SCImago statuses for the main corpus. --include-historical-only adds HISTORICAL_ONLY;
    # before v3.0.4 it removed the status filter entirely, which also admitted UNMATCHED/AMBIGUOUS
    # rows. That broad behavior is now explicit: --any-source-status.
    if any_source_status or not default_require_latest:
        allowed_statuses: Optional[set] = None
    elif include_historical_only:
        allowed_statuses = {"ACTIVE_LATEST", "HISTORICAL_ONLY"}
    else:
        allowed_statuses = {"ACTIVE_LATEST"}

    main: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []

    for row in rows:
        row["source_class"] = classify_source(row)
        reasons: List[str] = []
        if allowed_statuses is not None and row.get("source_status") not in allowed_statuses:
            reasons.append(f"SOURCE_STATUS_{safe_str(row.get('source_status')) or 'UNKNOWN'}")

        if source_types_norm:
            observed_type = _observed_source_type(row).casefold()
            if observed_type not in source_types_norm:
                reasons.append("SOURCE_TYPE_FILTER")

        if quartiles_norm:
            q = _quartile_from_row(row, quartile_basis, quartile_mode).upper()
            if not q:
                reasons.append("QUARTILE_UNAVAILABLE")
                if quartile_basis != "latest" and _published_after_scimago(row, last_year):
                    reasons.append(FUTURE_YEAR_QUARTILE_REASON)
            elif q not in quartiles_norm:
                reasons.append(f"QUARTILE_{q}_NOT_SELECTED")

        if max_apc is not None:
            value = row.get("estimated_apc_usd")
            if value in (None, ""):
                reasons.append("APC_UNAVAILABLE")
            else:
                try:
                    if float(value) > float(max_apc):
                        reasons.append("APC_ABOVE_LIMIT")
                except (TypeError, ValueError):
                    reasons.append("APC_INVALID")

        if require_doaj and not _truthy(row.get("source_is_in_doaj")):
            reasons.append("NOT_IN_DOAJ")
        if require_cwts_core and not _truthy(row.get("source_is_core")):
            reasons.append("NOT_IN_CWTS_CORE")
        if exclude_retracted and _truthy(row.get("is_retracted")):
            reasons.append("RETRACTED")

        if admit:
            # The user's venue list admits this venue: the venue filters are waived and recorded, not hidden.
            tier = safe_str(row.get("venue_tier"))
            waived = [r for r in reasons if r.startswith(VENUE_FILTER_REASON_PREFIXES)] if tier in admit else []
            reasons = [r for r in reasons if r not in waived]
            row["main_admission_tier"] = tier if tier in admit else ""
            row["main_waived_reasons"] = "; ".join(waived)

        row["main_corpus"] = not reasons
        row["main_exclusion_reasons"] = "; ".join(reasons)
        if reasons:
            excluded.append(row)
        else:
            main.append(row)

    return main, excluded


def source_audit(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per OpenAlex source.

    Statuses and Sourceids are aggregated over all papers of the source instead of being copied
    from the first paper, so the result does not depend on input order. A source whose papers
    received different statuses (possible because publication-year matching is per year) is
    reported as MIXED with the per-status paper counts.
    """
    # Every descriptive column is the sorted set of distinct values over the source's papers
    # (joined with " | " when they differ), never the first paper's value.
    aggregated = {
        "source": lambda r: r.get("source"),
        "openalex_source_type": lambda r: r.get("source_type"),
        "scimago_source_type": lambda r: r.get("scimago_type_latest") or r.get("scimago_type_historical"),
        "source_class": lambda r: r.get("source_class") or classify_source(r),
        "issn_l": lambda r: r.get("issn_l"),
        "issn": lambda r: r.get("issn"),
        "latest_match_method": lambda r: r.get("latest_match_method"),
        "latest_match_candidates": lambda r: r.get("latest_match_candidates"),
        "historical_match_method": lambda r: r.get("historical_match_method"),
        "historical_snapshot_years": lambda r: r.get("historical_snapshot_years"),
        "match_diagnostic": lambda r: r.get("match_diagnostic"),
        "issn_conflict_candidates": lambda r: r.get("issn_conflict_candidates"),
    }
    # Venue tiers exist only when a venue list was given; they are listed after paper_count.
    with_tiers = any("venue_tier" in row for row in rows)
    if with_tiers:
        aggregated.update({field: (lambda r, f=field: r.get(f)) for field in VENUE_TIER_FIELDS})
    grouped: Dict[str, Dict[str, Any]] = {}
    statuses: Dict[str, Dict[str, int]] = {}
    sourceids: Dict[str, Dict[str, None]] = {}
    values: Dict[str, Dict[str, Dict[str, None]]] = {}
    for row in rows:
        key = safe_str(row.get("source_openalex_id")) or "|".join(
            [safe_str(row.get("source")), safe_str(row.get("issn_l")), safe_str(row.get("issn"))]
        )
        if key not in grouped:
            grouped[key] = {
                "source_openalex_id": row.get("source_openalex_id", ""),
                "source": "",
                "openalex_source_type": "",
                "scimago_source_type": "",
                "source_class": "",
                "issn_l": "",
                "issn": "",
                "source_status": "",
                "paper_status_counts": "",
                "scimago_sourceid": "",
                "latest_match_method": "",
                "latest_match_candidates": "",
                "historical_match_method": "",
                "historical_snapshot_years": "",
                "match_diagnostic": "",
                "issn_conflict_candidates": "",
                "paper_count": 0,
            }
            if with_tiers:
                grouped[key].update({field: "" for field in VENUE_TIER_FIELDS})
            statuses[key] = {}
            sourceids[key] = {}
            values[key] = {field: {} for field in aggregated}
        grouped[key]["paper_count"] += 1
        for field, getter in aggregated.items():
            value = safe_str(getter(row))
            if value:
                values[key][field][value] = None
        status = safe_str(row.get("source_status")) or "UNKNOWN"
        statuses[key][status] = statuses[key].get(status, 0) + 1
        sourceid = safe_str(row.get("scimago_sourceid")) or safe_str(row.get("scimago_sourceid_historical"))
        if sourceid:
            sourceids[key][sourceid] = None
    for key, entry in grouped.items():
        counts = statuses[key]
        entry["source_status"] = next(iter(counts)) if len(counts) == 1 else "MIXED"
        entry["paper_status_counts"] = "; ".join(f"{status}:{n}" for status, n in sorted(counts.items()))
        entry["scimago_sourceid"] = "; ".join(sorted(sourceids[key]))
        for field in aggregated:
            entry[field] = " | ".join(sorted(values[key][field]))
    result = list(grouped.values())
    result.sort(key=lambda row: (-int(row["paper_count"]), safe_str(row["source"])))
    return result


def _count_field(rows: List[Dict[str, Any]], field: str, default: str = "UNKNOWN") -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = safe_str(row.get(field)) or default
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _status_class_matrix(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    matrix: Dict[str, Dict[str, int]] = {}
    for row in rows:
        status = safe_str(row.get("source_status")) or "UNKNOWN"
        source_class = safe_str(row.get("source_class")) or "OTHER"
        bucket = matrix.setdefault(status, {})
        bucket[source_class] = bucket.get(source_class, 0) + 1
    return {status: dict(sorted(classes.items())) for status, classes in sorted(matrix.items())}


def build_summary(rows: List[Dict[str, Any]], main: List[Dict[str, Any]], excluded: List[Dict[str, Any]]) -> Dict[str, Any]:
    for row in rows:
        if not row.get("source_class"):
            row["source_class"] = classify_source(row)

    audit = source_audit(rows)
    match_methods: Dict[str, int] = {}
    quartiles_pub: Dict[str, int] = {}
    quartiles_latest: Dict[str, int] = {}
    for row in rows:
        method = safe_str(row.get("latest_match_method")) or "UNKNOWN"
        match_methods[method] = match_methods.get(method, 0) + 1
        # NOT_ASSIGNED: a SCImago row exists for that snapshot but carries no quartile ("-").
        # UNAVAILABLE: no SCImago row was used for that snapshot.
        has_pub_row = safe_str(row.get("sjr_year_at_publication")) != ""
        q = safe_str(row.get("best_quartile_at_publication")) or ("NOT_ASSIGNED" if has_pub_row else "UNAVAILABLE")
        quartiles_pub[q] = quartiles_pub.get(q, 0) + 1
        has_latest_row = row.get("source_status") == "ACTIVE_LATEST"
        ql = safe_str(row.get("best_quartile_latest")) or ("NOT_ASSIGNED" if has_latest_row else "UNAVAILABLE")
        quartiles_latest[ql] = quartiles_latest.get(ql, 0) + 1

    openalex_ids = [safe_str(row.get("openalex_id")) for row in rows if safe_str(row.get("openalex_id"))]
    quality: Dict[str, Any] = {
        "papers_retracted": sum(1 for row in rows if _truthy(row.get("is_retracted"))),
        # Rows retrieved before v3.1.0 carry no retraction flag; run `run` again to obtain it.
        "papers_without_retraction_flag": sum(1 for row in rows if safe_str(row.get("is_retracted")) == ""),
        "papers_without_fwci": sum(1 for row in rows if safe_str(row.get("fwci")) == ""),
    }
    if any("venue_tier" in row for row in rows):
        quality["paper_venue_tier_counts"] = _count_field(rows, "venue_tier")
        quality["unique_source_venue_tier_counts"] = _count_field(audit, "venue_tier")
    preprints = [row for row in rows if is_preprint(row)]
    quality["preprints"] = {
        "papers": len(preprints),
        "in_main_corpus": sum(1 for row in preprints if _truthy(row.get("main_corpus"))),
        "citation_top_10_percent": sum(1 for row in preprints if _truthy(row.get("citation_top_10_percent"))),
    }
    return {
        "tool_version": __version__,
        "total_enriched_records": len(rows),
        "main_corpus_records": len(main),
        "excluded_records": len(excluded),
        "paper_source_status_counts": _count_field(rows, "source_status"),
        "unique_source_status_counts": _count_field(audit, "source_status"),
        "unique_source_count_note": (
            "Unique sources are OpenAlex sources (source_audit rows). All papers without any source "
            "metadata are grouped into one NO_SOURCE row; MIXED marks a source whose papers have different statuses."
        ),
        "paper_source_class_counts": _count_field(rows, "source_class"),
        "unique_source_class_counts": _count_field(audit, "source_class"),
        "paper_source_status_by_class": _status_class_matrix(rows),
        "unique_source_status_by_class": _status_class_matrix(audit),
        "latest_match_method_counts": dict(sorted(match_methods.items())),
        "historical_match_method_counts": _count_field(
            [row for row in rows if row.get("historical_match_method")], "historical_match_method"
        ),
        "ranking_basis_counts": _count_field(rows, "ranking_basis"),
        "publication_quartile_counts": dict(sorted(quartiles_pub.items())),
        "latest_quartile_counts": dict(sorted(quartiles_latest.items())),
        "papers_with_publication_sourceid_differing_from_identity": sum(
            1 for row in rows if row.get("publication_sourceid_differs") in (True, "True")
        ),
        "papers_with_scimago_discontinued_label_latest": sum(
            1 for row in rows if row.get("scimago_discontinued_label_latest") in (True, "True")
        ),
        "duplicate_openalex_id_records": len(openalex_ids) - len(set(openalex_ids)),
        # Papers where a title candidate was rejected because its ISSNs contradict the paper's ISSNs.
        "match_diagnostic_counts": _count_field(
            [row for row in rows if row.get("match_diagnostic")], "match_diagnostic"
        ),
        **quality,
    }


def quartile_filter_diagnostics(
    excluded: List[Dict[str, Any]],
    manifest: Dict[str, Any],
    quartiles: Optional[List[str]] = None,
    quartile_basis: str = "latest",
    quartile_mode: str = "best",
    **_other_policy: Any,
) -> Dict[str, Any]:
    """Summary block, always written: papers that fail the quartile filter only because they were published after
    the last SCImago year (0 when no quartile filter is set or the basis is latest)."""
    return {
        "quartile_filter": sorted({q.upper() for q in (quartiles or []) if q}),
        "basis": quartile_basis,
        "mode": quartile_mode,
        "scimago_latest_year": _scimago_last_year(manifest),
        "future_year_papers_without_publication_quartile": sum(
            1 for row in excluded
            if FUTURE_YEAR_QUARTILE_REASON in safe_str(row.get("main_exclusion_reasons")).split("; ")
        ),
    }


def admission_summary(rows: List[Dict[str, Any]], admit_tiers: List[str]) -> Dict[str, Any]:
    """Summary block written when --admit-tier is used: papers that entered the main corpus only because their venue
    tier is admitted (the venue filters would have excluded them)."""
    admitted = [row for row in rows if _truthy(row.get("main_corpus")) and safe_str(row.get("main_waived_reasons"))]
    return {
        "admit_tiers": sorted({tier for tier in admit_tiers if tier}),
        "papers_in_admitted_tiers": sum(1 for row in rows if safe_str(row.get("main_admission_tier"))),
        "papers_admitted_by_waiving_venue_filters": _count_field(admitted, "main_admission_tier"),
    }


def _write_partitions(output_prefix: Path, enriched: List[Dict[str, Any]], main: List[Dict[str, Any]],
                      excluded: List[Dict[str, Any]]) -> Dict[str, str]:
    """Write enriched/main/excluded/audit/preprint files; main, excluded and preprints share the enriched columns."""
    paths = {
        "enriched_csv": Path(str(output_prefix) + "_enriched.csv"),
        "enriched_jsonl": Path(str(output_prefix) + "_enriched.jsonl"),
        "main_csv": Path(str(output_prefix) + "_main.csv"),
        "excluded_csv": Path(str(output_prefix) + "_excluded.csv"),
        "source_audit_csv": Path(str(output_prefix) + "_source_audit.csv"),
        "preprints_csv": Path(str(output_prefix) + "_preprints.csv"),
    }
    fieldnames = union_fieldnames(enriched)
    write_csv(paths["enriched_csv"], enriched, fieldnames)
    write_jsonl(paths["enriched_jsonl"], enriched)
    write_csv(paths["main_csv"], main, fieldnames)
    write_csv(paths["excluded_csv"], excluded, fieldnames)
    write_csv(paths["source_audit_csv"], source_audit(enriched))
    # Preprints for manual screening, most cited first; the file is written even when empty.
    write_csv(paths["preprints_csv"], sorted((row for row in enriched if is_preprint(row)), key=_preprint_order), fieldnames)
    return {name: str(path) for name, path in paths.items()}


def _environment_metadata() -> Dict[str, Any]:
    try:
        import requests

        requests_version = requests.__version__
    except Exception:  # pragma: no cover - requests is a hard dependency
        requests_version = ""
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "requests_version": requests_version,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def enrich_existing_file(
    input_path: Path,
    output_prefix: Path,
    cache_dir: Path,
    manifest: Dict[str, Any],
    relevant_categories: Optional[List[str]] = None,
    repository_state: Optional[Dict[str, Any]] = None,
    config_file: Optional[str] = None,
    settings_sources: Optional[Dict[str, str]] = None,
    venue_tiers: Optional[VenueTiers] = None,
    **policy_kwargs: Any,
) -> Dict[str, Any]:
    problem = admit_tier_problem(policy_kwargs.get("admit_tiers") or [], venue_tiers)
    if problem:
        raise ValueError(problem)
    if input_path.suffix.lower() == ".jsonl":
        rows = read_jsonl(input_path)
    else:
        rows = read_csv(input_path)
    enriched = enrich_records(rows, cache_dir, manifest, relevant_categories)
    if venue_tiers is not None:
        venue_tiers.assign(enriched)
    main, excluded = apply_main_policy(enriched, manifest, **policy_kwargs)
    outputs = _write_partitions(output_prefix, enriched, main, excluded)
    summary = build_summary(enriched, main, excluded)
    summary["quartile_filter_diagnostics"] = quartile_filter_diagnostics(excluded, manifest, **policy_kwargs)
    if policy_kwargs.get("admit_tiers"):
        summary["admission_by_venue_tier"] = admission_summary(enriched, policy_kwargs["admit_tiers"])
    if repository_state is not None:
        summary["scimago_repository"] = repository_state
    summary_json = Path(str(output_prefix) + "_summary.json")
    write_json(summary_json, summary)
    outputs["summary_json"] = str(summary_json)
    write_json(Path(str(output_prefix) + "_run_metadata.json"), {
        "tool": "Reprovane",
        "tool_version": __version__,
        "command": "enrich",
        "input_path": str(input_path),
        "input_sha256": sha256_file(input_path),
        "input_records": len(rows),
        "manifest_schema_version": manifest.get("schema_version"),
        "manifest_updated": manifest.get("updated"),
        "scimago_first_year": manifest["sjr"]["first_year"],
        "scimago_last_year": manifest["sjr"]["last_year"],
        "scimago_latest_snapshot_year": manifest["sjr"]["latest_snapshot_year"],
        "scimago_integrity_verified": (repository_state or {}).get("integrity_verified"),
        "config_file": config_file,
        "settings_sources": settings_sources or {},
        "relevant_categories": relevant_categories or [],
        "policy": policy_kwargs,
        "venue_tiers": venue_tiers.describe() if venue_tiers is not None else None,
        "environment": _environment_metadata(),
        "outputs": outputs,
    })
    return summary


def run_pipeline(
    queries: List[str],
    from_year: int,
    to_year: int,
    api_key: str,
    email: str,
    output_prefix: Path,
    cache_dir: Path,
    manifest_url: str = DEFAULT_MANIFEST_URL,
    require_abstract: bool = False,
    max_per_query: Optional[int] = None,
    no_resume: bool = False,
    offline: bool = False,
    refresh_scimago: bool = False,
    relevant_categories: Optional[List[str]] = None,
    policy_kwargs: Optional[Dict[str, Any]] = None,
    api_key_source: str = "",
    config_file: Optional[str] = None,
    settings_sources: Optional[Dict[str, str]] = None,
    venue_tiers: Optional[VenueTiers] = None,
    search_field: str = "search",
) -> Dict[str, Any]:
    policy_kwargs = policy_kwargs or {}
    # A query that the search field cannot carry stops the run before any SCImago or OpenAlex request.
    for query in queries:
        problem = search_query_problem(query, search_field)
        if problem:
            raise ValueError(f"query {query!r}: {problem}")
    problem = admit_tier_problem(policy_kwargs.get("admit_tiers") or [], venue_tiers)
    if problem:
        raise ValueError(problem)
    repo = sync_repository(
        cache_dir=cache_dir,
        manifest_url=manifest_url,
        offline=offline,
        force_redownload=refresh_scimago,
        verbose=True,
    )
    manifest = repo["manifest"]

    raw_jsonl = Path(str(output_prefix) + "_raw.jsonl")
    raw_csv = Path(str(output_prefix) + "_raw.csv")
    checkpoint = Path(str(output_prefix) + "_checkpoint.json")

    raw_rows, openalex_summary = download_queries(
        queries=queries,
        from_year=from_year,
        to_year=to_year,
        api_key=api_key,
        email=email,
        raw_jsonl_path=raw_jsonl,
        checkpoint_path=checkpoint,
        require_abstract=require_abstract,
        max_per_query=max_per_query,
        no_resume=no_resume,
        search_field=search_field,
    )
    write_csv(raw_csv, raw_rows)

    enriched = enrich_records(raw_rows, cache_dir, manifest, relevant_categories)
    if venue_tiers is not None:
        venue_tiers.assign(enriched)
    main, excluded = apply_main_policy(enriched, manifest, **policy_kwargs)

    summary_json = Path(str(output_prefix) + "_summary.json")
    metadata_json = Path(str(output_prefix) + "_run_metadata.json")
    outputs = _write_partitions(output_prefix, enriched, main, excluded)

    summary = build_summary(enriched, main, excluded)
    summary["quartile_filter_diagnostics"] = quartile_filter_diagnostics(excluded, manifest, **policy_kwargs)
    if policy_kwargs.get("admit_tiers"):
        summary["admission_by_venue_tier"] = admission_summary(enriched, policy_kwargs["admit_tiers"])
    summary["openalex"] = openalex_summary
    summary["scimago_repository"] = repo["state"]
    write_json(summary_json, summary)

    metadata = {
        "tool": "Reprovane",
        "tool_version": __version__,
        "command": "run",
        "queries": queries,
        "search_field": search_field,
        "from_year": from_year,
        "to_year": to_year,
        "require_abstract": require_abstract,
        "max_per_query": max_per_query,
        "openalex_email": email,
        # Only whether a key was configured and where it came from; the value is never recorded.
        "openalex_api_key_configured": bool(api_key),
        "openalex_api_key_source": api_key_source or ("unspecified" if api_key else "not configured"),
        "openalex_api_key_stored_in_output": False,
        "config_file": config_file,
        "settings_sources": settings_sources or {},
        "manifest_url": manifest_url,
        "manifest_schema_version": manifest.get("schema_version"),
        "manifest_updated": manifest.get("updated"),
        "scimago_first_year": manifest["sjr"]["first_year"],
        "scimago_last_year": manifest["sjr"]["last_year"],
        "scimago_latest_snapshot_year": manifest["sjr"]["latest_snapshot_year"],
        "scimago_integrity_verified": repo["state"].get("integrity_verified"),
        "relevant_categories": relevant_categories or [],
        "policy": policy_kwargs,
        "venue_tiers": venue_tiers.describe() if venue_tiers is not None else None,
        "environment": _environment_metadata(),
        "outputs": {
            "raw_csv": str(raw_csv),
            "raw_jsonl": str(raw_jsonl),
            **outputs,
            "summary_json": str(summary_json),
        },
    }
    write_json(metadata_json, metadata)
    return summary
