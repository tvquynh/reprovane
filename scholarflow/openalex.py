from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from . import __version__
from .common import normalize_doi, read_jsonl, redact_secrets, safe_str, short_openalex_id, write_json, write_jsonl

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
PER_PAGE = 100
REQUEST_TIMEOUT = 60
MAX_RETRIES = 8
USER_AGENT = f"ScholarFlow/{__version__}"
# Where OpenAlex matches the query: "search" sends the search parameter (title, abstract and, where OpenAlex has it,
# full text); "title_and_abstract" sends the title_and_abstract.search filter (title and abstract only).
SEARCH_FIELDS = ("search", "title_and_abstract")


def search_query_problem(query: str, search_field: str) -> Optional[str]:
    """Why `query` cannot be sent with `search_field`, or None. In a filter value ',' separates filters and '|'
    means OR between values, so a title_and_abstract query must contain neither."""
    if search_field not in SEARCH_FIELDS:
        return f"unknown search field {search_field!r} (allowed: {', '.join(SEARCH_FIELDS)})"
    if search_field == "title_and_abstract":
        for char, meaning in ((",", "separates OpenAlex filters"), ("|", "means OR between filter values")):
            if char in query:
                return f"contains {char!r}, which {meaning}; rewrite the query without it (join terms with OR)"
    return None


def reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    if not inverted_index:
        return ""
    positioned = []
    for word, positions in inverted_index.items():
        if isinstance(positions, list):
            for pos in positions:
                positioned.append((pos, word))
    positioned.sort(key=lambda item: item[0])
    return " ".join(word for _, word in positioned)


def extract_authors(authorships: List[Dict[str, Any]]) -> str:
    return "; ".join(
        item.get("author", {}).get("display_name")
        for item in authorships or []
        if item.get("author", {}).get("display_name")
    )


def extract_author_ids(authorships: List[Dict[str, Any]]) -> str:
    values = []
    for item in authorships or []:
        aid = item.get("author", {}).get("id")
        if aid:
            values.append(short_openalex_id(aid))
    return "; ".join(values)


def extract_orcids(authorships: List[Dict[str, Any]]) -> str:
    values = set()
    for item in authorships or []:
        value = item.get("author", {}).get("orcid")
        if value:
            values.add(value.replace("https://orcid.org/", "").strip())
    return "; ".join(sorted(values))


def extract_institutions(authorships: List[Dict[str, Any]]) -> str:
    values = set()
    for item in authorships or []:
        for institution in item.get("institutions", []) or []:
            name = institution.get("display_name")
            if name:
                values.add(name)
    return "; ".join(sorted(values))


def extract_institution_ids(authorships: List[Dict[str, Any]]) -> str:
    values = set()
    for item in authorships or []:
        for institution in item.get("institutions", []) or []:
            iid = institution.get("id")
            if iid:
                values.add(short_openalex_id(iid))
    return "; ".join(sorted(values))


def extract_countries(authorships: List[Dict[str, Any]]) -> str:
    values = set()
    for item in authorships or []:
        for country in item.get("countries", []) or []:
            if country:
                values.add(country)
        for institution in item.get("institutions", []) or []:
            code = institution.get("country_code")
            if code:
                values.add(code)
    return "; ".join(sorted(values))


def extract_source(work: Dict[str, Any]) -> Dict[str, Any]:
    source = (work.get("primary_location") or {}).get("source") or {}
    return {
        "source": safe_str(source.get("display_name")),
        "source_openalex_id": short_openalex_id(source.get("id")),
        "source_type": safe_str(source.get("type")),
        "issn_l": safe_str(source.get("issn_l")),
        "issn": "; ".join(source.get("issn") or []),
        "publisher": safe_str(source.get("host_organization_name")),
        "source_is_oa": source.get("is_oa"),
        "source_is_in_doaj": source.get("is_in_doaj"),
        "source_is_core": source.get("is_core"),
        "source_listed_in": "; ".join(source.get("listed_in") or []),
    }


