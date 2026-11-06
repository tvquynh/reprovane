"""Deterministic bounded control of accepted Stage08 one-hop operations."""
import json
from collections import defaultdict
from ..stage09 import Stage09Store,stable
from ..core import ValidationError,ArtifactIntegrityError
from ..core.ids import new_id,utc_now
from ..core.provenance import canonical_json
from ..discovery.store import digest
from ..providers.scimago import _transaction
from .policy import FrontierPolicy,DIRECTIONS


class FrontierStore(Stage09Store):
    def get(self,fid): return self.one('frontier_run','frontier_run_id',fid)
    def rounds(self,fid): return [dict(r) for r in self.db.execute('SELECT * FROM frontier_round WHERE frontier_run_id=? ORDER BY round_number',(fid,))]
    def actions(self,fid): return [dict(r) for r in self.db.execute('SELECT * FROM frontier_action WHERE frontier_run_id=? ORDER BY round_number,action_index',(fid,))]
    def _bindings(self,rid):
        return sorted({r[0] for r in self.db.execute("SELECT b.acquisition_id FROM discovery_operation_acquisition b JOIN discovery_operation o ON o.operation_id=b.operation_id WHERE o.discovery_run_id=? AND b.role!='PRIMARY_SOURCE'",(rid,))})
    def _evidence(self,rid):
        events=self.discovery.events(rid); ids={e['returned_work_id'] for e in events if e['returned_work_id']}; aids=self._bindings(rid)
        for aid in aids: ids.update(self.acquisitions._replay(self.acquisitions.get(aid)))
        return dict(work_ids=sorted(ids),child_ids=aids,event_ids=sorted(e['event_id'] for e in events),missing_ids=sorted({e['requested_target_id'] for e in events if not e['provider_record_id'] and e['requested_target_id']}))
    def create(self,project_revision_id,discovery_run_id,policy):
        self.foundation.get_project_revision(project_revision_id); d=self.discovery._open(discovery_run_id)
        if d['project_revision_id']!=project_revision_id: raise ValidationError('Frontier/discovery project revision mismatch')
        policy=FrontierPolicy.from_value(policy); fid=new_id('ftr'); initial=self._evidence(discovery_run_id); initial['operation_ids']=sorted(o['operation_id'] for o in self.discovery.operations(discovery_run_id))
        if any(o['state']!='COMPLETE' for o in self.discovery.operations(discovery_run_id)): raise ValidationError('Finish existing discovery operations before binding frontier')
        with _transaction(self.db),self.authorized('F',fid):
            return self.insert('frontier_run',dict(frontier_run_id=fid,project_revision_id=project_revision_id,discovery_run_id=discovery_run_id,policy_json=policy.canonical,policy_sha256=policy.sha256,initial_json=canonical_json(initial),state='OPEN',stop_reason=None,finalization_sha256=None,created_at_utc=utc_now(),sealed_at_utc=None))
    def _context(self,fid,policy=None,discovery_run_id=None):
        run=self.get(fid)
        if digest(json.loads(run['policy_json']))!=run['policy_sha256']: raise ArtifactIntegrityError('Frontier policy hash mismatch')
        if policy is not None and FrontierPolicy.from_value(policy).sha256!=run['policy_sha256']: raise ValidationError('Resume policy mismatch')
        if discovery_run_id is not None and discovery_run_id!=run['discovery_run_id']: raise ValidationError('Resume discovery mismatch')
        return run
    def _owned(self,run):
        initial=json.loads(run['initial_json']); allowed=set(initial['operation_ids'])
        for row in self.rounds(run['frontier_run_id']):
            p=json.loads(row['plan_json']); allowed.update(p['checkpoint_operation_ids']); allowed.update(a['operation_id'] for a in p['actions'])
            if row['result_json']: allowed.update(json.loads(row['result_json'])['checkpoint_operation_ids'])
        if set(o['operation_id'] for o in self.discovery.operations(run['discovery_run_id']))!=allowed: raise ValidationError('Discovery changed outside frozen frontier context')
    def _usage(self,run,evidence=None):
        initial=json.loads(run['initial_json']); current=evidence or self._evidence(run['discovery_run_id'])
        return dict(new_work_ids=sorted(set(current['work_ids'])-set(initial['work_ids'])),child_acquisition_ids=sorted(set(current['child_ids'])-set(initial['child_ids'])),selected_seeds=sum(json.loads(r['result_json'])['selected_seed_count'] for r in self.rounds(run['frontier_run_id']) if r['state']=='COMPLETE'))
    def _checkpoint(self,rid):
        if not self.discovery._records(rid): return dict(resolution_run_id=None,provider_records=[])
        return self.discovery.resolve_seeds(rid)
    def _checkpoint_ops(self,rid): return sorted(o['operation_id'] for o in self.discovery.operations(rid) if o['operation_kind']=='RESOLUTION_CHECKPOINT')
    def _candidates(self,run,checkpoint):
        events=self.discovery.events(run['discovery_run_id']); by_record=defaultdict(list)
        for e in events:
            if e['provider_record_id'] and not e['self_loop']: by_record[e['provider_record_id']].append(e)
        groups=defaultdict(list); unresolved=[]
        for r in checkpoint['provider_records']:
            if r['provider_record_id'] not in by_record: continue
            if r['state']=='RESOLVED': groups[r['publication_entity_id']].append(r['provider_record_id'])
            else: unresolved.append(dict(provider_record_id=r['provider_record_id'],reason='NO_RESOLVED_OPENALEX_SEED'))
        history=defaultdict(set)
        for row in self.actions(run['frontier_run_id']):
            receipt=json.loads(row['receipt_json']); history[receipt['direction']].update(receipt['component_work_ids'])
        candidates=[]; policy=json.loads(run['policy_json'])
        for entity,records in groups.items():
            records=sorted(records,key=lambda r:(self.discovery._work(r)['record']['provider_native_id'],r)); chosen=records[0]
            work_ids=sorted({self.discovery._work(r)['record']['provider_native_id'] for r in records}); route_events=[e for r in records for e in by_record[r]]
            depth=min(e['depth'] for e in route_events); event=min(e['event_id'] for e in route_events)
            reasons={direction:('DIRECTION_DISABLED' if direction not in policy['directions'] else 'MAX_DEPTH_REACHED' if depth>=policy['max_depth'] else 'ALREADY_EXPANDED' if set(work_ids)&history[direction] else 'AVAILABLE') for direction in DIRECTIONS}
            candidates.append(dict(seed=dict(resolution_run_id=checkpoint['resolution_run_id'],publication_entity_id=entity,provider_record_id=chosen,depth=depth),work_id=work_ids[0],component_work_ids=work_ids,ordering_basis=[depth,event,work_ids[0],chosen],directions=reasons))
        return sorted(candidates,key=lambda c:c['ordering_basis']),sorted(unresolved,key=lambda c:c['provider_record_id'])
    def _stop(self,run,reason): self.update('frontier_run','frontier_run_id',run['frontier_run_id'],state='STOPPED',stop_reason=reason)
    def plan_next(self,fid,*,policy=None,discovery_run_id=None):
        run=self._context(fid,policy,discovery_run_id)
        if run['state'] in ('STOPPED','SEALED'): return self.inspect(fid)
        self.discovery._open(run['discovery_run_id']); self._owned(run); rounds=self.rounds(fid)
        if rounds and rounds[-1]['state']!='COMPLETE': return self.inspect(fid)
        p=json.loads(run['policy_json']); usage=self._usage(run)
        with _transaction(self.db),self.authorized('F',fid):
            if len(rounds)>=p['max_rounds']: self._stop(run,'MAX_ROUNDS'); return self.inspect(fid)
            checkpoint=self._checkpoint(run['discovery_run_id']); candidates,unresolved=self._candidates(run,checkpoint); before=self._evidence(run['discovery_run_id'])
            remaining_work=p['global_new_work_budget']-len(usage['new_work_ids']); remaining_child=p['global_child_acquisition_budget']-len(usage['child_acquisition_ids']); actions=[]; selected=0; blocked=None
            for c in candidates:
                available=[x for x in DIRECTIONS if c['directions'][x]=='AVAILABLE']
                if not available: continue
                if selected>=p['max_seeds_per_round']:
                    for direction in available: c['directions'][direction]='ROUND_SEED_LIMIT'
                    continue
                chosen=False
                for direction in available:
                    info=self.discovery._work(c['seed']['provider_record_id']); supplemental=int(direction=='BACKWARD_CITATION' and 'referenced_works' not in info['raw'])
                    child_capacity=(remaining_child-supplemental)*p['batch_or_page_size'] if direction=='BACKWARD_CITATION' else (p['max_results_per_seed'] if remaining_child>=1 else 0)
                    effective=min(p['max_results_per_seed'],remaining_work-supplemental,child_capacity)
                    if effective<1:
                        reason='GLOBAL_NEW_WORK_BUDGET' if remaining_work-supplemental<1 else 'GLOBAL_CHILD_ACQUISITION_BUDGET'; c['directions'][direction]='GLOBAL_BUDGET_LIMIT'; blocked=blocked or reason; continue
                    children=supplemental+(effective+p['batch_or_page_size']-1)//p['batch_or_page_size'] if direction=='BACKWARD_CITATION' else 1
                    validated=self.discovery._seed(run['discovery_run_id'],c['seed']); spec=dict(seeds=[validated],max_results=effective,batch_or_page_size=p['batch_or_page_size'],corpus=p['corpus'])
                    op=self.discovery._operation(run['discovery_run_id'],direction,spec)
                    actions.append(dict(action_index=len(actions),seed=c['seed'],component_work_ids=c['component_work_ids'],direction=direction,requested_limit=p['max_results_per_seed'],effective_limit=effective,work_reservation=effective+supplemental,child_reservation=children,batch_or_page_size=p['batch_or_page_size'],corpus=p['corpus'],expected_operation_spec=dict(kind=direction,**spec),operation_id=op['operation_id']))
                    remaining_work-=effective+supplemental; remaining_child-=children; c['directions'][direction]='SELECTED'; chosen=True
                selected+=int(chosen)
            terminal=None
            if not actions: terminal=blocked or ('MAX_DEPTH' if any('MAX_DEPTH_REACHED' in c['directions'].values() for c in candidates) else 'NO_ELIGIBLE_FRONTIER')
            plan=dict(policy_sha256=run['policy_sha256'],discovery_run_id=run['discovery_run_id'],resolution_checkpoint_id=checkpoint['resolution_run_id'],checkpoint_operation_ids=self._checkpoint_ops(run['discovery_run_id']),before_safe_provider_record_ids=sorted(r['provider_record_id'] for r in checkpoint['provider_records'] if r['state']=='RESOLVED'),candidate_count=len(candidates),candidates=candidates,unresolved=unresolved,selected_seed_count=selected,actions=actions,before=before,remaining_before=dict(work=p['global_new_work_budget']-len(usage['new_work_ids']),children=p['global_child_acquisition_budget']-len(usage['child_acquisition_ids'])),remaining_reserved=dict(work=remaining_work,children=remaining_child),terminal_reason=terminal)
            self.insert('frontier_round',dict(frontier_run_id=fid,round_number=len(rounds)+1,plan_json=canonical_json(plan),plan_sha256=digest(plan),state='PLANNED',result_json=None,created_at_utc=utc_now(),completed_at_utc=None))
        return self.inspect(fid)
    def _round_update(self,fid,number,**fields): self.db.execute('UPDATE frontier_round SET '+','.join(k+'=?' for k in fields)+' WHERE frontier_run_id=? AND round_number=?',(*fields.values(),fid,number))
    def run_next(self,fid,client=None,*,policy=None,discovery_run_id=None):
        run=self._context(fid,policy,discovery_run_id)
        if run['state'] in ('STOPPED','SEALED'): return self.inspect(fid)
        self.plan_next(fid); run=self.get(fid)
        if run['state']=='STOPPED': return self.inspect(fid)
        row=self.rounds(fid)[-1]; plan=json.loads(row['plan_json']); number=row['round_number']; p=json.loads(run['policy_json']); self._owned(run)
        try:
            for action in plan['actions']:
                existing=self.db.execute('SELECT 1 FROM frontier_action WHERE frontier_run_id=? AND round_number=? AND action_index=?',(fid,number,action['action_index'])).fetchone()
                if existing: continue
                op=self.one('discovery_operation','operation_id',action['operation_id'])
                if op['specification_json']!=canonical_json(action['expected_operation_spec']): raise ArtifactIntegrityError('Frozen Stage08 operation mismatch')
                if op['state']!='COMPLETE':
                    if client is None: raise ValidationError('Runtime client required for unfinished expansion')
                    if action['direction']=='BACKWARD_CITATION': self.discovery.expand_backward(run['discovery_run_id'],[action['seed']],client,max_results=action['effective_limit'],batch_size=action['batch_or_page_size'],corpus=action['corpus'])
                    else: self.discovery.expand_forward(run['discovery_run_id'],[action['seed']],client,max_results=action['effective_limit'],page_size=action['batch_or_page_size'],corpus=action['corpus'])
                bindings=[r[0] for r in self.db.execute('SELECT acquisition_id FROM discovery_operation_acquisition WHERE operation_id=? ORDER BY acquisition_id',(action['operation_id'],))]
                receipt=dict(direction=action['direction'],seed=action['seed'],component_work_ids=action['component_work_ids'],operation_id=action['operation_id'],child_acquisition_ids=bindings)
                with _transaction(self.db),self.authorized('F',fid): self.insert('frontier_action',dict(frontier_run_id=fid,round_number=number,action_index=action['action_index'],operation_id=action['operation_id'],receipt_json=canonical_json(receipt),created_at_utc=utc_now()))
            with _transaction(self.db),self.authorized('F',fid):
                after=self._evidence(run['discovery_run_id']); usage=self._usage(run,after)
                usage['selected_seeds']+=plan['selected_seed_count']
                if len(usage['new_work_ids'])>p['global_new_work_budget'] or len(usage['child_acquisition_ids'])>p['global_child_acquisition_budget']: raise ArtifactIntegrityError('Global frontier bound violated')
                checkpoint=self._checkpoint(run['discovery_run_id']); new=sorted(set(after['work_ids'])-set(plan['before']['work_ids']))
                previous=[json.loads(r['result_json']) for r in self.rounds(fid) if r['state']=='COMPLETE']; saturation=p['saturation']; streak=0
                if saturation and len(new)<=saturation['minimum_new_items']: streak=(previous[-1]['saturation_streak'] if previous else 0)+1
                reason=plan['terminal_reason'] or ('GLOBAL_NEW_WORK_BUDGET' if len(usage['new_work_ids'])>=p['global_new_work_budget'] else 'GLOBAL_CHILD_ACQUISITION_BUDGET' if len(usage['child_acquisition_ids'])>=p['global_child_acquisition_budget'] else 'MAX_ROUNDS' if number>=p['max_rounds'] else 'STRUCTURAL_SATURATION' if saturation and streak>=saturation['consecutive_rounds'] else None)
                result=dict(selected_seed_count=plan['selected_seed_count'],actions_by_direction={x:sum(a['direction']==x for a in plan['actions']) for x in DIRECTIONS},after=after,new_work_ids=new,new_discovery_events=sorted(set(after['event_ids'])-set(plan['before']['event_ids'])),new_missing_ids=sorted(set(after['missing_ids'])-set(plan['before']['missing_ids'])),safe_resolved_provider_records=sum(r['state']=='RESOLVED' for r in checkpoint['provider_records']),new_safe_resolved_provider_record_ids=sorted({r['provider_record_id'] for r in checkpoint['provider_records'] if r['state']=='RESOLVED'}-set(plan['before_safe_provider_record_ids'])),unresolved_provider_records=sum(r['state']!='RESOLVED' for r in checkpoint['provider_records']),resolution_checkpoint_id=checkpoint['resolution_run_id'],checkpoint_operation_ids=self._checkpoint_ops(run['discovery_run_id']),cumulative=usage,saturation_streak=streak,stop_reason=reason)
                self._round_update(fid,number,state='COMPLETE',result_json=canonical_json(result),completed_at_utc=utc_now()); self.update('frontier_run','frontier_run_id',fid,state='OPEN',stop_reason=None)
                if reason: self._stop(run,reason)
        except BaseException:
            with _transaction(self.db),self.authorized('F',fid): self._round_update(fid,number,state='FAILED'); self.update('frontier_run','frontier_run_id',fid,state='FAILED',stop_reason='FAILED')
            raise
        return self.inspect(fid)
    def run_to_policy_stop(self,fid,client=None,**context):
        self._context(fid,**context)
        while self.get(fid)['state'] not in ('STOPPED','SEALED'): self.run_next(fid,client,**context)
        return self.inspect(fid)
    def _manifest(self,fid):
        run=stable(self.get(fid)); run={k:v for k,v in run.items() if k not in ('state','finalization_sha256')}; d=self.discovery.get(run['discovery_run_id'])
        checkpoints=set()
        for row in self.rounds(fid):
            checkpoints.add(json.loads(row['plan_json'])['resolution_checkpoint_id'])
            if row['result_json']: checkpoints.add(json.loads(row['result_json'])['resolution_checkpoint_id'])
        manifests={key:self.discovery.resolver.get_resolution_run(key)['materialization_manifest_sha256'] for key in sorted(k for k in checkpoints if k)}
        return digest(dict(run=run,rounds=[stable(r) for r in self.rounds(fid)],actions=[stable(a) for a in self.actions(fid)],discovery_manifest=d['materialization_sha256'],checkpoint_manifests=manifests))
    def verify(self,fid):
        run=self._context(fid); self._owned(run); p=json.loads(run['policy_json'])
        for r in self.rounds(fid):
            if r['state']!='COMPLETE' or digest(json.loads(r['plan_json']))!=r['plan_sha256']: raise ArtifactIntegrityError('Incomplete/tampered frontier round')
            plan=json.loads(r['plan_json']); receipts=[a for a in self.actions(fid) if a['round_number']==r['round_number']]
            for key in [plan['resolution_checkpoint_id'],json.loads(r['result_json'])['resolution_checkpoint_id']]:
                if key: self.discovery.resolver.verify_resolution_run(key)
            if len(receipts)!=len(plan['actions']): raise ArtifactIntegrityError('Frontier action count mismatch')
            for a,receipt in zip(plan['actions'],receipts):
                op=self.one('discovery_operation','operation_id',receipt['operation_id'])
                if op['operation_id']!=a['operation_id'] or op['state']!='COMPLETE' or op['specification_json']!=canonical_json(a['expected_operation_spec']): raise ArtifactIntegrityError('Frontier operation provenance mismatch')
        usage=self._usage(run)
        if len(usage['new_work_ids'])>p['global_new_work_budget'] or len(usage['child_acquisition_ids'])>p['global_child_acquisition_budget']: raise ArtifactIntegrityError('Frontier budget mismatch')
        if run['state']=='SEALED':
            self.discovery.verify(run['discovery_run_id'])
            if self._manifest(fid)!=run['finalization_sha256']: raise ArtifactIntegrityError('Frontier final manifest mismatch')
        return True
    def finalize(self,fid):
        run=self.get(fid)
        if run['state']=='SEALED': self.verify(fid); return self.inspect(fid)
        if run['state']!='STOPPED': raise ValidationError('Policy must stop successfully before finalization')
        with _transaction(self.db),self.authorized('F',fid):
            self.verify(fid); d=self.discovery.get(run['discovery_run_id'])
            if d['state']!='SEALED': self.discovery.seal(run['discovery_run_id'])
            self.update('frontier_run','frontier_run_id',fid,state='SEALED',finalization_sha256=self._manifest(fid),sealed_at_utc=utc_now())
        return self.inspect(fid)
    def inspect(self,fid):
        run=self.get(fid); p=json.loads(run['policy_json']); usage=self._usage(run); rounds=self.rounds(fid); plans=[json.loads(r['plan_json']) for r in rounds]; results=[json.loads(r['result_json']) for r in rounds if r['result_json']]
        return dict(run=run,discovery_state=self.discovery.get(run['discovery_run_id'])['state'],rounds=rounds,actions=self.actions(fid),usage=usage,remaining_budgets=dict(work=p['global_new_work_budget']-len(usage['new_work_ids']),children=p['global_child_acquisition_budget']-len(usage['child_acquisition_ids'])),max_depth=p['max_depth'],current_depth=max([c['seed']['depth'] for plan in plans for c in plan['candidates']]+[0]),selected_seed_count=sum(r['selected_seed_count'] for r in results),deferred_seed_count=sum(any(v=='ROUND_SEED_LIMIT' for v in c['directions'].values()) for plan in plans for c in plan['candidates']),saturation_history=[r['saturation_streak'] for r in results],worst_case=FrontierPolicy.from_value(p).worst_case(),interpretation='Structural expansion only; no scientific recall or screening judgment.')
