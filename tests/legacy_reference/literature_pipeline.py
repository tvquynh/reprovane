"""Test-only predecessor command reference; never a ScholarFlow console entry point."""
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict

from scholarflow import __version__
from scholarflow._version import LEGACY_PREDECESSOR_VERSION
from scholarflow.common import write_csv, write_json
from scholarflow.config import Config, ConfigError, load_config
from scholarflow.fulltext import DEFAULT_DELAY, DEFAULT_MAX_MB, REQUEST_TIMEOUT, fetch_open_access
from scholarflow.openalex import SEARCH_FIELDS, search_query_problem
from scholarflow.pipeline import FUTURE_YEAR_QUARTILE_REASON, enrich_existing_file, load_queries, run_pipeline
from scholarflow.scimago import source_history
from scholarflow.scimago_repo import (
    DEFAULT_MANIFEST_URL,
    RepositoryError,
    load_repository_metadata,
    sync_repository,
    validate_cached_repository,
)
from scholarflow.venues import VenueTierError, VenueTiers, admit_tier_problem

# No personal default: the OpenAlex mailto comes from --email or config.json openalex.email (else none is sent).
DEFAULT_EMAIL = ""
DEFAULT_CACHE = Path("data") / "scimago"
DEFAULT_FROM_YEAR = 2010
DEFAULT_TO_YEAR = 2026
PROJECT_DIR = Path(__file__).resolve().parent


def add_repo_args(parser: argparse.ArgumentParser) -> None:
    # Defaults are None so that CLI > config.json > code default can be resolved after parsing.
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Private JSON config (default: config.json next to literature_pipeline.py, if present)",
    )
    parser.add_argument("--manifest-url", default=None, help=f"Default: config.json scimago.manifest_url, else {DEFAULT_MANIFEST_URL}")
    parser.add_argument("--cache-dir", type=Path, default=None, help=f"Default: config.json scimago.cache_dir, else {DEFAULT_CACHE}")
    parser.add_argument("--offline", action="store_true", help="Use verified local SCImago cache only")


def resolve_settings(args: argparse.Namespace, config: Config) -> Dict[str, str]:
    """Apply CLI > config.json > code default in place; return {setting: source}. Secret values are never returned."""
    sources: Dict[str, str] = {}

    def pick(name: str, cli_value: Any, section: str, key: str, default: Any) -> Any:
        if cli_value is not None:
            sources[name] = "cli"
            return cli_value
        value = config.get(section, key)
        if value is not None:
            sources[name] = config.label
            return value
        sources[name] = "default"
        return default

    if hasattr(args, "cache_dir"):  # commands that use the SCImago repository (fetch-oa does not)
        args.cache_dir = Path(pick("cache_dir", args.cache_dir, "scimago", "cache_dir", DEFAULT_CACHE))
        args.manifest_url = pick("manifest_url", args.manifest_url, "scimago", "manifest_url", DEFAULT_MANIFEST_URL)
    if hasattr(args, "venue_tiers"):  # commands with screening options (run, enrich)
        venue_tiers = pick("venue_tiers", args.venue_tiers, "screening", "venue_tiers", None)
        args.venue_tiers = Path(venue_tiers) if venue_tiers else None
        if args.no_admit_tiers:
            args.admit_tiers, sources["admit_tiers"] = [], "cli"
        else:
            args.admit_tiers = list(pick("admit_tiers", args.admit_tier, "screening", "admit_tiers", []))
    if args.command == "run":
        args.from_year = pick("from_year", args.from_year, "run", "from_year", DEFAULT_FROM_YEAR)
        args.to_year = pick("to_year", args.to_year, "run", "to_year", DEFAULT_TO_YEAR)
        args.max_per_query = pick("max_per_query", args.max_per_query, "run", "max_per_query", None)
        args.require_abstract = bool(pick("require_abstract", args.require_abstract, "run", "require_abstract", False))
        args.search_field = pick("search_field", args.search_field, "run", "search_field", "search")
        args.email = pick("email", args.email, "openalex", "email", DEFAULT_EMAIL)
        # API key: CLI > config.json > legacy OPENALEX_API_KEY environment variable (deprecated).
        if args.api_key:
            args.api_key_source = "cli"
        elif config.get("openalex", "api_key"):
            args.api_key, args.api_key_source = config.get("openalex", "api_key"), config.label
        elif os.environ.get("OPENALEX_API_KEY", "").strip():
            args.api_key, args.api_key_source = os.environ["OPENALEX_API_KEY"].strip(), "environment"
        else:
            args.api_key, args.api_key_source = "", ""
        sources["api_key"] = args.api_key_source or "not configured"
    return sources


