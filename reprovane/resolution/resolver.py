"""Deterministic blocking, conservative decisions and SAME closure."""
from collections import defaultdict
from itertools import combinations
from . import CandidateBlockTooLargeError, ResolutionConflictError
from .normalization import VERSIONS
from .policy import digest


def pair_evidence(left: dict, right: dict, methods: list[str]) -> tuple[dict, list[str]]:
    a, b = set(left["valid_dois"]), set(right["valid_dois"])
    conflicts = set(left["evidence_warnings"] + right["evidence_warnings"])
    if a and b and not a & b:
        conflicts.add("DISTINCT_VALID_DOI")
    if len(a) > 1 or len(b) > 1:
        conflicts.add("MULTIPLE_VALID_DOI_CLAIMS")
    if left["publication_type_class"] != right["publication_type_class"]:
        conflicts.add("NOTICE_TYPE_CONFLICT")
    return dict(left_record_id=left["record_id"], right_record_id=right["record_id"], candidate_methods=methods,
                left=left, right=right, observation_ids_used=sorted(set(left["observation_ids_used"] + right["observation_ids_used"])),
                normalization_versions=VERSIONS), sorted(conflicts)


def generate(evidence: list[dict], limit: int) -> list[dict]:
    blocks = defaultdict(set)
    by_id = {e["record_id"]: e for e in evidence}
    for row in evidence:
        rid = row["record_id"]
        if row["provider_native_id"]:
            blocks[("SAME_PROVIDER_NATIVE_ID", (row["provider_namespace"], row["provider_native_id"]))].add(rid)
        for doi in row["valid_dois"]:
            blocks[("SHARED_VALID_DOI", (doi,))].add(rid)
        for title, year in row["title_year_keys"]:
            if not title:
                continue
            blocks[("EXACT_TITLE_YEAR", (title, year))].add(rid)
            if row["first_author_normalized"] and "AMBIGUOUS_AUTHORSHIP_POSITION" not in row["evidence_warnings"]:
                blocks[("EXACT_TITLE_FIRST_AUTHOR_YEAR", (title, year, row["first_author_normalized"]))].add(rid)
    # Validate EVERY block before enumerating or persisting any pair.
    for (method, key), ids in sorted(blocks.items()):
        if len(ids) > limit:
            raise CandidateBlockTooLargeError(method, len(ids), limit)
    pairs = defaultdict(set)
    for (method, key), ids in sorted(blocks.items()):
        for pair in combinations(sorted(ids), 2):
            pairs[pair].add(method)
    result = []
    for (left, right), methods in sorted(pairs.items()):
        methods = sorted(methods)
        proof, conflicts = pair_evidence(by_id[left], by_id[right], methods)
        result.append(dict(left_provider_record_id=left, right_provider_record_id=right,
                           candidate_methods=methods, evidence=proof, hard_conflicts=conflicts))
    return result


def automatic(evidence: dict, conflicts: list[str]) -> tuple[str, str]:
    if conflicts:
        return "AMBIGUOUS", "HARD_CONFLICT"
    if "SAME_PROVIDER_NATIVE_ID" in evidence["candidate_methods"]:
        return "SAME", "SAME_PROVIDER_NATIVE_ID"
    a, b = evidence["left"]["valid_dois"], evidence["right"]["valid_dois"]
    if len(a) == len(b) == 1 and a == b:
        return "SAME", "SINGLE_SHARED_VALID_DOI"
    if "EXACT_TITLE_FIRST_AUTHOR_YEAR" in evidence["candidate_methods"]:
        return "AMBIGUOUS", "CANDIDATE_ONLY_TITLE_AUTHOR_YEAR"
    if "EXACT_TITLE_YEAR" in evidence["candidate_methods"]:
        return "AMBIGUOUS", "CANDIDATE_ONLY_TITLE_YEAR"
    return "AMBIGUOUS", "INSUFFICIENT_STRONG_EVIDENCE"


def components(record_ids: list[str], candidates: list[dict], decisions: list[dict]) -> list[list[str]]:
    parent = {r: r for r in record_ids}
    def root(r):
        while parent[r] != r:
            parent[r] = parent[parent[r]]
            r = parent[r]
        return r
    by_candidate = {c["candidate_id"]: c for c in candidates}
    for d in decisions:
        if d["decision_type"] == "SAME":
            c = by_candidate[d["candidate_id"]]
            a, b = root(c["left_provider_record_id"]), root(c["right_provider_record_id"])
            parent[max(a, b)] = min(a, b)
    for d in decisions:
        c = by_candidate[d["candidate_id"]]
        if d["decision_type"] != "SAME" and root(c["left_provider_record_id"]) == root(c["right_provider_record_id"]):
            raise ResolutionConflictError("SAME closure contradicts an active DISTINCT or AMBIGUOUS pair")
    groups = defaultdict(list)
    for record in sorted(record_ids):
        groups[root(record)].append(record)
    return sorted(groups.values())


def entity_identity(members: list[str]) -> tuple[str, str]:
    membership_hash = digest(sorted(members))
    return "pub_" + membership_hash[:32], membership_hash
