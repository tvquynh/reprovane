"""Explicit frozen search comparisons; never promotes variants to discovery."""
import json,math,re
from ..stage09 import Stage09Store,stable
from ..core import ValidationError,ArtifactIntegrityError
from ..core.ids import new_id,utc_now
from ..core.provenance import canonical_json
from ..discovery.store import digest,PROFILE
from ..openalex_acquisition import AcquisitionSpec
from ..openalex_acquisition.normalize import native_id
from ..resolution.normalization import normalize_doi
from ..providers.scimago import _transaction


def sentinel(value):
    if not isinstance(value,dict) or set(value)-{'namespace','value','source_note'} or not {'namespace','value'}<=set(value): raise ValidationError('Exact identifier sentinel required')
    namespace=value['namespace']; original=value['value']; note=value.get('source_note')
    if namespace not in ('openalex_work_id','doi') or not isinstance(original,str): raise ValidationError('Only exact Work ID/DOI sentinels supported')
    if note is not None and (not isinstance(note,str) or len(note)>500 or '@' in note or re.search(r'(?i)bearer|password|api.key|token=',note)): raise ValidationError('Sentinel source note must be non-sensitive')
    try: normalized=native_id(original) if namespace=='openalex_work_id' else normalize_doi(original)
    except ValueError: normalized=None
    if not normalized: raise ValidationError('Malformed sentinel identifier')
    return dict(sentinel_id='sent_'+digest([namespace,normalized])[:32],namespace=namespace,value=normalized,original_values=[original],source_notes=[note] if note else [])


