"""Explicit one-hop expansion; all requests use the accepted Stage07 acquisition API."""
import json
from collections import defaultdict
from ..core import ValidationError
from ..core.provenance import canonical_json
from ..openalex_acquisition.normalize import native_id
from ..providers.scimago import _transaction
from .store import CITATION_PROFILE

# Official OR maximum checked2026-10-29; guarded by documentation and boundary tests.
OPENALEX_OR_LIMIT=100
FIELDS=['id','title','doi','publication_year','publication_date','type','authorships','primary_location','referenced_works','is_retracted']


def _spec(filters,maximum,page_size,corpus):
    return dict(filters=filters,max_records=maximum,page_size=page_size,select=FIELDS,corpus=corpus)


def _local(store,rid,oid):
    revision=store.get(rid)['project_revision_id']
    row=store.db.execute("SELECT r.provider_record_id FROM provider_record r JOIN openalex_acquisition a ON a.snapshot_id=r.snapshot_id WHERE a.project_revision_id=? AND a.state='COMPLETE' AND r.provider_native_id=? ORDER BY r.provider_record_id LIMIT 1",(revision,oid)).fetchone()
    return row[0] if row else None


def _backward_plan(store,op,seeds,client,maximum,corpus):
    plan=[]; duplicate=0; available=0; omitted=0
    for seed in seeds:
        info=store._work(seed['provider_record_id']); scope=corpus or json.loads(info['acquisition']['request_spec_json'])['corpus']
        if 'referenced_works' not in info['raw']:
            child=store._child(op,_spec({'ids.openalex':seed['work_id'].rsplit('/',1)[-1]},1,1,scope),client,'SUPPLEMENTAL')
            records=store.foundation.list_provider_records(child['snapshot_id'])
            if len(records)!=1 or records[0]['provider_native_id']!=seed['work_id']:
                raise ValidationError('Supplemental seed identifier missing/changed; explicit Stage03 seed resolution required')
            info=store._work(records[0]['provider_record_id'])
        refs=info['raw'].get('referenced_works')
        if not isinstance(refs,list): raise ValidationError('Referenced works missing or malformed; not an empty neighborhood')
        client.private.check(refs); grouped={}
        for index,value in enumerate(refs):
            try: oid=native_id(value); valid=True
            except ValueError: oid=canonical_json(value); valid=False
            key=(not valid,oid)
            if key in grouped: duplicate+=1
            target=grouped.setdefault(key,dict(requested=oid,valid=valid,indices=[],original_values=[],local_record_id=None))
            target['indices'].append(index)
            if value not in target['original_values']: target['original_values'].append(value)
        chosen=[grouped[k] for k in sorted(grouped)[:maximum]]; available+=len(grouped); omitted+=max(0,len(grouped)-len(chosen))
        for target in chosen:
            if target['valid']: target['local_record_id']=seed['provider_record_id'] if target['requested']==seed['work_id'] else _local(store,op['discovery_run_id'],target['requested'])
        plan.append(dict(seed=seed,evidence_record_id=info['record']['provider_record_id'],corpus=scope,targets=chosen))
    return dict(seeds=plan,duplicate_reference_ids=duplicate,available_reference_ids=available,omitted_due_to_explicit_bound=omitted)


def _backward_events(store,op,plan,client,batch_size):
    missing=defaultdict(set); fetched={}
    for row in plan['seeds']:
        for target in row['targets']:
            if target['valid'] and target['local_record_id'] is None: missing[row['corpus']].add(target['requested'])
    for scope,targets in sorted(missing.items()):
        ordered=sorted(targets)
        for offset in range(0,len(ordered),batch_size):
            batch=ordered[offset:offset+batch_size]
            acquisition=store._child(op,_spec({'ids.openalex':'|'.join(x.rsplit('/',1)[-1] for x in batch)},len(batch),min(batch_size,OPENALEX_OR_LIMIT),scope),client,'TARGET')
            records=store.foundation.list_provider_records(acquisition['snapshot_id']); by_id={r['provider_native_id']:r['provider_record_id'] for r in records}; unknown=sorted(set(by_id)-set(batch))
            for requested in batch:
                returned=requested if requested in by_id else (next(iter(by_id)) if len(batch)==len(by_id)==1 else None)
                fetched[(scope,requested)]=dict(record_id=by_id.get(returned),acquisition_id=acquisition['acquisition_id'],requested_batch=batch,
                    returned_unassigned_ids=unknown,mapping_basis='EXACT_NATIVE_ID' if returned==requested else ('SINGLE_ID_FILTER_RESPONSE' if returned else 'NO_UNAMBIGUOUS_MAPPING'))
    events=[]
    for row in plan['seeds']:
        seed=row['seed']; evidence=store._work(row['evidence_record_id'])
        for target in row['targets']:
            proof=dict(seed_evidence_record_id=evidence['record']['provider_record_id'],seed_raw=evidence['locator'],reference_indices=target['indices'],original_reference_values=target['original_values'],basis='REFERENCED_WORKS_ARRAY')
            if not target['valid']:
                events.append(dict(seed=seed,target=None,requested=target['requested'],proof=proof,state='MALFORMED',edge=None,self_loop=False)); continue
            target_info=None; state='LOCAL'
            if target['local_record_id']: target_info=store._work(target['local_record_id'])
            else:
                result=fetched[(row['corpus'],target['requested'])]; proof.update(result)
                target_info=store._work(result['record_id']) if result['record_id'] else None
                state='ACQUIRED' if target_info else ('UNATTRIBUTED_ID_CHANGE' if result['returned_unassigned_ids'] else 'MISSING')
            self_loop=target['requested']==evidence['record']['provider_native_id']
            if self_loop: state='SELF_LOOP'
            edge=dict(provider_record_id=evidence['record']['provider_record_id'],source=evidence['record']['provider_native_id'],target=target['requested'],
                path=evidence['locator']['pointer']+'/referenced_works/'+str(target['indices'][0]))
            events.append(dict(seed=seed,target=target_info,requested=target['requested'],proof=proof,state=state,edge=edge,self_loop=self_loop))
    return events