def add_policy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--include-historical-only",
        action="store_true",
        help="Also admit HISTORICAL_ONLY sources (in an earlier SCImago snapshot, absent from latest) to the main corpus",
    )
    parser.add_argument(
        "--any-source-status",
        action="store_true",
        help=(
            "Do not filter the main corpus by SCImago source status at all (also admits UNMATCHED and AMBIGUOUS); "
            "this was the behavior of --include-historical-only before v3.0.4"
        ),
    )
    parser.add_argument(
        "--source-type",
        action="append",
        default=None,
        help=(
            'Allowed SCImago source type; repeat as needed. Default: journal only. '
            'Example: --source-type journal --source-type "conference and proceedings"'
        ),
    )
    parser.add_argument(
        "--all-source-types",
        action="store_true",
        help="Disable the default journal-only main-corpus filter",
    )
    parser.add_argument(
        "--quartile",
        action="append",
        choices=["Q1", "Q2", "Q3", "Q4"],
        default=[],
        help="Allowed quartile; repeat as needed",
    )
    parser.add_argument(
        "--quartile-basis",
        choices=["publication", "latest"],
        default="latest",
        help=(
            "Quartile used by --quartile: latest (default; the venue's quartile in the latest SCImago snapshot, also for "
            "papers published after it) or publication (the quartile of the paper's publication year, which papers "
            "newer than the SCImago data do not have)"
        ),
    )
    parser.add_argument(
        "--quartile-mode",
        choices=["best", "relevant-category"],
        default="best",
        help="Filter using SJR Best Quartile or matched relevant-category quartile",
    )
    parser.add_argument(
        "--relevant-category",
        action="append",
        default=[],
        help='Relevant SCImago category; repeat as needed, e.g. "Computer Science Applications"',
    )
    parser.add_argument("--max-apc", type=float, default=None, help="Maximum estimated APC (USD) from the APC snapshot")
    parser.add_argument("--require-doaj", action="store_true", help="Require OpenAlex source_is_in_doaj=True")
    parser.add_argument("--require-cwts-core", action="store_true", help="Require OpenAlex source_is_core=True")
    parser.add_argument(
        "--exclude-retracted",
        action="store_true",
        help="Exclude works that OpenAlex marks as retracted (is_retracted=True) from the main corpus",
    )
    parser.add_argument(
        "--venue-tiers",
        type=Path,
        default=None,
        help=(
            "Your venue list (TSV, or CSV by extension; columns tier, venue, openalex_source_id, issn, "
            "title_exact, title_contains). Adds venue_tier, venue_tier_venue and venue_tier_match; filters nothing "
            "unless --admit-tier is given. Default: config.json screening.venue_tiers, else none. "
            "See examples/venue_tiers_example.tsv"
        ),
    )
    parser.add_argument(
        "--admit-tier",
        action="append",
        default=None,
        help=(
            "Admit papers whose venue has this tier in the --venue-tiers list to the main corpus even when the venue "
            "filters (SCImago status, source type, quartile) would exclude them, e.g. conferences absent from SCImago; "
            "APC, DOAJ, CWTS Core and retraction filters still apply. Repeat as needed. "
            "Default: config.json screening.admit_tiers, else none"
        ),
    )
    parser.add_argument(
        "--no-admit-tiers",
        action="store_true",
        help="Admit no tier, overriding screening.admit_tiers in config.json (for a journal-only view)",
    )


