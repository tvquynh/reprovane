"""Durable acquisition over the exact, verified Stage11 handoff; never rebuild membership."""
import json
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import quote
from ..core import ValidationError,ArtifactIntegrityError
from ..core.ids import new_id,utc_now
from ..core.migrations import apply_migrations
from ..core.artifacts import ContentAddressedArtifactStore
from ..core.provenance import canonical_json
from ..corpus_release import CorpusReleaseStore
from ..corpus_release.model import digest,sha
from ..resolution.normalization import normalize_doi
from .policy import FullTextPolicy
from .safety import guard,normalize_url,sanitize_url,validate_pdf
from .candidates import location_routes,make_candidates
from .transport import SafeTransport,FetchError


class FullTextStore:
    def __init__(self,database):
        self.db=database; self.releases=CorpusReleaseStore(database)
        self.cas=ContentAddressedArtifactStore(database.workspace)
        if not hasattr(database,'_fulltext_auth'): database._fulltext_auth=None
        database.connection.create_function('fulltext_authorized',1,lambda rid:int(database._fulltext_auth==rid))
        apply_migrations(database.connection,target_version=12)

    @contextmanager
    def write(self,rid):
        old=self.db._fulltext_auth; self.db._fulltext_auth=rid
        try:
            with self.db.transaction(): yield
        finally: self.db._fulltext_auth=old

    def insert(self,table,row):
        keys=list(row)
        self.db.execute('INSERT INTO '+table+' ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',tuple(row[k] for k in keys))

    def rows(self,table,rid): return [dict(r) for r in self.db.execute('SELECT * FROM '+table+' WHERE run_id=? ORDER BY rowid',(rid,))]
    def get(self,rid):
        r=self.db.execute('SELECT * FROM fulltext_run WHERE run_id=?',(rid,)).fetchone()
        if r is None: raise ValidationError('Unknown full-text run')
        return dict(r)
    def policy(self,rid): return FullTextPolicy.from_value(json.loads(self.get(rid)['policy_json'])).as_dict()

    def create(self,release_id,policy):
        p=FullTextPolicy.from_value(policy); handoff=self.releases.handoff(release_id)
        rid=new_id('ftr')
        with self.write(rid):
            old=self.db.execute('SELECT run_id FROM fulltext_run WHERE release_id=? AND policy_sha256=?',(release_id,p.sha256)).fetchone()
            if old:
                if self.get(old[0])['handoff_json']!=canonical_json(handoff): raise ArtifactIntegrityError('Handoff drift')
                self.verify(old[0])
                return self.inspect(old[0])
            self.insert('fulltext_run',dict(run_id=rid,release_id=release_id,release_manifest_sha256=handoff['release_manifest_sha256'],policy_json=p.canonical,policy_sha256=p.sha256,handoff_json=canonical_json(handoff),handoff_sha256=digest(handoff),state='OPEN',manifest_json=None,manifest_sha256=None,created_at=utc_now()))
            for item in handoff['items']:
                self.insert('fulltext_input',dict(run_id=rid,corpus_item_id=item['corpus_item_id'],input_json=canonical_json(item),input_sha256=digest(item)))
        return self.inspect(rid)

    def _context(self,rid):
        run=self.get(rid); p=FullTextPolicy.from_value(json.loads(run['policy_json']))
        handoff=self.releases.handoff(run['release_id'])
        inputs=[json.loads(r['input_json']) for r in self.rows('fulltext_input',rid)]
        if p.sha256!=run['policy_sha256'] or canonical_json(handoff)!=run['handoff_json'] or digest(handoff)!=run['handoff_sha256'] or handoff['release_manifest_sha256']!=run['release_manifest_sha256'] or inputs!=handoff['items']:
            raise ArtifactIntegrityError('Full-text source/policy drift')
        if any(digest(json.loads(r['input_json']))!=r['input_sha256'] for r in self.rows('fulltext_input',rid)):
            raise ArtifactIntegrityError('Input hash mismatch')
        return run,p.as_dict(),inputs

    def budget(self,rid):
        req=self.rows('fulltext_request',rid); results={r['request_id']:json.loads(r['result_json']) for r in self.rows('fulltext_request_result',rid)}
        attempts=self.rows('fulltext_attempt',rid); cmap={r['candidate_id']:r['corpus_item_id'] for r in self.rows('fulltext_candidate',rid)}
        return dict(items=len({cmap[a['candidate_id']] for a in attempts}),http_requests=len(req),
            bytes=sum(results[r['request_id']]['bytes'] if r['request_id'] in results else json.loads(r['request_json'])['reserved_bytes'] for r in req),
            artifacts=len(self.rows('fulltext_artifact',rid)),provider_lookups=len({json.loads(r['request_json'])['lookup_identity'] for r in req if json.loads(r['request_json'])['purpose']=='METADATA'}))

    def _fetch(self,rid,transport,url,purpose,max_bytes,identity):
        p=self.policy(rid)
        def reserve(safe,purpose,requested):
            with self.write(rid):
                b=self.budget(rid); cap=p['global_download_budget']; remaining=cap['bytes']-b['bytes']
                # Reserve one extra bounded stream chunk to account for oversize detection.
                reserved=requested+65536
                if b['http_requests']>=cap['http_requests'] or remaining<reserved: raise FetchError('GLOBAL_BUDGET_EXHAUSTED')
                lookups={json.loads(r['request_json'])['lookup_identity'] for r in self.rows('fulltext_request',rid) if json.loads(r['request_json'])['purpose']=='METADATA'}
                if purpose=='METADATA' and identity not in lookups and len(lookups)>=cap['provider_lookups']: raise FetchError('GLOBAL_BUDGET_EXHAUSTED')
                tid=new_id('fth'); request=dict(url=safe,purpose=purpose,reserved_bytes=reserved,lookup_identity=identity)
                try: guard(request,(transport.key,transport.email))
                except ValidationError: raise FetchError('PRIVATE_REQUEST_REFUSED') from None
                self.insert('fulltext_request',dict(run_id=rid,request_id=tid,request_json=canonical_json(request),request_sha256=digest(request),created_at=utc_now()))
            return tid,requested
        def complete(tid,result):
            guard(result,(transport.key,transport.email))
            with self.write(rid): self.insert('fulltext_request_result',dict(run_id=rid,request_id=tid,result_json=canonical_json(result),result_sha256=digest(result),finished_at=utc_now()))
        if transport.p!=p: raise ValidationError('Transport must use exact frozen policy')
        return transport.fetch(url,purpose=purpose,max_bytes=max_bytes,reserve=reserve,complete=complete,provider='UNPAYWALL' if identity.startswith('UNPAYWALL:') else None)

    def _lookup(self,rid,provider,link,transport):
        run,p,items=self._context(rid)
        allowed={v for item in items for v in (item['openalex_work_ids'] if provider=='OPENALEX' else item['dois'])}
        if run['state']!='OPEN' or provider not in ('OPENALEX','UNPAYWALL') or provider not in p['provider_sources_enabled'] or link not in allowed:
            raise ValidationError('Lookup must use an enabled provider and exact sealed handoff identifier')
        eid='fte_'+digest([provider,link])[:40]
        old=next((r for r in self.rows('fulltext_evidence',rid) if r['evidence_id']==eid),None)
        if old: return old
        url='https://api.openalex.org/works/'+link.rsplit('/',1)[-1] if provider=='OPENALEX' else 'https://api.unpaywall.org/v2/'+quote(link,safe='/')
        metadata=dict(provider=provider,linkage=link,request_url=url,authentication_mode='runtime_email_parameter' if provider=='UNPAYWALL' else 'runtime_bearer_header',parser='fulltext-provider/1',routes=[],locations_count=None)
        requests_before={r['request_id'] for r in self.rows('fulltext_request',rid)}
        response_sha=None
        try:
            body,http=self._fetch(rid,transport,url,'METADATA',self.policy(rid)['max_metadata_bytes'],provider+':'+link)
            guard(body,(transport.key,transport.email)); data=json.loads(body)
            if not isinstance(data,dict): raise FetchError('INVALID_PROVIDER_JSON')
            if provider=='OPENALEX' and data.get('id')!='https://openalex.org/'+link.rsplit('/',1)[-1]: raise FetchError('PROVIDER_ID_MISMATCH')
            if provider=='UNPAYWALL' and normalize_doi(data.get('doi'))!=link: raise FetchError('PROVIDER_ID_MISMATCH')
            routes=location_routes(data,provider,link,eid)
            # Exact raw bytes are preserved only when safe; never archive signed secrets.
            def check_urls(v):
                if isinstance(v,dict):
                    for val in v.values(): check_urls(val)
                elif isinstance(v,list):
                    for val in v: check_urls(val)
                elif isinstance(v,str) and v.startswith(('https://','http://')):
                    try:
                        if sanitize_url(v)!=normalize_url(v): raise FetchError('PRIVATE_RESPONSE_REFUSED')
                    except ValidationError: pass
            check_urls(data)
            response_sha=self.cas.put_bytes(body).artifact_sha256
            metadata.update(http_status=http['http_status'],failure_class=None,routes=routes,locations_count=data.get('locations_count'),raw_response_preserved=True)
        except FetchError as exc: metadata.update(http_status=None,failure_class=exc.reason,raw_response_preserved=False)
        except (ValueError,ValidationError): metadata.update(http_status=None,failure_class='INVALID_OR_PRIVATE_PROVIDER_RESPONSE',raw_response_preserved=False)
        metadata['request_ids']=[r['request_id'] for r in self.rows('fulltext_request',rid) if r['request_id'] not in requests_before]
        if metadata['http_status'] is None and metadata['request_ids']:
            last=next((r for r in reversed(self.rows('fulltext_request_result',rid)) if r['request_id']==metadata['request_ids'][-1]),None)
            if last: metadata['http_status']=json.loads(last['result_json'])['http_status']
        guard(metadata,(transport.key,transport.email))
        row=dict(run_id=rid,evidence_id=eid,provider=provider,linkage=link,response_sha256=response_sha,metadata_json=canonical_json(metadata),metadata_sha256=digest(metadata),retrieved_at=utc_now())
        with self.write(rid): self.insert('fulltext_evidence',row)
        return row

    def discover(self,rid,transport=None):
        run,p,items=self._context(rid)
        if run['state']!='OPEN': self.verify(rid); return self.inspect(rid)
        self.verify(rid)
        transport=transport or SafeTransport(p)
        if 'UNPAYWALL' in p['provider_sources_enabled'] and not transport.email:
            raise ValidationError('UNPAYWALL_EMAIL runtime configuration required when enabled')
        for item in items:
            if any(r['corpus_item_id']==item['corpus_item_id'] for r in self.rows('fulltext_plan',rid)): continue
            routes=[]; evidence=[]
            if 'FROZEN' in p['provider_sources_enabled']:
                for record in (item.get('known_oa_locations') or {}).get('records',[]):
                    eid='stage11:'+item['item_sha256']+':'+record['provider_record_id']
                    routes.extend(location_routes(record['fields'],'FROZEN',record['provider_record_id'],eid))
            lookups=[]
            if 'OPENALEX' in p['provider_sources_enabled']:
                lookups.extend(('OPENALEX',w) for w in item['openalex_work_ids'])
            if 'UNPAYWALL' in p['provider_sources_enabled']:
                lookups.extend(('UNPAYWALL',d) for d in sorted({normalize_doi(v) for v in item['dois'] if normalize_doi(v)}))
            for provider,link in sorted(set(lookups)):
                row=self._lookup(rid,provider,link,transport); metadata=json.loads(row['metadata_json'])
                routes.extend(metadata['routes']); evidence.append(dict(evidence_id=row['evidence_id'],metadata_sha256=row['metadata_sha256'],response_sha256=row['response_sha256']))
            cs=make_candidates(item['corpus_item_id'],routes,p)
            plan=dict(corpus_item_id=item['corpus_item_id'],policy_sha256=run['policy_sha256'],candidates=cs,evidence=evidence)
            with self.write(rid):
                self.insert('fulltext_plan',dict(run_id=rid,corpus_item_id=item['corpus_item_id'],plan_json=canonical_json(plan),plan_sha256=digest(plan)))
                for c in cs: self.insert('fulltext_candidate',dict(run_id=rid,corpus_item_id=item['corpus_item_id'],candidate_id=c['candidate_id'],candidate_json=canonical_json(c),candidate_sha256=digest(c)))
        with self.write(rid): self.db.execute("UPDATE fulltext_run SET state='ACQUIRING' WHERE run_id=?",(rid,))
        self.verify(rid)
        return self.inspect(rid)

    def _attempts(self,rid,cid=None):
        results={r['attempt_id']:r for r in self.rows('fulltext_attempt_result',rid)}
        return [(a,results.get(a['attempt_id'])) for a in self.rows('fulltext_attempt',rid) if cid is None or a['candidate_id']==cid]

    def acquire(self,rid,transport=None,*,expected_policy_sha256=None,runtime_urls=None):
        run,p,items=self._context(rid)
        if expected_policy_sha256 and expected_policy_sha256!=run['policy_sha256']: raise ValidationError('Changed policy blocks resume')
        if run['state'] in ('MATERIALIZED','SEALED'): self.verify(rid); return self.inspect(rid)
        if run['state']!='ACQUIRING': raise ValidationError('Freeze candidate plans before acquisition')
        self.verify(rid); transport=transport or SafeTransport(p)
        for item in items:
            iid=item['corpus_item_id']; candidates=[json.loads(r['candidate_json']) for r in self.rows('fulltext_candidate',rid) if r['corpus_item_id']==iid]
            candidates=sorted([c for c in candidates if c['eligible']],key=lambda c:c['rank_components'])[:p['per_item_candidate_limit']]
            # All bounded eligible candidates may be retained; first ranked success is primary.
            for c in candidates:
                previous=self._attempts(rid,c['candidate_id'])
                if previous:
                    last=previous[-1][1]
                    if last and not json.loads(last['result_json']).get('retryable'): continue
                    if len(previous)>=p['retry_count']+1: continue
                with self.write(rid):
                    b=self.budget(rid); attempted_items={r['corpus_item_id'] for r in self.rows('fulltext_candidate',rid) if self._attempts(rid,r['candidate_id'])}
                    if (iid not in attempted_items and b['items']>=p['global_download_budget']['items']) or b['artifacts']>=p['global_download_budget']['artifacts']: continue
                    aid=new_id('fta'); self.insert('fulltext_attempt',dict(run_id=rid,candidate_id=c['candidate_id'],attempt_id=aid,ordinal=len(previous),started_at=utc_now()))
                requests_before={r['request_id'] for r in self.rows('fulltext_request',rid)}
                result=dict(status='FAILED',failure_class=None,retryable=False,artifact_sha256=None,http=None,request_ids=[])
                try:
                    url=(runtime_urls or {}).get(c['candidate_id'],c['url'])
                    if digest(normalize_url(url))!=c['url_identity_sha256']: raise FetchError('RUNTIME_URL_REQUIRED')
                    body,http=self._fetch(rid,transport,url,'PDF',p['max_pdf_bytes'],c['candidate_id'])
                    guard(body,(transport.key,transport.email)); metadata=validate_pdf(body,p)
                    stored=self.cas.put_bytes(body); metadata['storage_locator']=stored.relative_path
                    with self.write(rid):
                        if not any(a['artifact_sha256']==stored.artifact_sha256 for a in self.rows('fulltext_artifact',rid)):
                            self.insert('fulltext_artifact',dict(run_id=rid,artifact_sha256=stored.artifact_sha256,artifact_json=canonical_json(metadata),metadata_sha256=digest(metadata),first_acquired_at=utc_now()))
                    result.update(status='ACQUIRED',artifact_sha256=stored.artifact_sha256,http=http)
                except FetchError as exc: result.update(failure_class=exc.reason,retryable=exc.retryable)
                except ValidationError: result.update(failure_class='PDF_VALIDATION_FAILED')
                result['request_ids']=[r['request_id'] for r in self.rows('fulltext_request',rid) if r['request_id'] not in requests_before]
                if result['http'] is None and result['request_ids']:
                    last=next((r for r in reversed(self.rows('fulltext_request_result',rid)) if r['request_id']==result['request_ids'][-1]),None)
                    if last: result['http']=json.loads(last['result_json'])
                with self.write(rid): self.insert('fulltext_attempt_result',dict(run_id=rid,attempt_id=aid,result_json=canonical_json(result),result_sha256=digest(result),artifact_sha256=result['artifact_sha256'],finished_at=utc_now()))
        return self.inspect(rid)

    def _statuses(self,rid,items,p):
        result=[]; plans={r['corpus_item_id']:r for r in self.rows('fulltext_plan',rid)}
        for item in items:
            iid=item['corpus_item_id']; plan=plans[iid]; cs=json.loads(plan['plan_json'])['candidates']; attempts=[]; successful=[]
            for c in cs:
                for a,r in self._attempts(rid,c['candidate_id']):
                    outcome=json.loads(r['result_json']) if r else dict(status='INTERRUPTED',failure_class='INTERRUPTED',retryable=True,artifact_sha256=None,http=None)
                    attempts.append(dict(candidate_id=c['candidate_id'],ordinal=a['ordinal'],outcome=outcome))
                    if outcome['status']=='ACQUIRED': successful.append((c['rank_components'],c['candidate_id'],outcome['artifact_sha256']))
            successful.sort(); reasons=[]
            if not successful:
                reasons=sorted({a['outcome']['failure_class'] for a in attempts if a['outcome']['failure_class']})
                if not reasons:
                    if not cs: reasons=['NO_OA_LOCATION']
                    elif all(c['eligibility_reason']=='ONLY_SUBMITTED_VERSION' for c in cs): reasons=['ONLY_SUBMITTED_VERSION']
                    elif all(c['eligibility_reason']=='ONLY_UNKNOWN_VERSION' for c in cs): reasons=['ONLY_UNKNOWN_VERSION']
                    elif all(c['url_type']=='LANDING_PAGE' for c in cs): reasons=['NO_DIRECT_PDF']
                    else: reasons=['GLOBAL_BUDGET_EXHAUSTED' if any(c['eligible'] for c in cs) else 'NO_ACCEPTABLE_OA']
                if not item['dois']: reasons.append('NO_DOI')
                if not item['openalex_work_ids']: reasons.append('NO_OPENALEX_ID')
            selected=next((c for c in cs if successful and c['candidate_id']==successful[0][1]),None)
            body=dict(corpus_item_id=iid,publication_entity_id=item['publication_entity_id'],candidate_plan_sha256=plan['plan_sha256'],attempts=attempts,status='ACQUIRED_AUTO' if successful else 'MANUAL_REQUIRED',reasons=sorted(set(reasons)),selected_candidate_id=successful[0][1] if successful else None,selected_artifact_sha256=successful[0][2] if successful else None,selected_evidence=selected['routes'] if selected else [])
            result.append(dict(**body,status_sha256=digest(body)))
        return result

    def _manual(self,run,items,statuses):
        source={i['corpus_item_id']:i for i in items}; plans={r['corpus_item_id']:json.loads(r['plan_json']) for r in self.rows('fulltext_plan',run['run_id'])}; rows=[]
        for s in statuses:
            if s['status']=='ACQUIRED_AUTO': continue
            i=source[s['corpus_item_id']]; cs=plans[i['corpus_item_id']]['candidates']
            rows.append(dict(corpus_item_id=i['corpus_item_id'],publication_entity_id=i['publication_entity_id'],titles=i['titles'],dois=i['dois'],openalex_work_ids=i['openalex_work_ids'],landing_pages=sorted({c['url'] for c in cs if c['url_type']=='LANDING_PAGE'}),candidates=[dict(candidate_id=c['candidate_id'],url=c['url'],version=c['version'],eligible=c['eligible'],reason=c['eligibility_reason']) for c in cs],status=s['status'],reasons=s['reasons'],release_id=run['release_id'],release_manifest_sha256=run['release_manifest_sha256'],fulltext_policy_sha256=run['policy_sha256']))
        return sorted(rows,key=lambda r:r['corpus_item_id'])

    def _manifest(self,run,items,p,statuses):
        queue=self._manual(run,items,statuses)
        inventory={}
        for table in ('fulltext_input','fulltext_evidence','fulltext_plan','fulltext_candidate','fulltext_request','fulltext_request_result','fulltext_attempt','fulltext_attempt_result','fulltext_artifact'):
            inventory[table]=digest([{k:v for k,v in row.items() if k not in ('created_at','retrieved_at','started_at','finished_at','first_acquired_at')} for row in self.rows(table,run['run_id'])])
        provider_evidence=[{k:e[k] for k in ('evidence_id','provider','linkage','response_sha256','metadata_sha256')} for e in self.rows('fulltext_evidence',run['run_id'])]
        return dict(schema='fulltext-manifest/1',run_id=run['run_id'],release_id=run['release_id'],release_manifest_sha256=run['release_manifest_sha256'],fulltext_policy=p,fulltext_policy_sha256=run['policy_sha256'],handoff_sha256=run['handoff_sha256'],ordered_corpus_item_ids=[i['corpus_item_id'] for i in items],items=statuses,manual_queue=queue,manual_queue_sha256=digest(queue),provider_lookup_evidence=provider_evidence,evidence_inventory=inventory,budget=self.budget(run['run_id']),artifact_count=len(self.rows('fulltext_artifact',run['run_id'])),failure_count=sum(s['status']!='ACQUIRED_AUTO' for s in statuses),interpretation='Frozen-policy selection and provider-reported OA/version evidence; no universal authority or redistribution determination.')

    def materialize(self,rid):
        run,p,items=self._context(rid)
        if run['state'] in ('MATERIALIZED','SEALED'): self.verify(rid); return self.inspect(rid)
        if run['state']!='ACQUIRING': raise ValidationError('Candidate plans must be complete')
        self.verify(rid); statuses=self._statuses(rid,items,p); manifest=self._manifest(run,items,p,statuses)
        with self.write(rid):
            for s in statuses: self.insert('fulltext_status',dict(run_id=rid,corpus_item_id=s['corpus_item_id'],status_json=canonical_json(s),status_sha256=digest(s)))
            self.db.execute("UPDATE fulltext_run SET state='MATERIALIZED',manifest_json=?,manifest_sha256=? WHERE run_id=?",(canonical_json(manifest),digest(manifest),rid))
        return self.inspect(rid)

    def finalize(self,rid):
        if self.get(rid)['state']=='ACQUIRING': self.materialize(rid)
        if self.get(rid)['state']=='SEALED': self.verify(rid); return self.inspect(rid)
        if self.get(rid)['state']!='MATERIALIZED': raise ValidationError('Materialized run required')
        self.verify(rid)
        with self.write(rid): self.db.execute("UPDATE fulltext_run SET state='SEALED' WHERE run_id=?",(rid,))
        return self.inspect(rid)

    def inspect(self,rid):
        run=self.get(rid)
        return dict(run_id=rid,release_id=run['release_id'],state=run['state'],policy_sha256=run['policy_sha256'],manifest_sha256=run['manifest_sha256'],corpus_item_count=len(self.rows('fulltext_input',rid)),candidate_count=len(self.rows('fulltext_candidate',rid)),artifact_count=len(self.rows('fulltext_artifact',rid)),budget=self.budget(rid))

    def verify(self,rid):
        run,p,items=self._context(rid)
        for table,field,hashfield in [('fulltext_evidence','metadata_json','metadata_sha256'),('fulltext_plan','plan_json','plan_sha256'),('fulltext_candidate','candidate_json','candidate_sha256'),('fulltext_request','request_json','request_sha256'),('fulltext_request_result','result_json','result_sha256'),('fulltext_attempt_result','result_json','result_sha256'),('fulltext_artifact','artifact_json','metadata_sha256'),('fulltext_status','status_json','status_sha256')]:
            for row in self.rows(table,rid):
                if digest(json.loads(row[field]))!=row[hashfield]: raise ArtifactIntegrityError('Full-text row hash mismatch')
        evidences={e['evidence_id']:e for e in self.rows('fulltext_evidence',rid)}
        for e in evidences.values():
            m=json.loads(e['metadata_json'])
            allowed={v for item in items for v in (item['openalex_work_ids'] if e['provider']=='OPENALEX' else item['dois'])}
            if e['provider'] not in ('OPENALEX','UNPAYWALL') or e['provider'] not in p['provider_sources_enabled'] or e['linkage'] not in allowed:
                raise ArtifactIntegrityError('Lookup outside exact handoff')
            if e['evidence_id']!='fte_'+digest([e['provider'],e['linkage']])[:40]: raise ArtifactIntegrityError('Evidence identity mismatch')
            if e['response_sha256']:
                stored=self.cas.verify(e['response_sha256']); data=json.loads((self.db.workspace/stored.relative_path).read_bytes())
                if location_routes(data,e['provider'],e['linkage'],e['evidence_id'])!=m['routes'] or data.get('locations_count')!=m['locations_count']: raise ArtifactIntegrityError('Evidence projection mismatch')
        plans=self.rows('fulltext_plan',rid)
        if run['state']!='OPEN' and {r['corpus_item_id'] for r in plans}!={i['corpus_item_id'] for i in items}: raise ArtifactIntegrityError('Incomplete item plans')
        byitem={i['corpus_item_id']:i for i in items}; expected_candidates=[]
        for row in plans:
            item=byitem[row['corpus_item_id']]; plan=json.loads(row['plan_json']); routes=[]; refs=[]
            if 'FROZEN' in p['provider_sources_enabled']:
                for record in (item.get('known_oa_locations') or {}).get('records',[]): routes.extend(location_routes(record['fields'],'FROZEN',record['provider_record_id'],'stage11:'+item['item_sha256']+':'+record['provider_record_id']))
            for provider,links in [('OPENALEX',item['openalex_work_ids']),('UNPAYWALL',sorted({normalize_doi(v) for v in item['dois'] if normalize_doi(v)}))]:
                if provider not in p['provider_sources_enabled']: continue
                for link in sorted(set(links)):
                    eid='fte_'+digest([provider,link])[:40]
                    if eid not in evidences: raise ArtifactIntegrityError('Missing lookup evidence')
                    e=evidences[eid]; routes.extend(json.loads(e['metadata_json'])['routes']); refs.append(dict(evidence_id=eid,metadata_sha256=e['metadata_sha256'],response_sha256=e['response_sha256']))
            cs=make_candidates(item['corpus_item_id'],routes,p)
            expected=dict(corpus_item_id=item['corpus_item_id'],policy_sha256=run['policy_sha256'],candidates=cs,evidence=refs)
            if plan!=expected: raise ArtifactIntegrityError('Candidate plan mismatch')
            expected_candidates.extend(cs)
        actual=[json.loads(r['candidate_json']) for r in self.rows('fulltext_candidate',rid)]
        if sorted(actual,key=digest)!=sorted(expected_candidates,key=digest): raise ArtifactIntegrityError('Candidate table mismatch')
        cmap={c['candidate_id']:c for c in actual}
        requests={r['request_id']:json.loads(r['request_json']) for r in self.rows('fulltext_request',rid)}
        http_results={r['request_id']:json.loads(r['result_json']) for r in self.rows('fulltext_request_result',rid)}
        for qid,q in requests.items():
            if sanitize_url(q['url'])!=q['url'] or q['purpose'] not in ('PDF','METADATA'):
                raise ArtifactIntegrityError('Unsafe persisted request')
            if qid in http_results and not 0<=http_results[qid]['bytes']<=q['reserved_bytes']:
                raise ArtifactIntegrityError('Invalid request accounting')
        for e in evidences.values():
            m=json.loads(e['metadata_json'])
            for qid in m['request_ids']:
                if qid not in requests or requests[qid]['purpose']!='METADATA' or requests[qid]['lookup_identity']!=e['provider']+':'+e['linkage']:
                    raise ArtifactIntegrityError('Provider request linkage mismatch')
            if e['response_sha256'] and (not m['request_ids'] or m['http_status']!=200):
                raise ArtifactIntegrityError('Raw provider evidence requires HTTP success')
        seen_artifacts=set()
        for a,r in self._attempts(rid):
            c=cmap[a['candidate_id']]
            if not c['eligible'] or a['ordinal']>p['retry_count']: raise ArtifactIntegrityError('Ineligible/unbounded attempt')
            if r:
                outcome=json.loads(r['result_json'])
                if outcome['artifact_sha256']!=r['artifact_sha256'] or (outcome['status']=='ACQUIRED')!=(r['artifact_sha256'] is not None): raise ArtifactIntegrityError('Attempt artifact mismatch')
                for qid in outcome['request_ids']:
                    if qid not in requests or requests[qid]['lookup_identity']!=a['candidate_id'] or requests[qid]['purpose']!='PDF': raise ArtifactIntegrityError('Attempt request linkage mismatch')
                if outcome['status']=='ACQUIRED':
                    if not outcome['request_ids'] or outcome['http']['http_status']!=200 or not any(http_results.get(qid,{}).get('http_status')==200 for qid in outcome['request_ids']):
                        raise ArtifactIntegrityError('Successful artifact requires HTTP success provenance')
                    seen_artifacts.add(outcome['artifact_sha256'])
        for a in self.rows('fulltext_artifact',rid):
            stored=self.cas.verify(a['artifact_sha256']); body=(self.db.workspace/stored.relative_path).read_bytes(); expected=validate_pdf(body,p); expected['storage_locator']=stored.relative_path
            if expected!=json.loads(a['artifact_json']): raise ArtifactIntegrityError('Artifact metadata mismatch')
        if seen_artifacts!={a['artifact_sha256'] for a in self.rows('fulltext_artifact',rid)}:
            raise ArtifactIntegrityError('Artifact registration/attempt mismatch')
        b=self.budget(rid)
        if any(b[k]>p['global_download_budget'][k] for k in b): raise ArtifactIntegrityError('Budget exceeded')
        if run['state'] in ('MATERIALIZED','SEALED'):
            statuses=self._statuses(rid,items,p); actual=[json.loads(r['status_json']) for r in self.rows('fulltext_status',rid)]
            if actual!=statuses: raise ArtifactIntegrityError('Item statuses mismatch')
            manifest=self._manifest(run,items,p,statuses)
            if canonical_json(manifest)!=run['manifest_json'] or digest(manifest)!=run['manifest_sha256']: raise ArtifactIntegrityError('Manifest mismatch')
        return dict(verified=True,mode='DB_PROVENANCE_AND_ARTIFACT_BYTES_OFFLINE',**self.inspect(rid))

    def export(self,rid,directory):
        from .portable import render_files,write_exact,verify_export
        self.verify(rid); run=self.get(rid)
        if run['state']!='SEALED': raise ValidationError('Sealed export required')
        write_exact(Path(directory),render_files(json.loads(run['manifest_json'])))
        return verify_export(directory,expected_manifest_sha256=run['manifest_sha256'])

    def export_pdfs(self,rid,directory):
        from .portable import write_exact
        self.verify(rid); run=self.get(rid)
        if run['state']!='SEALED': raise ValidationError('Sealed artifact view required')
        files={}
        for status in json.loads(run['manifest_json'])['items']:
            if status['selected_artifact_sha256']:
                files[status['corpus_item_id']+'.pdf']=self.cas.path_for_hash(status['selected_artifact_sha256']).read_bytes()
        write_exact(Path(directory),files)
        return dict(files=len(files),verified=True)
