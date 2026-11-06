"""Discovery candidate plans built on the shared Stage11B route/eligibility rules (make_candidates).

Discovery adds only an explicit, policy-frozen ordering (version, URL type, best-location flag, host,
evidence source) on top of the shared eligibility evaluation. Routes keep their per-source provenance.
"""
from ..fulltext_acquisition.candidates import make_candidates
from .policy import EVIDENCE_SOURCES, candidate_policy


def explicitly_closed(route):
    if route['evidence_provider'] == 'INPUT_ROW' or 'oa_evidence' in route:
        return route.get('oa_evidence') == 'FALSE'
    return route['is_oa'] is not True


def _route_rank(route, p):
    url_type = route['url_type'] if route['url_type'] in p['url_type_rank'] else 'OA_URL'
    return (p['url_type_rank'].index(url_type), 0 if route.get('is_best') else 1,
            EVIDENCE_SOURCES.index(route['evidence_provider']) if route['evidence_provider'] in EVIDENCE_SOURCES else 99)


def plan_work(work_id, routes, p, evidence_refs=(), invalid_urls=0, oa_flags=()):
    """Return the frozen plan for one work: ordered candidates, excluded routes and a no-candidate reason.

    invalid_urls counts input URLs refused at import (unsafe scheme/port, user-info, malformed); oa_flags are the
    work's input-row open-access flags (v4.1.0 reported a closed paper without URL as NOT_OPEN_ACCESS)."""
    enabled = set(p['evidence_sources_enabled'])
    usable, excluded = [], []
    for route in routes:
        if route['evidence_provider'] not in enabled:
            excluded.append(dict(route=route, reason='EVIDENCE_SOURCE_DISABLED'))
        elif p['oa_evidence_policy'] == 'NOT_EXPLICITLY_CLOSED' and explicitly_closed(route):
            excluded.append(dict(route=route, reason='OA_FLAG_FALSE'))
        else:
            usable.append(route)
    shared = candidate_policy(p)
    candidates = make_candidates(work_id, usable, shared)
    for c in candidates:
        eligible_routes = [r for r in c['routes'] if r['url_type'] != 'LANDING_PAGE']
        best = min((_route_rank(r, p) for r in eligible_routes), default=(len(p['url_type_rank']), 1, 99))
        c['discovery_rank'] = [p['version_rank'].index(c['version']), best[0], best[1],
                               p['host_rank'].index(c['host_type']), best[2], c['candidate_id']]
        c['url_type'] = min(eligible_routes, key=lambda r: _route_rank(r, p))['url_type'] if eligible_routes else c['url_type']
    candidates.sort(key=lambda c: (not c['eligible'], c['discovery_rank']))
    for rank, c in enumerate(candidates):
        c['rank'] = rank
    eligible = [c for c in candidates if c['eligible']]
    reason = None
    if not eligible:
        if not routes:
            reason = ('NOT_OPEN_ACCESS' if oa_flags and set(oa_flags) == {'FALSE'} else
                      'UNSAFE_OR_INVALID_URL' if invalid_urls else 'NO_OA_URL')
        elif not usable and all(e['reason'] == 'OA_FLAG_FALSE' for e in excluded):
            reason = 'NOT_OPEN_ACCESS'
        elif not usable:
            reason = 'EVIDENCE_SOURCE_DISABLED'
        else:
            reasons = {c['eligibility_reason'] for c in candidates}
            reason = sorted(reasons)[0] if len(reasons) == 1 else 'NO_ELIGIBLE_CANDIDATE'
    landing = sorted({r['url'] for r in routes if r['url_type'] == 'LANDING_PAGE'})
    return dict(schema='pdf-plan/1', work_id=work_id, candidates=candidates, excluded_routes=excluded, invalid_input_urls=invalid_urls,
                attempt_limit=p['per_item_candidate_limit'], landing_pages=landing, evidence=list(evidence_refs),
                no_candidate_reason=reason)
