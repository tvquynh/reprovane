"""Deterministic work identity from exact identifiers only (OpenAlex Work ID, then DOI).

Records are never merged by title similarity. Identifier disagreements are kept as explicit issues for
human reconciliation rather than being resolved by guesswork.
"""
from collections import defaultdict

from ..corpus_release.model import digest
from .identifiers import legacy_doi_key



class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def work_id(run_id, key):
    return 'pw_' + digest([run_id, key])[:40]


def match_key(rec):
    """The record's 4.1.0 file-name key when it is safe for automatic file matching, else None.

    The input adapter marks a key trusted only when it can be recomputed from the validated identifiers (or, for a
    record without identifiers, from the content fields 4.1.0 hashed). Its shape alone proves nothing: a key copied
    from an invalid identifier value is kept for display and legacy fetch-state replay, and a manual file carrying
    it needs confirmation.
    """
    return rec['legacy_key'] if rec['legacy_key'] and rec.get('legacy_key_trusted') is True else None


def freeze(run_id, records):
    """records: ordered (record_id, record) pairs of ACCEPTED rows. Returns (works, identity summary)."""
    union = _Union()
    for rid, rec in records:
        union.find(rid)
        for w in rec['openalex_ids']:
            union.union(rid, 'W:' + w)
    owners = defaultdict(set)
    for rid, rec in records:
        if rec['openalex_ids']:
            for d in rec['dois']:
                owners[d].add(union.find(rid))
    issues, ambiguous = [], set()
    for rid, rec in records:
        if rec['openalex_ids'] or not rec['dois']:
            continue
        groups = sorted({o for d in rec['dois'] for o in owners.get(d, ())})
        if len(groups) == 1:
            union.union(rid, groups[0])
        elif groups:
            # A DOI-only record whose DOI belongs to several OpenAlex works stays separate.
            ambiguous.add(rid)
            issues.append(dict(kind='DOI_RECORD_MATCHES_SEVERAL_OPENALEX_WORKS', record_ids=[rid], dois=rec['dois']))
        else:
            for d in rec['dois']:
                union.union(rid, 'D:' + d)
    # Records without a persistent identifier are never merged: equal titles or equal title-derived keys do not
    # prove that two rows describe one publication. Shared keys are reported as LEGACY_KEY_COLLISION.
    groups = defaultdict(list)
    for position, (rid, rec) in enumerate(records):
        groups[union.find(rid)].append((position, rid, rec))
    works = []
    for members in sorted(groups.values(), key=lambda m: m[0][0]):
        recs = [r for _, _, r in members]
        openalex = sorted({w for r in recs for w in r['openalex_ids']})
        dois = sorted({d for r in recs for d in r['dois']})
        flags = []
        if openalex:
            key = 'openalex:' + openalex[0]
        elif dois:
            key = 'doi:' + dois[0]
            if members[0][1] in ambiguous:
                key += '#ambiguous:' + members[0][1]
                flags.append('AMBIGUOUS_DOI_OWNERSHIP')
        else:
            key = 'row:' + members[0][1]
            flags.append('NO_PERSISTENT_IDENTIFIER')
        if len(openalex) > 1:
            flags.append('MULTIPLE_OPENALEX_IDS_EXACT_LINKED')
        if len(dois) > 1:
            flags.append('MULTIPLE_DOIS')
        legacy = sorted({r['legacy_key'] for r in recs if r['legacy_key']} | set(openalex) |
                        {legacy_doi_key(d) for d in dois})
        safe = sorted({k for k in map(match_key, recs) if k} | set(openalex) | {legacy_doi_key(d) for d in dois})
        works.append(dict(work_key=key, openalex_ids=openalex, dois=dois, primary_legacy_key=recs[0]['legacy_key'],
                          match_keys=safe,
                          titles=sorted({t for r in recs for t in r['titles']})[:10],
                          publication_years=sorted({r['publication_year'] for r in recs if r['publication_year'] is not None}),
                          record_ids=[rid for _, rid, _ in members], legacy_keys=legacy,
                          corpus_item_ids=sorted({r['corpus_item_id'] for r in recs if r.get('corpus_item_id')}),
                          oa_flags=sorted({r['oa_flag'] for r in recs}), flags=sorted(flags)))
    legacy_owners, doi_owners = defaultdict(list), defaultdict(list)
    for w in works:
        w['work_id'] = work_id(run_id, w['work_key'])
        for k in w['legacy_keys']:
            legacy_owners[k].append(w['work_id'])
        for d in w['dois']:
            doi_owners[d].append(w['work_id'])
    for d, ws in sorted(doi_owners.items()):
        if len(ws) > 1:
            issues.append(dict(kind='DOI_SHARED_BY_SEVERAL_WORKS', dois=[d], work_ids=sorted(ws)))
    for k, ws in sorted(legacy_owners.items()):
        if len(ws) > 1:
            issues.append(dict(kind='LEGACY_KEY_COLLISION', legacy_key=k, work_ids=sorted(ws)))
    for w in works:
        # The suggested file name: the 4.1.0 key when it is safe and unique, otherwise the opaque work ID.
        primary = w['primary_legacy_key']
        unique = primary in w['match_keys'] and len(legacy_owners.get(primary, ())) == 1
        w['file_key'] = primary if unique else w['work_id']
    for w in works:
        if 'MULTIPLE_DOIS' in w['flags'] and w['openalex_ids']:
            issues.append(dict(kind='MULTIPLE_DOIS_FOR_OPENALEX_WORK', work_ids=[w['work_id']], dois=w['dois']))
    summary = dict(schema='pdf-identity/1', policy='EXACT_IDENTIFIERS_NO_TITLE_MERGE', accepted_records=len(records),
                   unique_works=len(works), duplicate_records=len(records) - len(works),
                   issues=sorted(issues, key=digest), work_index_sha256=digest([w['work_key'] for w in works]))
    return works, summary