def policy_kwargs(args: argparse.Namespace):
    if args.quartile_mode == "relevant-category" and args.quartile and not args.relevant_category:
        raise SystemExit("--quartile-mode relevant-category requires at least one --relevant-category")
    effective_source_types = [] if args.all_source_types else (args.source_type or ["journal"])
    return {
        "include_historical_only": args.include_historical_only,
        "source_types": effective_source_types,
        "quartiles": args.quartile,
        "quartile_basis": args.quartile_basis,
        "quartile_mode": args.quartile_mode,
        "max_apc": args.max_apc,
        "require_doaj": args.require_doaj,
        "require_cwts_core": args.require_cwts_core,
        "any_source_status": args.any_source_status,
        "exclude_retracted": args.exclude_retracted,
        "admit_tiers": args.admit_tiers,
    }


def load_venue_tiers(args: argparse.Namespace):
    """Return (VenueTiers or None, error message or None); the list and the admitted tiers are checked before any
    network access."""
    tiers = None
    if args.venue_tiers is not None:
        try:
            tiers = VenueTiers.load(args.venue_tiers)
        except VenueTierError as exc:
            return None, f"ERROR (venue list): {exc}"
    problem = admit_tier_problem(args.admit_tiers, tiers)
    if problem:
        return None, f"ERROR (venue list): {problem}"
    return tiers, None


def print_quality_summary(summary: Dict[str, Any], args: argparse.Namespace) -> None:
    print(f"  retracted (OpenAlex is_retracted): {summary['papers_retracted']:,}"
          + (" (excluded from the main corpus)" if args.exclude_retracted else ""))
    missing = summary["papers_without_retraction_flag"]
    if missing:
        print(f"  NOTE: {missing:,} records carry no retraction flag (retrieved before v3.1.0); "
              "run `run` again to obtain is_retracted, fwci and citation_normalized_percentile.")
    if "paper_venue_tier_counts" in summary:
        print("  paper venue tier:", summary["paper_venue_tier_counts"])
    admission = summary.get("admission_by_venue_tier")
    if admission:
        print(f"  admitted by venue tier {', '.join(admission['admit_tiers'])} (venue filters waived):",
              admission["papers_admitted_by_waiving_venue_filters"])
    preprints = summary.get("preprints") or {}
    if preprints.get("papers"):
        print(f"  preprints (OpenAlex type preprint or a repository source): {preprints['papers']:,}, "
              f"{preprints['in_main_corpus']:,} in the main corpus, {preprints['citation_top_10_percent']:,} in the "
              f"top 10% by citations; listed most cited first in {args.output}_preprints.csv for manual screening")


def print_quartile_warning(summary: Dict[str, Any]) -> None:
    """Warn when a publication-basis quartile filter excluded papers published after the last SCImago year."""
    diagnostics = summary.get("quartile_filter_diagnostics") or {}
    count = diagnostics.get("future_year_papers_without_publication_quartile") or 0
    if not count:
        return
    last = diagnostics.get("scimago_latest_year")
    subject, them, their = ((f"1 paper published after {last} has", "it", "its") if count == 1
                            else (f"{count:,} papers published after {last} have", "them", "their"))
    print(f"  WARNING: SCImago data ends at {last}. {subject} no publication-year quartile and "
          f"failed the current quartile filter ({FUTURE_YEAR_QUARTILE_REASON}). Use --quartile-basis latest (the "
          f"default) to screen {them} using the latest available SCImago snapshot (not a quartile of {their} "
          "publication year).")


