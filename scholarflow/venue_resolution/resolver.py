from collections import defaultdict
from itertools import combinations
from . import VenueCandidateBlockTooLargeError,ResolutionConflictError
from .policy import METHODS,digest


def node_identity(namespace,native,members):
    h=digest(dict(provider_namespace=namespace,provider_native_id=native,provider_record_ids=sorted(members)))
    return 'node_'+h[:32],h


def aggregate(evidence):
    fields=['titles','title_exact_keys','title_normalized_keys','title_status_normalized_keys','issn','eissn','issn_l',
            'valid_issn_family_union','source_types','publisher_names','country_codes','observation_ids_used','invalid_issn_observation_ids']
    result={key:sorted({v for e in evidence for v in e[key]}) for key in fields}
    result.update(provider_namespace=evidence[0]['provider_namespace'],provider_native_id=evidence[0]['provider_native_id'],
                  snapshot_releases=sorted({e['snapshot_release'] for e in evidence if e['snapshot_release']}))
    return result


def form_nodes(evidence):
    groups=defaultdict(list)
    for row in evidence:
        key=(row['provider_namespace'],row['provider_native_id'] or ('singleton:'+row['record_id']))
        groups[key].append(row)
    result=[]
    for key,rows in sorted(groups.items()):
        members=sorted(r['record_id'] for r in rows); proof=aggregate(rows)
        node,h=node_identity(proof['provider_namespace'],proof['provider_native_id'],members)
        result.append(dict(provider_node_id=node,provider_namespace=proof['provider_namespace'],
            provider_native_id=proof['provider_native_id'],member_manifest_sha256=h,member_count=len(members),
            evidence=proof,provider_record_ids=members))
    return sorted(result,key=lambda r:r['provider_node_id'])


def generate(nodes,limit):
    blocks=defaultdict(set); by_id={n['provider_node_id']:n for n in nodes}
    for n in nodes:
        p=n['evidence']; nid=n['provider_node_id']
        for value in set(p['issn']+p['eissn']): blocks[('ISSN_EXACT',value)].add(nid)
        for value in p['valid_issn_family_union']: blocks[('ISSNL_EXACT',value)].add(nid)
        for method,field in [('TITLE_EXACT','title_exact_keys'),('TITLE_NORMALIZED','title_normalized_keys'),('TITLE_STATUS_NORMALIZED','title_status_normalized_keys')]:
            for value in p[field]: blocks[(method,value)].add(nid)
    for (method,key),ids in sorted(blocks.items()):
        if len(ids)>limit: raise VenueCandidateBlockTooLargeError(method,len(ids),limit)
    pairs=defaultdict(lambda:defaultdict(list))
    for (method,key),ids in sorted(blocks.items()):
        for a,b in combinations(sorted(ids),2):
            if method=='ISSNL_EXACT' and key not in set(by_id[a]['evidence']['issn_l']+by_id[b]['evidence']['issn_l']): continue
            pairs[(a,b)][method].append(dict(block_size=len(ids),provider_node_ids=sorted(ids),block_sha256=digest([method,key])))
    result=[]
    for (a,b),matches in sorted(pairs.items()):
        primary=next(m for m in METHODS if m in matches); left,right=by_id[a]['evidence'],by_id[b]['evidence']; conflicts=[]
        if left['provider_namespace']==right['provider_namespace'] and left['provider_native_id'] and right['provider_native_id'] and left['provider_native_id']!=right['provider_native_id']:
            conflicts.append('DISTINCT_PROVIDER_NATIVE_ID_SAME_NAMESPACE')
        if primary.startswith('TITLE_') and left['valid_issn_family_union'] and right['valid_issn_family_union'] and not set(left['valid_issn_family_union'])&set(right['valid_issn_family_union']):
            conflicts.append('TITLE_MATCH_ISSN_CONFLICT')
        if any(c['block_size']!=2 for c in matches[primary]): conflicts.append('METHOD_BLOCK_AMBIGUOUS')
        methods=sorted(matches)
        result.append(dict(left_provider_node_id=a,right_provider_node_id=b,primary_method=primary,candidate_methods=methods,
            block_context=matches,evidence=dict(left=left,right=right,candidate_methods=methods,primary_method=primary,block_context=matches),
            hard_conflicts=sorted(conflicts)))
    return result


def automatic(evidence,conflicts):
    if conflicts: return 'AMBIGUOUS','HARD_CONFLICT'
    if any(c['block_size']!=2 for c in evidence['block_context'][evidence['primary_method']]): return 'AMBIGUOUS','METHOD_BLOCK_AMBIGUOUS'
    return 'SAME',evidence['primary_method']


def components(ids,candidates,decisions):
    parent={n:n for n in ids}
    def root(n):
        while parent[n]!=n: parent[n]=parent[parent[n]]; n=parent[n]
        return n
    by={c['candidate_id']:c for c in candidates}
    for d in decisions:
        if d['decision_type']=='SAME':
            c=by[d['candidate_id']]; a,b=root(c['left_provider_node_id']),root(c['right_provider_node_id']); parent[max(a,b)]=min(a,b)
    for d in decisions:
        c=by[d['candidate_id']]
        if d['decision_type']!='SAME' and root(c['left_provider_node_id'])==root(c['right_provider_node_id']): raise ResolutionConflictError('Venue SAME closure contradicts an explicit non-SAME decision')
    groups=defaultdict(list)
    for n in sorted(ids): groups[root(n)].append(n)
    return sorted(groups.values())


def entity_identity(members):
    h=digest(sorted(members)); return 'ven_'+h[:32],h