def extract_topics(work: Dict[str, Any]) -> str:
    return "; ".join(
        item.get("display_name")
        for item in work.get("topics", []) or []
        if item.get("display_name")
    )


def extract_topic_ids(work: Dict[str, Any]) -> str:
    return "; ".join(
        short_openalex_id(item.get("id"))
        for item in work.get("topics", []) or []
        if item.get("id")
    )


def extract_keywords(work: Dict[str, Any]) -> str:
    values = []
    for item in work.get("keywords", []) or []:
        value = item.get("display_name") or item.get("keyword")
        if value:
            values.append(value)
    return "; ".join(values)


def work_to_record(work: Dict[str, Any], matched_query: str) -> Dict[str, Any]:
    authorships = work.get("authorships") or []
    source = extract_source(work)
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    oa = work.get("open_access") or {}
    best_oa = work.get("best_oa_location") or {}
    primary_topic = work.get("primary_topic") or {}
    percentile = work.get("citation_normalized_percentile") or {}
    referenced = work.get("referenced_works") or []
    record = {
        "openalex_id": short_openalex_id(work.get("id")),
        "doi": normalize_doi(work.get("doi")),
        "title": safe_str(work.get("title")),
        "display_name": safe_str(work.get("display_name")),
        "publication_year": work.get("publication_year"),
        "publication_date": safe_str(work.get("publication_date")),
        "language": safe_str(work.get("language")),
        "type": safe_str(work.get("type")),
        "type_crossref": safe_str(work.get("type_crossref")),
        "authors": extract_authors(authorships),
        "author_openalex_ids": extract_author_ids(authorships),
        "orcids": extract_orcids(authorships),
        "institutions": extract_institutions(authorships),
        "institution_openalex_ids": extract_institution_ids(authorships),
        "countries": extract_countries(authorships),
        **source,
        "cited_by_count": work.get("cited_by_count", 0),
        # Computed by OpenAlex at retrieval time (citations normalized by year and field); None when not computed.
        "fwci": work.get("fwci"),
        "citation_normalized_percentile": percentile.get("value"),
        "citation_top_1_percent": percentile.get("is_in_top_1_percent"),
        "citation_top_10_percent": percentile.get("is_in_top_10_percent"),
        "is_retracted": work.get("is_retracted"),
        "references_count": len(referenced),
        "topics": extract_topics(work),
        "topic_ids": extract_topic_ids(work),
        "primary_topic": safe_str(primary_topic.get("display_name")),
        "keywords": extract_keywords(work),
        "is_open_access": oa.get("is_oa", False),
        "oa_status": safe_str(oa.get("oa_status")),
        "oa_url": safe_str(oa.get("oa_url")),
        "best_oa_pdf_url": safe_str(best_oa.get("pdf_url")),
        "best_oa_landing_page": safe_str(best_oa.get("landing_page_url")),
        "has_abstract": bool(abstract),
        "abstract": abstract,
        "matched_query": matched_query,
        "relevance_score": work.get("relevance_score"),
    }
    return record


def dedup_key(record: Dict[str, Any]) -> str:
    doi = normalize_doi(record.get("doi"))
    if doi:
        return "doi:" + doi
    oid = safe_str(record.get("openalex_id")).strip()
    if oid:
        return "openalex:" + oid
    title = safe_str(record.get("title")).strip().casefold()
    year = safe_str(record.get("publication_year"))
    return f"title:{title}|year:{year}" if title else ""


def dedup_aliases(record: Dict[str, Any]) -> List[str]:
    """All identity keys of a record: DOI and OpenAlex ID, or title+year when neither exists."""
    aliases = []
    doi = normalize_doi(record.get("doi"))
    if doi:
        aliases.append("doi:" + doi)
    oid = safe_str(record.get("openalex_id")).strip()
    if oid:
        aliases.append("openalex:" + oid)
    if not aliases:
        key = dedup_key(record)
        if key:
            aliases.append(key)
    return aliases