def command_sync(args: argparse.Namespace) -> int:
    result = sync_repository(
        cache_dir=args.cache_dir,
        manifest_url=args.manifest_url,
        offline=args.offline,
        force_redownload=args.force_redownload,
        verbose=True,
    )
    print("\nSCImago repository ready")
    for key, value in result["state"].items():
        print(f"  {key}: {value}")
    state = result["state"]
    if state.get("integrity_verified"):
        print("\nSUCCESS: All required SCImago files are present locally and passed SHA-256 verification.")
        if state.get("downloaded_files", 0):
            print(f"  Newly downloaded this run: {state['downloaded_files']}")
        if state.get("reused_files", 0):
            print(f"  Reused from verified local cache: {state['reused_files']}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    result = validate_cached_repository(args.cache_dir, args.manifest_url, args.offline)
    print(f"metadata_source: {result['metadata_source']}")
    print(f"overall_ok: {result['ok']}")
    for row in result["files"]:
        status = "OK" if row["sha256_ok"] else "FAIL"
        print(f"[{status}] {row['file']}")
    return 0 if result["ok"] else 2


def command_run(args: argparse.Namespace) -> int:
    queries = load_queries(args.query, args.query_file)
    if not queries:
        raise SystemExit("Provide --query and/or --query-file")
    venue_tiers, error = load_venue_tiers(args)
    if error:
        print(error, file=sys.stderr)
        return 2
    for query in queries:
        problem = search_query_problem(query, args.search_field)
        if problem:
            print(f"ERROR (query, --search-field {args.search_field}): {query!r} {problem}", file=sys.stderr)
            return 2
    if not args.api_key:
        # Fail before any SCImago or OpenAlex request: discovery needs a key.
        print(
            "ERROR (config): `run` needs an OpenAlex API key, but none is configured.\n"
            f"  Copy config.example.json to config.json in {PROJECT_DIR} and set \"openalex\": {{\"api_key\": \"...\"}},\n"
            "  or point to another private file with --config <path>.\n"
            "  (--api-key and the OPENALEX_API_KEY environment variable still work but are discouraged.)\n"
            "  Commands that do not query OpenAlex (sync-scimago, validate-repo, enrich, inspect-source) need no key.",
            file=sys.stderr,
        )
        return 2
    print(f"OpenAlex API key: configured (source: {args.api_key_source}; value not shown)")
    if args.api_key_source == "cli":
        print("WARNING: --api-key is visible in shell history and process lists; prefer openalex.api_key in config.json.")
    elif args.api_key_source == "environment":
        print("NOTE: using the OPENALEX_API_KEY environment variable (deprecated); prefer openalex.api_key in config.json.")
    field_note = ("title and abstract only" if args.search_field == "title_and_abstract"
                  else "title, abstract and full text where OpenAlex has it")
    print(f"OpenAlex search field: {args.search_field} ({field_note})")
    summary = run_pipeline(
        queries=queries,
        from_year=args.from_year,
        to_year=args.to_year,
        api_key=args.api_key,
        api_key_source=args.api_key_source,
        config_file=args.config_file,
        settings_sources=args.settings_sources,
        email=args.email,
        output_prefix=args.output,
        cache_dir=args.cache_dir,
        manifest_url=args.manifest_url,
        require_abstract=args.require_abstract,
        max_per_query=args.max_per_query,
        no_resume=args.no_resume,
        offline=args.offline,
        refresh_scimago=args.refresh_scimago,
        relevant_categories=args.relevant_category,
        policy_kwargs=policy_kwargs(args),
        venue_tiers=venue_tiers,
        search_field=args.search_field,
    )
    print("\nFINAL SUMMARY")
    print(f"  enriched: {summary['total_enriched_records']:,}")
    print(f"  main corpus: {summary['main_corpus_records']:,}")
    print(f"  excluded: {summary['excluded_records']:,}")
    print("  paper source status:", summary["paper_source_status_counts"])
    print("  unique source status:", summary["unique_source_status_counts"])
    print("  paper source class:", summary["paper_source_class_counts"])
    unmatched = summary.get("paper_source_status_by_class", {}).get("UNMATCHED", {})
    if unmatched:
        print("  unmatched paper breakdown:", unmatched)
    print_quality_summary(summary, args)
    print_quartile_warning(summary)
    for q in summary.get("openalex", {}).get("queries", []):
        if q.get("stopped_by_limit"):
            print(
                f"  OpenAlex query '{q.get('query')}' completed successfully at the configured limit; "
                "more results remain and the cursor was saved."
            )
    print(f"\nOutput prefix: {args.output}")
    return 0


def command_enrich(args: argparse.Namespace) -> int:
    venue_tiers, error = load_venue_tiers(args)
    if error:
        print(error, file=sys.stderr)
        return 2
    repo = sync_repository(
        cache_dir=args.cache_dir,
        manifest_url=args.manifest_url,
        offline=args.offline,
        force_redownload=args.refresh_scimago,
        verbose=True,
    )
    summary = enrich_existing_file(
        input_path=args.input,
        output_prefix=args.output,
        cache_dir=args.cache_dir,
        manifest=repo["manifest"],
        relevant_categories=args.relevant_category,
        repository_state=repo["state"],
        config_file=args.config_file,
        settings_sources=args.settings_sources,
        venue_tiers=venue_tiers,
        **policy_kwargs(args),
    )
    print("\nENRICH SUMMARY")
    print(f"  enriched: {summary['total_enriched_records']:,}")
    print(f"  main corpus: {summary['main_corpus_records']:,}")
    print(f"  excluded: {summary['excluded_records']:,}")
    print("  paper source status:", summary["paper_source_status_counts"])
    print("  unique source status:", summary["unique_source_status_counts"])
    print("  paper source class:", summary["paper_source_class_counts"])
    unmatched = summary.get("paper_source_status_by_class", {}).get("UNMATCHED", {})
    if unmatched:
        print("  unmatched paper breakdown:", unmatched)
    print_quality_summary(summary, args)
    print_quartile_warning(summary)
    if summary.get("duplicate_openalex_id_records"):
        print(f"  WARNING: input contains {summary['duplicate_openalex_id_records']} duplicate OpenAlex-ID records (kept, not removed).")
    print(f"\nOutput prefix: {args.output}")
    return 0


def command_inspect_source(args: argparse.Namespace) -> int:
    repo = sync_repository(
        cache_dir=args.cache_dir,
        manifest_url=args.manifest_url,
        offline=args.offline,
        force_redownload=False,
        verbose=False,
    )
    match, history, apc, source_status = source_history(args.cache_dir, repo["manifest"], args.title or "", args.issn or "")
    latest_year = repo["manifest"]["sjr"]["latest_snapshot_year"]
    print(f"match_status: {match.status}")
    print(f"source_status: {source_status}")
    print(f"match_method: {match.method}")
    print(f"sourceid: {match.sourceid}")
    if match.candidates:
        print("candidates:", ", ".join(match.candidates))
    notes = {
        "ACTIVE_LATEST": f"present in the latest SCImago snapshot ({latest_year}); this is not proof of current Scopus indexing.",
        "HISTORICAL_ONLY": (
            f"present in earlier SCImago snapshots but absent from the latest snapshot ({latest_year}); "
            "no latest SJR/quartile exists. This alone is not evidence of Scopus discontinuation or low quality."
        ),
        "UNMATCHED": "no SCImago snapshot matched; this alone is not evidence of predatory status or low quality.",
        "AMBIGUOUS": "more than one SCImago source matched; resolve manually (e.g. with --issn). Nothing was auto-selected.",
    }
    if match.method == "TITLE_MATCH_ISSN_CONFLICT":
        print(
            "note: the title matched the SCImago source(s) listed as candidates, but their ISSNs contradict the "
            "given ISSN, so no match was accepted (a title never overrides contradictory ISSN evidence)."
        )
    else:
        print(f"note: {notes.get(source_status, '')}")
    if not history:
        return 1 if source_status in {"UNMATCHED", "AMBIGUOUS"} else 0
    print("\nYEAR  SJR       Q   H-index  Title")
    for row in history:
        sjr = "" if row.get("sjr") is None else f"{row['sjr']:.3f}"
        print(f"{row['year']:4}  {sjr:8}  {row.get('best_quartile') or '-':2}  {str(row.get('h_index') or ''):7}  {row.get('title','')}")
    present_years = {int(row["year"]) for row in history}
    if int(latest_year) not in present_years:
        print(f"{int(latest_year):4}  (absent from the latest snapshot: no latest SJR/quartile)")
    if apc:
        print("\nAPC snapshot")
        print(f"  year: {apc.get('apc_data_year')}")
        print(f"  estimated_apc_usd: {apc.get('estimated_apc_usd')}")
        print(f"  estimated_value_usd: {apc.get('estimated_value_usd')}")
    if args.output:
        write_csv(Path(str(args.output) + "_history.csv"), history)
        write_json(Path(str(args.output) + "_apc.json"), apc or {})
    return 0


def command_fetch_oa(args: argparse.Namespace) -> int:
    if not args.input.is_file():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2
    if args.delay < 1.0:
        print("ERROR: --delay must be at least 1 second (one request per host per second at most).", file=sys.stderr)
        return 2
    if args.timeout <= 0 or args.max_mb <= 0 or (args.limit is not None and args.limit < 1):
        print("ERROR: --timeout and --max-mb must be positive, and --limit at least 1.", file=sys.stderr)
        return 2
    output_dir = args.output_dir or args.input.with_name(args.input.stem + "_fulltext")
    print(f"Output folder: {output_dir}")
    try:
        summary = fetch_open_access(
            input_path=args.input,
            output_dir=output_dir,
            delay=args.delay,
            timeout=args.timeout,
            max_mb=args.max_mb,
            limit=args.limit,
            retry_failed=args.retry_failed,
        )
    except KeyboardInterrupt:
        print("\nInterrupted. Progress, blocked hosts and the manual-download list were saved; run the same command to continue.",
              file=sys.stderr)
        return 130
    print("\nFETCH-OA SUMMARY")
    print(f"  papers in input: {summary['papers']:,}")
    print(f"  downloaded open-access PDFs: {summary['downloaded']:,} (folder pdf)")
    if summary["manually_downloaded"]:
        print(f"  registered manual downloads: {summary['manually_downloaded']:,} (folder pdf_manual)")
    print(f"  to download by hand: {summary['manual_list']:,} (manual_download.html, .csv, .ris)")
    print("  status:", summary["status_counts"])
    if summary["blocked_hosts"]:
        print("  blocked hosts, never requested again (see blocked_hosts.json):", ", ".join(summary["blocked_hosts"]))
    if summary["paused_hosts_this_run"]:
        print("  hosts paused for this run only (rate limit, server busy or unreadable robots.txt):",
              ", ".join(summary["paused_hosts_this_run"]))
    print(f"  requests sent this run: {summary['requests_sent_this_run']:,}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(f"OpenAlex Literature Intelligence v{LEGACY_PREDECESSOR_VERSION}: discovery + SCImago historical enrichment + "
                     "venue screening + open-access full text")
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync-scimago", help="Download/verify the SCImago mirror into local cache")
    add_repo_args(p_sync)
    p_sync.add_argument("--force-redownload", action="store_true")
    p_sync.set_defaults(func=command_sync)

    p_validate = sub.add_parser("validate-repo", help="Validate cached SCImago files against SHA256SUMS.txt")
    add_repo_args(p_validate)
    p_validate.set_defaults(func=command_validate)

    p_run = sub.add_parser("run", help="Run OpenAlex discovery, SCImago enrichment, and main-corpus screening")
    add_repo_args(p_run)
    add_policy_args(p_run)
    p_run.add_argument("--query", action="append", default=[])
    p_run.add_argument("--query-file", type=Path)
    p_run.add_argument("--from-year", type=int, default=None, help=f"Default: config.json run.from_year, else {DEFAULT_FROM_YEAR}")
    p_run.add_argument("--to-year", type=int, default=None, help=f"Default: config.json run.to_year, else {DEFAULT_TO_YEAR}")
    p_run.add_argument("--require-abstract", action="store_true", default=None, help="Default: config.json run.require_abstract, else off")
    p_run.add_argument("--max-per-query", type=int, default=None, help="Default: config.json run.max_per_query, else no limit")
    p_run.add_argument(
        "--search-field",
        choices=list(SEARCH_FIELDS),
        default=None,
        help=(
            "Where OpenAlex matches the query: search (title, abstract and full text where OpenAlex has it) or "
            "title_and_abstract (title and abstract only; the query may not contain ',' or '|'). "
            "Default: config.json run.search_field, else search"
        ),
    )
    p_run.add_argument(
        "--api-key",
        default="",
        help="Discouraged (visible in shell history/process lists). Prefer openalex.api_key in config.json.",
    )
    p_run.add_argument("--email", default=None, help="OpenAlex mailto. Default: config.json openalex.email, else none")
    p_run.add_argument("--output", type=Path, default=Path("literature"))
    p_run.add_argument("--no-resume", action="store_true")
    p_run.add_argument("--refresh-scimago", action="store_true")
    p_run.set_defaults(func=command_run)

    p_enrich = sub.add_parser("enrich", help="Enrich an existing OpenAlex CSV/JSONL corpus with SCImago data")
    add_repo_args(p_enrich)
    add_policy_args(p_enrich)
    p_enrich.add_argument("--input", type=Path, required=True)
    p_enrich.add_argument("--output", type=Path, required=True)
    p_enrich.add_argument("--refresh-scimago", action="store_true")
    p_enrich.set_defaults(func=command_enrich)

    p_inspect = sub.add_parser("inspect-source", help="Show a source's SCImago trajectory from 2010 to latest")
    add_repo_args(p_inspect)
    p_inspect.add_argument("--title", default="")
    p_inspect.add_argument("--issn", default="")
    p_inspect.add_argument("--output", type=Path)
    p_inspect.set_defaults(func=command_inspect_source)

    p_fetch = sub.add_parser(
        "fetch-oa",
        help="Download open-access PDFs of a corpus; hosts that answer with a bot check are never retried",
    )
    p_fetch.add_argument("--config", type=Path, default=None, help=argparse.SUPPRESS)
    p_fetch.add_argument("--input", type=Path, required=True, help="A run/enrich output: *_main.csv, *_enriched.csv/.jsonl or *_raw.csv")
    p_fetch.add_argument("--output-dir", type=Path, default=None, help="Default: <input name>_fulltext next to the input file")
    p_fetch.add_argument("--delay", type=float, default=DEFAULT_DELAY, help=f"Seconds between requests to one host (default {DEFAULT_DELAY}, minimum 1)")
    p_fetch.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT, help=f"Seconds per request (default {REQUEST_TIMEOUT})")
    p_fetch.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help=f"Largest file accepted, in MB (default {DEFAULT_MAX_MB})")
    p_fetch.add_argument("--limit", type=int, default=None, help="Try at most this many papers in this run (pilot)")
    p_fetch.add_argument(
        "--retry-failed",
        action="store_true",
        help="Also retry papers that failed permanently before (HTTP 4xx, not a PDF, robots.txt); blocked hosts stay blocked",
    )
    p_fetch.set_defaults(func=command_fetch_oa)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config, PROJECT_DIR)
    except ConfigError as exc:
        print(f"ERROR (config): {exc}", file=sys.stderr)
        return 2
    args.config_file = str(config.path) if config.path else None
    args.settings_sources = resolve_settings(args, config)
    print(f"Config: {config.path if config.path else 'none (CLI arguments and built-in defaults only)'}")
    if getattr(args, "from_year", 0) and getattr(args, "to_year", 0) and args.from_year > args.to_year:
        parser.error("--from-year must be <= --to-year")
    try:
        return int(args.func(args))
    except RepositoryError as exc:
        # Integrity/availability failures are expected operational outcomes: report them plainly.
        print(f"ERROR (SCImago repository): {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