class SearchValidationStore(Stage09Store):
    def get(self,vid): return self.one('search_validation_run','validation_run_id',vid)
    def variants(self,vid): return [dict(r) for r in self.db.execute('SELECT * FROM search_validation_variant WHERE validation_run_id=? ORDER BY label',(vid,))]
    def sentinels(self,vid): return [json.loads(r[0]) for r in self.db.execute('SELECT definition_json FROM search_validation_sentinel WHERE validation_run_id=? ORDER BY sentinel_id',(vid,))]
    def result(self,vid):
        row=self.db.execute('SELECT * FROM search_validation_result WHERE validation_run_id=?',(vid,)).fetchone(); return dict(row) if row else None
    def create(self,project_revision_id,variants,sentinels=(),*,thresholds=None):
        self.foundation.get_project_revision(project_revision_id)
        if not isinstance(variants,list) or not 1<=len(variants)<=100: raise ValidationError('Explicit bounded variant list required')
        definitions={}
        for raw in sentinels:
            item=sentinel(raw); old=definitions.get(item['sentinel_id'])
            if old:
                for k in ['original_values','source_notes']: item[k]=sorted(set(item[k]+old[k]))
            definitions[item['sentinel_id']]=item
        if thresholds is not None:
            if not isinstance(thresholds,dict) or not thresholds or set(thresholds)-{'minimum_known_item_recall','maximum_unresolved','required_sentinels'}: raise ValidationError('Explicit supported thresholds required')
            thresholds=dict(thresholds)
            if 'minimum_known_item_recall' in thresholds:
                n=thresholds['minimum_known_item_recall']
                if type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<=1: raise ValidationError('Invalid known-item threshold')
            if 'maximum_unresolved' in thresholds and (type(thresholds['maximum_unresolved']) is not int or thresholds['maximum_unresolved']<0): raise ValidationError('Invalid unresolved threshold')
            if 'required_sentinels' in thresholds:
                if not isinstance(thresholds['required_sentinels'],list): raise ValidationError('Required sentinels must be explicit identifiers')
                keys=sorted({sentinel(v)['sentinel_id'] for v in thresholds['required_sentinels']})
                if not set(keys)<=set(definitions): raise ValidationError('Required sentinel absent from plan')
                thresholds['required_sentinels']=keys
        vid=new_id('svr'); frozen=[]; labels=set()
        with _transaction(self.db),self.authorized('V',vid):
            for value in variants:
                if not isinstance(value,dict) or set(value)-{'label','source','acquisition_id','spec'} or ('spec' in value)==('acquisition_id' in value): raise ValidationError('Variant needs label and exactly one explicit spec/acquisition')
                label=value.get('label'); source=value.get('source','SEALED_ACQUISITION' if 'acquisition_id' in value else 'USER')
                if not isinstance(label,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',label) or label in labels or source not in ('USER','PROTOCOL','SEALED_ACQUISITION'): raise ValidationError('Distinct safe labels and explicit source required')
                labels.add(label)
                if 'spec' in value:
                    spec=AcquisitionSpec.from_value(value['spec'])
                    if re.search(r'(?i)search\.semantic|rerank\s*[:=]',spec.canonical): raise ValidationError('Stage09 does not initiate semantic search/reranking')
                    a=self.acquisitions.create(project_revision_id,spec)
                else:
                    a=self.acquisitions.get(value['acquisition_id'])
                    if a['state']!='COMPLETE': raise ValidationError('Reused variant acquisition must be complete')
                if a['project_revision_id']!=project_revision_id: raise ValidationError('Validation variant project revision mismatch')
                frozen.append(dict(label=label,source=source,acquisition_id=a['acquisition_id'],request_spec_sha256=a['request_spec_sha256'],request_spec=json.loads(a['request_spec_json'])))
            plan=dict(variants=sorted(frozen,key=lambda v:v['label']),sentinels=sorted(definitions.values(),key=lambda v:v['sentinel_id']),thresholds=thresholds)
            run=self.insert('search_validation_run',dict(validation_run_id=vid,project_revision_id=project_revision_id,plan_json=canonical_json(plan),plan_sha256=digest(plan),state='OPEN',finalization_sha256=None,created_at_utc=utc_now(),sealed_at_utc=None))
            for item in frozen: self.insert('search_validation_variant',dict(validation_run_id=vid,label=item['label'],acquisition_id=item['acquisition_id'],request_spec_sha256=item['request_spec_sha256']))
            for item in plan['sentinels']: self.insert('search_validation_sentinel',dict(validation_run_id=vid,sentinel_id=item['sentinel_id'],definition_json=canonical_json(item)))
        return run
    def _sets(self,vid):
        data={}
        for variant in self.variants(vid):
            a=self.acquisitions.get(variant['acquisition_id'])
            if a['state']!='COMPLETE': raise ValidationError('Validation acquisition unfinished')
            self.foundation.verify_provider_snapshot(a['snapshot_id']); records=self.foundation.list_provider_records(a['snapshot_id']); data[variant['label']]=dict(acquisition=a,records=records,ids={r['provider_native_id'] for r in records})
        return data
    def _metrics(self,vid,resolution_id):
        run=self.get(vid); plan=json.loads(run['plan_json']); data=self._sets(vid); safe,_=self.discovery._safe_map(resolution_id); out=[]; sentinels=self.sentinels(vid); mappings={}
        for label,d in data.items():
            spec=json.loads(d['acquisition']['request_spec_json']); match=re.fullmatch(r'(?:ids\.openalex|openalex):(W\d+|https://openalex.org/W\d+)',spec['filters'])
            if match and len(d['ids'])==1:
                requested=native_id(match[1]); returned=next(iter(d['ids']))
                if requested!=returned: mappings.setdefault(requested,[]).append(dict(returned_work_id=returned,acquisition_id=d['acquisition']['acquisition_id'],mapping_basis='SINGLE_ID_FILTER_RESPONSE_NOT_IDENTITY_EQUIVALENCE'))
        for label,d in data.items():
            a=d['acquisition']; items=[]
            for item in sentinels:
                hits=[]; mapping=mappings.get(item['value'],[]) if item['namespace']=='openalex_work_id' else []
                for record in d['records']:
                    if item['namespace']=='openalex_work_id': hit=record['provider_native_id']==item['value']
                    else:
                        claims=[r[0] for r in self.db.execute('SELECT identifier_value_normalized FROM publication_identifier_observation WHERE provider_record_id=? AND identifier_namespace=? AND normalizer_name=? AND normalizer_version=? AND observation_schema_version=?',(record['provider_record_id'],'doi',PROFILE['normalizer_name'],PROFILE['normalizer_version'],PROFILE['observation_schema_version']))]
                        hit=any(normalize_doi(v)==item['value'] for v in claims)
                    if hit: hits.append(record['provider_record_id'])
                state='RETRIEVED' if any(r in safe for r in hits) else 'UNRESOLVED' if hits or mapping else 'NOT_OBSERVED_IN_ACQUISITION'
                items.append(dict(sentinel_id=item['sentinel_id'],namespace=item['namespace'],requested_identifier=item['value'],state=state,matching_provider_record_ids=sorted(hits),observed_id_mapping=mapping))
            retrieved=sum(x['state']=='RETRIEVED' for x in items); unresolved=sum(x['state']=='UNRESOLVED' for x in items); missing=len(items)-retrieved-unresolved; denominator=retrieved+missing; known=retrieved/denominator if denominator else None
            thresholds=plan['thresholds']; status='INFORMATIONAL_ONLY'
            if thresholds:
                passed=('minimum_known_item_recall' not in thresholds or known is not None and known>=thresholds['minimum_known_item_recall']) and ('maximum_unresolved' not in thresholds or unresolved<=thresholds['maximum_unresolved']) and all(any(i['sentinel_id']==sid and i['state']=='RETRIEVED' for i in items) for sid in thresholds.get('required_sentinels',[]))
                status='VALIDATION_THRESHOLD_MET' if passed else 'VALIDATION_THRESHOLD_NOT_MET'
            spec=json.loads(a['request_spec_json']); out.append(dict(label=label,acquisition_id=a['acquisition_id'],request_spec_sha256=a['request_spec_sha256'],request_spec=spec,snapshot_id=a['snapshot_id'],termination=a['termination'],query_exhausted=a['termination']=='NATURAL',bounded=a['termination']!='NATURAL',completeness='QUERY_EXHAUSTED' if a['termination']=='NATURAL' else 'BOUNDED_OBSERVATION',persisted_record_count=len(d['records']),stable_work_ids=sorted(d['ids']),stable_work_count=len(d['ids']),exclusive_count=len(d['ids']-set().union(*(v['ids'] for k,v in data.items() if k!=label))),sentinels=items,known_item_retrieved=retrieved,known_item_missing=missing,known_item_unresolved=unresolved,resolvable_expected=denominator,total_expected=len(items),known_item_recall=known,threshold_status=status))
        pairs=[]
        for index,left in enumerate(out):
            for right in out[index+1:]:
                a=set(left['stable_work_ids']); b=set(right['stable_work_ids']); union=a|b; changed=sorted(k for k in left['request_spec'] if left['request_spec'][k]!=right['request_spec'][k])
                pairs.append(dict(left=left['label'],right=right['label'],intersection=len(a&b),union=len(union),jaccard=len(a&b)/len(union) if union else 1.0,left_exclusive=len(a-b),right_exclusive=len(b-a),both_query_exhausted=left['query_exhausted'] and right['query_exhausted'],bounded_comparison=left['bounded'] or right['bounded'],changed_request_fields=changed,query_terms_differ=left['request_spec']['search']!=right['request_spec']['search'],interpretation='Observed persisted sets; ordering, limits, corpus and filters remain explicit.'))
        return dict(variants=out,pairwise=pairs,sentinel_count=len(sentinels),interpretation='Known-item recall is not recall of all relevant literature. Missing means not observed in this acquisition; unresolved items are explicitly reported outside the resolvable denominator. No query is selected or promoted.')
    def execute(self,vid,client=None,*,plan_sha256=None):
        run=self.get(vid)
        if plan_sha256 is not None and plan_sha256!=run['plan_sha256']: raise ValidationError('Validation resume plan mismatch')
        if run['state'] in ('COMPLETE','SEALED'): self.verify(vid); return self.inspect(vid)
        try:
            for variant in self.variants(vid):
                a=self.acquisitions.get(variant['acquisition_id'])
                if a['state']!='COMPLETE':
                    if client is None: raise ValidationError('Runtime client required for explicit acquisition')
                    self.acquisitions.resume(a['acquisition_id'],AcquisitionSpec(a['request_spec_json']),client)
            data=self._sets(vid); records=sorted({r['provider_record_id'] for d in data.values() for r in d['records']})
            with _transaction(self.db),self.authorized('V',vid):
                resolution_id=self.discovery._resolve(run['project_revision_id'],records); metrics=self._metrics(vid,resolution_id)
                if client is not None: client.private.check(metrics)
                self.insert('search_validation_result',dict(validation_run_id=vid,resolution_run_id=resolution_id,metrics_json=canonical_json(metrics),metrics_sha256=digest(metrics),created_at_utc=utc_now()))
                self.update('search_validation_run','validation_run_id',vid,state='COMPLETE')
        except BaseException:
            with _transaction(self.db),self.authorized('V',vid): self.update('search_validation_run','validation_run_id',vid,state='FAILED')
            raise
        return self.inspect(vid)
    def _manifest(self,vid):
        run={k:v for k,v in stable(self.get(vid)).items() if k not in ('state','finalization_sha256')}; result=stable(self.result(vid)); resolution=result['resolution_run_id']
        return digest(dict(run=run,variants=self.variants(vid),sentinels=self.sentinels(vid),result=result,resolution_manifest=self.discovery.resolver.get_resolution_run(resolution)['materialization_manifest_sha256'] if resolution else None))
    def verify(self,vid):
        run=self.get(vid); plan=json.loads(run['plan_json']); result=self.result(vid)
        if digest(plan)!=run['plan_sha256'] or result is None: raise ArtifactIntegrityError('Validation plan/result mismatch')
        expected=[dict(validation_run_id=vid,label=v['label'],acquisition_id=v['acquisition_id'],request_spec_sha256=v['request_spec_sha256']) for v in plan['variants']]
        if self.variants(vid)!=expected or self.sentinels(vid)!=plan['sentinels']: raise ArtifactIntegrityError('Validation definitions mismatch')
        for v in plan['variants']:
            a=self.acquisitions.get(v['acquisition_id'])
            if a['project_revision_id']!=run['project_revision_id'] or a['request_spec_sha256']!=v['request_spec_sha256'] or json.loads(a['request_spec_json'])!=v['request_spec']: raise ArtifactIntegrityError('Variant acquisition context mismatch')
        if result['resolution_run_id']: self.discovery.resolver.verify_resolution_run(result['resolution_run_id'])
        metrics=self._metrics(vid,result['resolution_run_id'])
        if canonical_json(metrics)!=result['metrics_json'] or digest(metrics)!=result['metrics_sha256']: raise ArtifactIntegrityError('Validation metrics mismatch')
        if run['state']=='SEALED' and self._manifest(vid)!=run['finalization_sha256']: raise ArtifactIntegrityError('Validation final manifest mismatch')
        return True
    def finalize(self,vid):
        if self.get(vid)['state']=='SEALED': self.verify(vid); return self.inspect(vid)
        if self.get(vid)['state']!='COMPLETE': raise ValidationError('Execute validation successfully before finalizing')
        with _transaction(self.db),self.authorized('V',vid): self.verify(vid); self.update('search_validation_run','validation_run_id',vid,state='SEALED',finalization_sha256=self._manifest(vid),sealed_at_utc=utc_now())
        return self.inspect(vid)
    def inspect(self,vid):
        run=self.get(vid); result=self.result(vid)
        return dict(run=run,variants=self.variants(vid),sentinels=self.sentinels(vid),result=json.loads(result['metrics_json']) if result else None)