class RecordStore:
    """Deduplicated records where a DOI and an OpenAlex ID both identify the same work.

    Keying only by DOI-or-ID kept two copies when a work was stored without a DOI and later
    returned with one (e.g. on resume after OpenAlex added the DOI).
    """

    def __init__(self) -> None:
        self.records: Dict[str, Dict[str, Any]] = {}
        self.alias_to_key: Dict[str, str] = {}

    def __len__(self) -> int:
        return len(self.records)

    def values(self):
        return self.records.values()

    def add(self, record: Dict[str, Any]) -> Optional[bool]:
        """Insert or merge; return True if new, False if merged, None if the record has no identity."""
        aliases = dedup_aliases(record)
        if not aliases:
            return None
        existing_key = next((self.alias_to_key[a] for a in aliases if a in self.alias_to_key), None)
        if existing_key is None:
            key = dedup_key(record)
            self.records[key] = record
            for alias in aliases:
                self.alias_to_key[alias] = key
            return True
        merge_record(self.records[existing_key], record)
        for alias in dedup_aliases(self.records[existing_key]):
            self.alias_to_key.setdefault(alias, existing_key)
        return False


def merge_record(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    old_queries = {q.strip() for q in safe_str(existing.get("matched_query")).split(" || ") if q.strip()}
    new_queries = {q.strip() for q in safe_str(incoming.get("matched_query")).split(" || ") if q.strip()}
    existing["matched_query"] = " || ".join(sorted(old_queries | new_queries))
    if len(safe_str(incoming.get("abstract"))) > len(safe_str(existing.get("abstract"))):
        existing["abstract"] = incoming.get("abstract", "")
        existing["has_abstract"] = bool(existing["abstract"])
    try:
        existing["cited_by_count"] = max(
            int(existing.get("cited_by_count") or 0),
            int(incoming.get("cited_by_count") or 0),
        )
    except (TypeError, ValueError):
        pass
    # A retraction reported by either copy is kept; citation metrics are filled only when missing.
    if incoming.get("is_retracted") in (True, "True", "true"):
        existing["is_retracted"] = True
    elif existing.get("is_retracted") in (None, "") and incoming.get("is_retracted") not in (None, ""):
        existing["is_retracted"] = incoming["is_retracted"]
    for field in ("fwci", "citation_normalized_percentile", "citation_top_1_percent", "citation_top_10_percent"):
        if existing.get(field) in (None, "") and incoming.get(field) not in (None, ""):
            existing[field] = incoming[field]
    # Fill identifiers that only the newer copy has (never overwrite existing values).
    if not safe_str(existing.get("doi")) and normalize_doi(incoming.get("doi")):
        existing["doi"] = normalize_doi(incoming.get("doi"))
    if not safe_str(existing.get("openalex_id")) and safe_str(incoming.get("openalex_id")):
        existing["openalex_id"] = incoming["openalex_id"]
    return existing


class OpenAlexClient:
    def __init__(self, api_key: str = "", email: str = "", base_url: Optional[str] = None, *,
                 session=None, timeout=(10, 60), max_attempts=None, max_response_bytes=16*1024*1024, sleeper=None):
        from urllib.parse import urlsplit
        import math
        from .openalex_http import PrivateValues
        self.api_key = api_key.strip()
        self.email = email.strip()
        self.base_url = base_url or OPENALEX_WORKS_URL
        if not isinstance(timeout,(tuple,list)) or len(timeout)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in timeout):
            raise ValueError('Finite positive connect/read timeouts required')
        parsed=urlsplit(self.base_url)
        if self.base_url != 'https://api.openalex.org/works' and not (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost') and parsed.path=='/works'):
            raise ValueError('Only official OpenAlex works or explicit loopback test endpoint is allowed')
        self.session = session if session is not None else requests.Session()
        self.session.trust_env=False
        self.session.auth=None
        self.total_cost_usd = 0.0
        self.timeout=timeout; self.max_attempts=max_attempts; self.max_response_bytes=max_response_bytes; self.sleeper=sleeper
        self.private=PrivateValues(self.api_key,self.email)

    def close(self): self.session.close()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()

    def _redact(self, text: Any) -> str:
        return redact_secrets(text, self.api_key)

    def _page(self,params,*,bearer=False):
        from .openalex_http import request_page,PrivateValues
        headers={'User-Agent':USER_AGENT,'Accept':'application/json','Accept-Encoding':'identity'}
        if bearer and self.api_key: headers['Authorization']='Bearer '+self.api_key
        private=PrivateValues(self.api_key,self.email,params.get('api_key'),params.get('mailto'))
        page=request_page(self.session,self.base_url,params,private=private,headers=headers,timeout=self.timeout,
            max_attempts=self.max_attempts if self.max_attempts is not None else MAX_RETRIES,
            max_bytes=self.max_response_bytes,sleeper=self.sleeper)
        try: self.total_cost_usd += float((page.data.get('meta') or {}).get('cost_usd') or 0)
        except (TypeError,ValueError): pass
        return page

    def request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # Preserve legacy parameter order/contract; this wrapper has no second transport.
        return self._page(params).data

    def request_page(self, params: Dict[str, Any]):
        if any(k in params for k in ('api_key','mailto','email','Authorization')):
            raise ValueError('Persisted acquisition parameters must be credential-free')
        return self._page(params,bearer=True)


def _load_checkpoint(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 2, "queries": {}}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _state_exhausted(state: Dict[str, Any]) -> bool:
    if "query_exhausted" in state:
        return bool(state.get("query_exhausted"))
    # Backward compatibility with v3.0.x checkpoints.
    return bool(state.get("done"))


def _public_state(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "signature": state.get("signature", {}),
        "cursor": state.get("cursor"),
        "retrieved": int(state.get("retrieved", 0)),
        "page": int(state.get("page", 0)),
        "matches_total": state.get("matches_total"),
        "query_exhausted": bool(state.get("query_exhausted", _state_exhausted(state))),
        "stopped_by_limit": bool(state.get("stopped_by_limit", False)),
        "has_more_results": bool(state.get("has_more_results", state.get("cursor"))),
        "resume_available": bool(state.get("resume_available", state.get("cursor"))),
        "run_completed_successfully": bool(state.get("run_completed_successfully", True)),
    }


def download_queries(
    queries: List[str],
    from_year: int,
    to_year: int,
    api_key: str,
    email: str,
    raw_jsonl_path: Path,
    checkpoint_path: Path,
    require_abstract: bool = False,
    max_per_query: Optional[int] = None,
    no_resume: bool = False,
    search_field: str = "search",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    # Every query is checked before any request or file change.
    for query in queries:
        problem = search_query_problem(query.strip(), search_field)
        if problem:
            raise ValueError(f"query {query.strip()!r}: {problem}")
    if no_resume:
        raw_jsonl_path.unlink(missing_ok=True)
        checkpoint_path.unlink(missing_ok=True)

    existing_rows = read_jsonl(raw_jsonl_path)
    records = RecordStore()
    for row in existing_rows:
        records.add(row)

    checkpoint = _load_checkpoint(checkpoint_path)
    checkpoint["version"] = 2
    client = OpenAlexClient(api_key=api_key, email=email)
    query_stats = []

    for q_index, query in enumerate(queries, start=1):
        query = query.strip()
        if not query:
            continue
        signature = {
            "query": query,
            "from_year": from_year,
            "to_year": to_year,
            "require_abstract": require_abstract,
        }
        if search_field != "search":
            # Only a non-default field enters the signature, so checkpoints written before 3.2.0 still resume.
            signature["search_field"] = search_field
        state = checkpoint.setdefault("queries", {}).get(query, {})
        if state and state.get("signature") != signature:
            raise RuntimeError(
                f"Checkpoint parameters changed for query {query!r}. Use --no-resume or a new --output prefix."
            )
        if _state_exhausted(state):
            print(f"[OpenAlex {q_index}/{len(queries)}] query already exhausted: {query}")
            query_stats.append({"query": query, "new_unique": 0, **_public_state(state)})
            continue

        cursor = state.get("cursor", "*")
        retrieved = int(state.get("retrieved", 0))
        page = int(state.get("page", 0))
        matches_total = state.get("matches_total")
        new_unique = 0
        stopped_by_limit = False

        print(f"\n[OpenAlex {q_index}/{len(queries)}] {query}")
        while True:
            remaining = None if max_per_query is None else max_per_query - retrieved
            if remaining is not None and remaining <= 0:
                stopped_by_limit = bool(cursor)
                state = {
                    "signature": signature,
                    "cursor": cursor,
                    "retrieved": retrieved,
                    "page": page,
                    "matches_total": matches_total,
                    "query_exhausted": False,
                    "stopped_by_limit": stopped_by_limit,
                    "has_more_results": bool(cursor),
                    "resume_available": bool(cursor),
                    "run_completed_successfully": True,
                }
                checkpoint["queries"][query] = state
                write_json(checkpoint_path, checkpoint)
                break

            per_page = PER_PAGE if remaining is None else min(PER_PAGE, remaining)
            filters = [
                f"from_publication_date:{from_year}-01-01",
                f"to_publication_date:{to_year}-12-31",
            ]
            if require_abstract:
                filters.append("has_abstract:true")
            params: Dict[str, Any] = {}
            if search_field == "title_and_abstract":
                filters.append(f"title_and_abstract.search:{query}")
            else:
                params["search"] = query
            params.update({"filter": ",".join(filters), "per_page": per_page, "cursor": cursor})
            if api_key:
                params["api_key"] = api_key
            if email:
                params["mailto"] = email

            data = client.request(params)
            meta = data.get("meta") or {}
            results = data.get("results") or []
            page += 1
            if matches_total is None:
                matches_total = meta.get("count")

            if not results:
                state = {
                    "signature": signature,
                    "cursor": None,
                    "retrieved": retrieved,
                    "page": page,
                    "matches_total": matches_total,
                    "query_exhausted": True,
                    "stopped_by_limit": False,
                    "has_more_results": False,
                    "resume_available": False,
                    "run_completed_successfully": True,
                }
                checkpoint["queries"][query] = state
                write_json(checkpoint_path, checkpoint)
                break

            for work in results:
                record = work_to_record(work, query)
                retrieved += 1
                if records.add(record):
                    new_unique += 1

            next_cursor = meta.get("next_cursor")
            cursor = next_cursor
            limit_reached = max_per_query is not None and retrieved >= max_per_query and bool(next_cursor)
            state = {
                "signature": signature,
                "cursor": cursor,
                "retrieved": retrieved,
                "page": page,
                "matches_total": matches_total,
                "query_exhausted": not bool(next_cursor),
                "stopped_by_limit": limit_reached,
                "has_more_results": bool(next_cursor),
                "resume_available": bool(next_cursor),
                "run_completed_successfully": True,
            }
            checkpoint["queries"][query] = state
            write_json(checkpoint_path, checkpoint)
            write_jsonl(raw_jsonl_path, records.values())
            print(
                f"  page={page} retrieved={retrieved:,} unique_global={len(records):,} "
                f"matches={matches_total if matches_total is not None else '?'}"
            )
            if not next_cursor or limit_reached:
                break
            time.sleep(0.05)

        state = checkpoint["queries"].get(query, state)
        if state.get("stopped_by_limit"):
            print(
                f"  SUCCESS: reached --max-per-query={max_per_query:,}; more OpenAlex results remain. "
                "Cursor saved for resume with a larger limit."
            )
        elif state.get("query_exhausted"):
            print("  SUCCESS: OpenAlex query fully exhausted.")

        query_stats.append({"query": query, "new_unique": new_unique, **_public_state(state)})

    final_rows = list(records.values())
    final_rows.sort(
        key=lambda row: (
            -(int(row.get("publication_year") or 0)),
            -(int(row.get("cited_by_count") or 0)),
            safe_str(row.get("title")),
        )
    )
    write_jsonl(raw_jsonl_path, final_rows)
    summary = {
        "search_field": search_field,
        "queries": query_stats,
        "unique_records": len(final_rows),
        "openalex_cost_usd_reported": round(client.total_cost_usd, 6),
    }
    return final_rows, summary