def _forward_events(store,op,seeds,client,maximum,page_size,corpus):
    events=[]
    for seed in seeds:
        parent=store._work(seed['provider_record_id']); scope=corpus or json.loads(parent['acquisition']['request_spec_json'])['corpus']
        acquisition=store._child(op,_spec({'cites':seed['work_id'].rsplit('/',1)[-1]},maximum,page_size,scope),client,'FORWARD')
        for record in store.foundation.list_provider_records(acquisition['snapshot_id']):
            info=store._work(record['provider_record_id']); raw=info['raw']; proof=dict(request_spec_sha256=acquisition['request_spec_sha256'],acquisition_id=acquisition['acquisition_id'],citing_raw=info['locator'])
            if 'referenced_works' in raw:
                if not isinstance(raw['referenced_works'],list): raise ValidationError('Forward citation array malformed')
                indices=[]
                for index,value in enumerate(raw['referenced_works']):
                    try:
                        if native_id(value)==seed['work_id']: indices.append(index)
                    except ValueError: pass
                if not indices: raise ValidationError('Forward result contradicts explicit referenced_works evidence')
                proof.update(basis='REFERENCED_WORKS_ARRAY',reference_indices=indices); path=info['locator']['pointer']+'/referenced_works/'+str(indices[0])
            else:
                proof.update(basis='CITES_FILTER',requested_seed_id=seed['work_id']); path='acquisition:'+acquisition['acquisition_id']+'/request_spec/filters/cites'
            self_loop=record['provider_native_id']==seed['work_id']
            events.append(dict(seed=seed,target=info,requested=record['provider_native_id'],proof=proof,state='SELF_LOOP' if self_loop else 'ACQUIRED',self_loop=self_loop,
                edge=dict(provider_record_id=record['provider_record_id'],source=record['provider_native_id'],target=seed['work_id'],path=path)))
    return events


def expand(store,rid,seeds,client,kind,maximum,batch_size,corpus):
    store._open(rid)
    if not isinstance(seeds,list) or not 1<=len(seeds)<=100 or type(maximum) is not int or not 1<=maximum<=10000 or type(batch_size) is not int or not 1<=batch_size<=OPENALEX_OR_LIMIT:
        raise ValidationError('Explicit bounded seeds/results and supported batch/page size required')
    if corpus is not None and corpus not in ('core','all','expansion'): raise ValidationError('Unsupported explicit corpus')
    validated=[store._seed(rid,seed) for seed in seeds]; validated=sorted({canonical_json(s):s for s in validated}.values(),key=canonical_json)
    op=store._operation(rid,kind,dict(seeds=validated,max_results=maximum,batch_or_page_size=batch_size,corpus=corpus))
    if op['state']=='COMPLETE': return store._replay_operation(op)
    try:
        store._op_update(op,state='OPEN')
        if kind=='BACKWARD_CITATION':
            if op['plan_json'] is None:
                plan=_backward_plan(store,op,validated,client,maximum,corpus); client.private.check(plan)
                store._op_update(op,plan_json=canonical_json(plan))
            else: plan=json.loads(op['plan_json'])
            events=_backward_events(store,op,plan,client,batch_size)
            stats={k:v for k,v in plan.items() if k!='seeds'}
        else:
            events=_forward_events(store,op,validated,client,maximum,batch_size,corpus); stats={}
        client.private.check([e['proof'] for e in events])
        with _transaction(store.db),store._authorize(rid):
            suppressed=0
            for event in events:
                edge=event['edge']; observation=None
                if edge:
                    observation=store.observations.add_citation_edge_observation(edge['provider_record_id'],CITATION_PROFILE,source_identifier_namespace='openalex_work_id',source_identifier_value=edge['source'],
                        target_identifier_namespace='openalex_work_id',target_identifier_value=edge['target'],source_field_path=edge['path'])['observation_id']
                if not store._event(op,kind,event['target'],requested=event['requested'],seed=event['seed'],evidence=event['proof'],citation=observation,state=event['state'],self_loop=event['self_loop']): suppressed+=1
            store._attempt(op,suppressed); store._complete(op,stats)
    except BaseException:
        if store.foundation._one('discovery_operation','operation_id',op['operation_id'])['state']!='COMPLETE': store._op_update(op,state='FAILED')
        raise
    return store.inspect(rid)
