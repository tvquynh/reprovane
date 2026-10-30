"""Evidence routes remain distinct; operational candidates deduplicate exact URL per item."""
from urllib.parse import urlsplit
from ..corpus_release.model import digest
from .safety import normalize_url,sanitize_url
from ..core import ValidationError


def location_routes(data,provider,link,evidence_id):
    routes=[]
    if provider=='UNPAYWALL':
        locations=[('best_oa_location',data.get('best_oa_location'))]+[(f'oa_locations/{i}',v) for i,v in enumerate(data.get('oa_locations') or [])]
    else:
        locations=[(k,data.get(k)) for k in ('primary_location','best_oa_location')]+[(f'locations/{i}',v) for i,v in enumerate(data.get('locations') or [])]
    for anchor,loc in locations:
        if not isinstance(loc,dict): continue
        source=loc.get('source') or {}; up=provider=='UNPAYWALL'
        host=loc.get('host_type') if up else ('repository' if source.get('type')=='repository' else 'publisher' if source.get('type') in ('journal','conference') else 'unknown')
        for key,kind in ([('url_for_pdf','DIRECT_PDF'),('url_for_landing_page','LANDING_PAGE')] if up else [('pdf_url','DIRECT_PDF'),('landing_page_url','LANDING_PAGE')]):
            url=loc.get(key)
            if not isinstance(url,str): continue
            try: normalized=normalize_url(url); safe=sanitize_url(url)
            except ValidationError: continue
            routes.append(dict(evidence_provider=provider,evidence_id=evidence_id,source_record=anchor,
                linkage=link,url=safe,url_identity_sha256=digest(normalized),url_type=kind,
                runtime_url_required=(safe!=normalized),host_type=host if host in ('publisher','repository') else 'unknown',
                version=loc.get('version') if loc.get('version') in ('publishedVersion','acceptedVersion','submittedVersion') else 'UNKNOWN',
                is_oa=True if up else loc.get('is_oa') is True,license=loc.get('license'),display_name=source.get('display_name'),is_best=bool(loc.get('is_best') or anchor=='best_oa_location')))
    if provider=='OPENALEX' and (data.get('has_content') or {}).get('pdf'):
        url=(data.get('content_urls') or {}).get('pdf')
        if not url: url='https://content.openalex.org/works/'+link.rsplit('/',1)[-1]+'.pdf'
        try:
            normalized=normalize_url(url); safe=sanitize_url(url)
            if urlsplit(normalized).hostname!='content.openalex.org': return routes
        except ValidationError: return routes
        routes.append(dict(evidence_provider='OPENALEX_CONTENT',evidence_id=evidence_id,source_record='content_urls/pdf',linkage=link,url=safe,url_identity_sha256=digest(normalized),url_type='OPENALEX_CONTENT_PDF',runtime_url_required=(safe!=normalized),host_type='openalex_content',version='UNKNOWN',is_oa=(data.get('open_access') or {}).get('is_oa') is True,license=None,display_name='OpenAlex cached PDF; original copyright retained',is_best=False))
    return routes


def make_candidates(item_id,routes,policy):
    groups={}
    for r in routes:
        if r['evidence_provider'] not in policy['provider_sources_enabled']: continue
        groups.setdefault(r['url_identity_sha256'],[]).append(r)
    result=[]
    for identity,rs in sorted(groups.items()):
        rs=sorted({digest(r):r for r in rs}.values(),key=digest)
        versions={r['version'] for r in rs}; conflict=len(versions-{'UNKNOWN'})>1
        version=next(iter(versions-{'UNKNOWN'}),'UNKNOWN')
        if conflict: reason='VERSION_CONFLICT'
        elif version=='submittedVersion' and version not in policy['allowed_versions']: reason='ONLY_SUBMITTED_VERSION'
        elif version=='UNKNOWN' and not policy['allow_unknown_version']: reason='ONLY_UNKNOWN_VERSION'
        elif version!='UNKNOWN' and version not in policy['allowed_versions']: reason='VERSION_NOT_ALLOWED'
        else: reason=None
        # An eligible route must itself support version, direct-PDF and OA; never combine unrelated claims.
        eligible=[r for r in rs if (r['version'] in policy['allowed_versions'] or r['version']=='UNKNOWN' and policy['allow_unknown_version']) and r['url_type']!='LANDING_PAGE' and (r['is_oa'] or not policy['require_oa_evidence']) and urlsplit(r['url']).scheme in policy['allowed_schemes']]
        if not reason and not eligible: reason='NO_DIRECT_PDF' if all(r['url_type']=='LANDING_PAGE' for r in rs) else 'NO_ACCEPTABLE_OA'
        ranked=sorted(eligible or rs,key=lambda r:(policy['version_rank'].index(r['version']),policy['host_rank'].index(r['host_type']),r['url_type']=='LANDING_PAGE',not r['is_oa'],r['evidence_provider'],digest(r)))
        best=ranked[0]; cid='ftc_'+digest([item_id,identity])[:40]
        rank=[policy['version_rank'].index(best['version']),policy['host_rank'].index(best['host_type']),int(best['url_type']=='LANDING_PAGE'),int(not best['is_oa']),best['evidence_provider'],cid]
        result.append(dict(candidate_id=cid,corpus_item_id=item_id,url=best['url'],url_identity_sha256=identity,routes=rs,eligible=reason is None,eligibility_reason=reason or 'ELIGIBLE',rank_components=rank,version=best['version'],host_type=best['host_type'],url_type=best['url_type'],runtime_url_required=best['runtime_url_required']))
    return sorted(result,key=lambda c:(not c['eligible'],c['rank_components']))
