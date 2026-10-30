"""Immutable discovery provenance over accepted acquisition/typed/resolution layers."""
import hashlib,json,re
from collections import Counter,defaultdict
from contextlib import contextmanager
from ..core import ValidationError,ArtifactIntegrityError
from ..core.ids import new_id,utc_now
from ..core.migrations import apply_migrations
from ..core.provenance import canonical_json
from ..openalex_acquisition import OpenAlexAcquisitionStore,AcquisitionSpec
from ..openalex_acquisition.normalize import native_id,NORMALIZER
from ..openalex_acquisition.observation_store import AcquisitionObservationStore
from ..observations import NormalizationProvenance
from ..resolution import ResolutionStore
from ..resolution.policy import ResolverPolicy
from ..providers.scimago import _transaction

def digest(value): return hashlib.sha256(canonical_json(value).encode()).hexdigest()

PROFILE=dict(normalizer_name=NORMALIZER.normalizer_name,normalizer_version=NORMALIZER.normalizer_version,observation_schema_version=NORMALIZER.observation_schema_version)
CITATION_PROFILE=NormalizationProvenance('openalex-citation','1','1')


class DiscoveryStore:
    def __init__(self,database):
        self.db=database; self.acquisitions=OpenAlexAcquisitionStore(database); self.foundation=self.acquisitions.foundation
        self.resolver=ResolutionStore(database); self.observations=AcquisitionObservationStore(database); self._authorization=None
        database.connection.create_function('discovery_authorized',2,lambda rid,phase:int(self._authorization==(rid,phase)))
        apply_migrations(database.connection,target_version=8)

    @contextmanager
    def _authorize(self,rid,phase='OPEN'):
        before=self._authorization; self._authorization=(rid,phase)
        try: yield
        finally: self._authorization=before

    def get(self,rid): return self.foundation._one('discovery_run','discovery_run_id',rid)
    def _open(self,rid):
        run=self.get(rid)
        if run['state']!='OPEN': raise ValidationError('Discovery run is immutable after materialization')
        return run

    def create(self,project_revision_id,*,parent_run_id=None):
        revision=self.foundation.get_project_revision(project_revision_id)
        if parent_run_id:
            parent=self.get(parent_run_id); previous=self.foundation.get_project_revision(parent['project_revision_id'])
            if parent['state']!='SEALED' or previous['project_id']!=revision['project_id']: raise ValidationError('Parent discovery must be sealed in the same project')
        with _transaction(self.db):
            return self.foundation._insert('discovery_run',dict(discovery_run_id=new_id('dsc'),project_revision_id=project_revision_id,parent_run_id=parent_run_id,state='OPEN',resolution_run_id=None,
                materialization_sha256=None,created_at_utc=utc_now(),materialized_at_utc=None,sealed_at_utc=None))

    def events(self,rid): return [dict(r) for r in self.db.execute('SELECT * FROM discovery_event WHERE discovery_run_id=? ORDER BY event_id',(rid,))]
    def operations(self,rid): return [dict(r) for r in self.db.execute('SELECT * FROM discovery_operation WHERE discovery_run_id=? ORDER BY operation_id',(rid,))]
    def memberships(self,rid): return [dict(r) for r in self.db.execute('SELECT * FROM discovery_membership WHERE discovery_run_id=? ORDER BY publication_entity_id',(rid,))]

    def _operation(self,rid,kind,spec):
        self._open(rid); body=canonical_json(dict(kind=kind,**spec)); sha=hashlib.sha256(body.encode()).hexdigest(); oid='dop_'+digest([rid,sha])[:40]
        existing=self.db.execute('SELECT * FROM discovery_operation WHERE operation_id=?',(oid,)).fetchone()
        if existing: return dict(existing)
        with _transaction(self.db),self._authorize(rid):
            return self.foundation._insert('discovery_operation',dict(operation_id=oid,discovery_run_id=rid,operation_kind=kind,specification_json=body,specification_sha256=sha,
                state='OPEN',result_resolution_run_id=None,statistics_json='{}',plan_json=None,created_at_utc=utc_now(),completed_at_utc=None))

    def _op_update(self,op,**values):
        with _transaction(self.db),self._authorize(op['discovery_run_id']):
            self.db.execute('UPDATE discovery_operation SET '+','.join(k+'=?' for k in values)+' WHERE operation_id=?',(*values.values(),op['operation_id']))

    def _complete(self,op,stats=None,resolution=None):
        self._op_update(op,state='COMPLETE',statistics_json=canonical_json(stats or {}),result_resolution_run_id=resolution,completed_at_utc=utc_now())

    def _attempt(self,op,suppressed):
        self.foundation._insert('discovery_attempt',dict(attempt_id=new_id('dat'),discovery_run_id=op['discovery_run_id'],operation_id=op['operation_id'],suppressed_event_count=suppressed,created_at_utc=utc_now()))

    def _replay_operation(self,op):
        with _transaction(self.db),self._authorize(op['discovery_run_id']):
            count=self.db.execute('SELECT count(*) FROM discovery_event WHERE operation_id=?',(op['operation_id'],)).fetchone()[0]; self._attempt(op,count)
        return self.inspect(op['discovery_run_id'])

    def _bind(self,op,aid,role):
        if self.db.execute('SELECT 1 FROM discovery_operation_acquisition WHERE operation_id=? AND acquisition_id=? AND role=?',(op['operation_id'],aid,role)).fetchone(): return
        with _transaction(self.db),self._authorize(op['discovery_run_id']):
            self.foundation._insert('discovery_operation_acquisition',dict(operation_id=op['operation_id'],acquisition_id=aid,role=role))

    def _child(self,op,spec,client,role):
        spec=AcquisitionSpec.from_value(spec); client.private.check(spec.canonical)
        run=self._open(op['discovery_run_id']); acquisition=self.acquisitions.create(run['project_revision_id'],spec)
        self._bind(op,acquisition['acquisition_id'],role)
        return self.acquisitions.resume(acquisition['acquisition_id'],spec,client)

    def _work(self,record_id):
        record=self.foundation._one('provider_record','provider_record_id',record_id); meta=json.loads(record['metadata_json'])
        if meta.get('entity_type')!='work': raise ValidationError('Discovery requires a Stage07 work record')
        acquisition=self.acquisitions.get(meta['openalex_acquisition'])
        if acquisition['state']!='COMPLETE' or acquisition['snapshot_id']!=record['snapshot_id']: raise ValidationError('Discovery requires a completed work acquisition')
        self.foundation.artifacts.verify(record['artifact_sha256'])
        anchor=meta['occurrences'][0]; pointer=anchor['pointer']; match=re.fullmatch(r'/results/(\d+)',pointer)
        if not match or anchor['artifact_sha256']!=record['artifact_sha256']: raise ArtifactIntegrityError('Work locator mismatch')
        data=json.loads(self.foundation.artifacts.path_for_hash(record['artifact_sha256']).read_bytes()); raw=data['results'][int(match[1])]
        if native_id(raw['id'])!=record['provider_native_id']: raise ArtifactIntegrityError('Work/native identity mismatch')
        return dict(record=record,acquisition=acquisition,raw=raw,locator=dict(artifact_sha256=record['artifact_sha256'],pointer=pointer,page_index=anchor['page_index']))

    def _event(self,op,kind,info=None,*,requested=None,seed=None,evidence=None,citation=None,state='ACQUIRED',self_loop=False):
        body=dict(discovery_run_id=op['discovery_run_id'],operation_id=op['operation_id'],event_type=kind,
            acquisition_id=info['acquisition']['acquisition_id'] if info else (evidence or {}).get('acquisition_id'),
            provider_record_id=info['record']['provider_record_id'] if info else None,requested_target_id=requested,
            returned_work_id=info['record']['provider_native_id'] if info else None,seed_resolution_run_id=seed['resolution_run_id'] if seed else None,
            seed_entity_id=seed['publication_entity_id'] if seed else None,seed_record_id=seed['provider_record_id'] if seed else None,seed_work_id=seed['work_id'] if seed else None,
            seed_depth=seed['depth'] if seed else None,depth=seed['depth']+1 if seed else 0,citation_observation_id=citation,
            metadata_state=state,self_loop=int(self_loop),evidence_json=canonical_json(evidence or {}))
        eid='dev_'+digest(body)[:40]
        if self.db.execute('SELECT 1 FROM discovery_event WHERE event_id=?',(eid,)).fetchone(): return False
        self.foundation._insert('discovery_event',dict(event_id=eid,**body,created_at_utc=utc_now())); return True

    def register_primary(self,rid,acquisition_id):
        run=self._open(rid); acquisition=self.acquisitions.get(acquisition_id)
        if acquisition['state']!='COMPLETE' or acquisition['project_revision_id']!=run['project_revision_id']: raise ValidationError('Primary acquisition must be complete in the discovery project revision')
        self.foundation.verify_provider_snapshot(acquisition['snapshot_id'])
        with _transaction(self.db),self._authorize(rid):
            op=self._operation(rid,'PRIMARY_QUERY',dict(acquisition_id=acquisition_id))
            if op['state']=='COMPLETE': return self._replay_operation(op)
            self._bind(op,acquisition_id,'PRIMARY_SOURCE')
            for record in self.foundation.list_provider_records(acquisition['snapshot_id']):
                info=self._work(record['provider_record_id'])
                self._event(op,'PRIMARY_QUERY',info,requested=record['provider_native_id'],evidence=dict(request_spec_sha256=acquisition['request_spec_sha256'],raw=info['locator']))
            self._attempt(op,0); self._complete(op)
        return self.inspect(rid)

    def _records(self,rid):
        return sorted({e[k] for e in self.events(rid) for k in ['provider_record_id','seed_record_id'] if e[k]})

    def _resolve(self,revision,records):
        if not records: return None
        run=self.resolver.create_resolution_run(revision,dict(profiles=[PROFILE]),policy=ResolverPolicy())
        key=run['resolution_run_id']; self.resolver.add_resolution_inputs(key,sorted(set(records))); self.resolver.generate_candidates(key)
        self.resolver.evaluate_candidates_automatically(key); self.resolver.materialize_resolution_run(key); self.resolver.seal_resolution_run(key)
        return key

    def _safe_map(self,resolution_id):
        if resolution_id is None: return {},set()
        run=self.resolver.get_resolution_run(resolution_id)
        if run['sealed_at_utc'] is None: raise ValidationError('Discovery requires sealed publication resolution')
        members={m['provider_record_id']:m['entity_id'] for m in self.resolver.list_entity_members(resolution_id)}
        candidates={c['candidate_id']:c for c in self.resolver.list_candidates(resolution_id)}; blocked=set()
        for d in json.loads(run['decision_manifest_json']):
            if d['decision_type']=='AMBIGUOUS':
                c=candidates[d['candidate_id']]; blocked.update([c['left_provider_record_id'],c['right_provider_record_id']])
        for row in self.resolver.list_resolution_inputs(resolution_id):
            if json.loads(row['evidence_json'])['evidence_warnings']: blocked.add(row['provider_record_id'])
        blocked_entities={members[r] for r in blocked}; return {r:e for r,e in members.items() if e not in blocked_entities},set(members)-{r for r,e in members.items() if e not in blocked_entities}

    def resolve_seeds(self,rid):
        run=self._open(rid); records=self._records(rid)
        if not records: raise ValidationError('No provider discoveries to resolve')
        with _transaction(self.db),self._authorize(rid):
            op=self._operation(rid,'RESOLUTION_CHECKPOINT',dict(provider_record_ids=records,profile=PROFILE))
            if op['state']=='COMPLETE': key=op['result_resolution_run_id']
            else:
                key=self._resolve(run['project_revision_id'],records); self._complete(op,resolution=key)
        mapping,blocked=self._safe_map(key)
        return dict(resolution_run_id=key,provider_records=[dict(provider_record_id=r,publication_entity_id=mapping.get(r),state='RESOLVED' if r in mapping else 'AMBIGUOUS') for r in records])

    def _seed(self,rid,value):
        if set(value)!={'resolution_run_id','publication_entity_id','provider_record_id','depth'} or type(value['depth']) is not int or not 0<=value['depth']<=10000: raise ValidationError('Explicit resolved seed and depth required')
        resolution=self.resolver.get_resolution_run(value['resolution_run_id']); run=self._open(rid)
        if resolution['project_revision_id']!=run['project_revision_id']: raise ValidationError('Seed project revision mismatch')
        self.resolver.verify_resolution_run(value['resolution_run_id']); mapping,_=self._safe_map(value['resolution_run_id'])
        if mapping.get(value['provider_record_id'])!=value['publication_entity_id']: raise ValidationError('Seed identity is unresolved/ambiguous')
        info=self._work(value['provider_record_id'])
        if info['acquisition']['project_revision_id']!=run['project_revision_id']: raise ValidationError('Seed acquisition project revision mismatch')
        return dict(**value,work_id=info['record']['provider_native_id'])

    def expand_backward(self,rid,seeds,client,*,max_results,batch_size=100,corpus=None):
        from .expansion import expand
        return expand(self,rid,seeds,client,'BACKWARD_CITATION',max_results,batch_size,corpus)

    def expand_forward(self,rid,seeds,client,*,max_results,page_size=100,corpus=None):
        from .expansion import expand
        return expand(self,rid,seeds,client,'FORWARD_CITATION',max_results,page_size,corpus)

    def _projection(self,rid,resolution_id):
        mapping,_=self._safe_map(resolution_id); groups=defaultdict(list)
        for event in self.events(rid):
            entity=mapping.get(event['provider_record_id'])
            if entity and not event['self_loop']: groups[entity].append(event)
        return [dict(discovery_run_id=rid,resolution_run_id=resolution_id,publication_entity_id=e,membership_basis_json=canonical_json(dict(event_ids=sorted(x['event_id'] for x in events),provider_record_ids=sorted({x['provider_record_id'] for x in events})))) for e,events in sorted(groups.items())]

    def _manifest(self,rid,resolution_id,members):
        def stable(row): return {k:v for k,v in row.items() if not k.endswith('_at_utc')}
        ops=self.operations(rid); bindings=[dict(r) for r in self.db.execute('SELECT b.* FROM discovery_operation_acquisition b JOIN discovery_operation o ON o.operation_id=b.operation_id WHERE o.discovery_run_id=? ORDER BY b.operation_id,b.acquisition_id,b.role',(rid,))]
        receipts=[dict(r) for r in self.db.execute('SELECT * FROM discovery_attempt WHERE discovery_run_id=? ORDER BY attempt_id',(rid,))]
        return digest(dict(run_id=rid,project_revision_id=self.get(rid)['project_revision_id'],events=[stable(e) for e in self.events(rid)],operations=[stable(o) for o in ops],bindings=bindings,attempts=[stable(a) for a in receipts],members=[stable(m) for m in members],
            resolution_run_id=resolution_id,resolution_hash=self.resolver.get_resolution_run(resolution_id)['materialization_manifest_sha256'] if resolution_id else None))

    def materialize(self,rid):
        run=self._open(rid)
        if any(op['state']!='COMPLETE' for op in self.operations(rid)): raise ValidationError('Unfinished discovery operation prevents materialization')
        with _transaction(self.db):
            key=self._resolve(run['project_revision_id'],self._records(rid)); members=self._projection(rid,key)
            with self._authorize(rid,'MATERIALIZE'):
                for m in members: self.foundation._insert('discovery_membership',dict(**m,created_at_utc=utc_now()))
                manifest=self._manifest(rid,key,members)
                self.db.execute("UPDATE discovery_run SET state='MATERIALIZED',resolution_run_id=?,materialization_sha256=?,materialized_at_utc=? WHERE discovery_run_id=?",(key,manifest,utc_now(),rid))
        return self.inspect(rid)

    def verify(self,rid):
        run=self.get(rid)
        if run['state']=='OPEN': raise ValidationError('Discovery run is not materialized')
        if run['resolution_run_id']: self.resolver.verify_resolution_run(run['resolution_run_id'])
        expected=self._projection(rid,run['resolution_run_id']); actual=[{k:v for k,v in m.items() if k!='created_at_utc'} for m in self.memberships(rid)]
        if actual!=expected or self._manifest(rid,run['resolution_run_id'],expected)!=run['materialization_sha256']: raise ArtifactIntegrityError('Discovery projection/manifest mismatch')
        snapshots={}
        for event in self.events(rid):
            for field in ['provider_record_id','seed_record_id']:
                if event[field]:
                    info=self._work(event[field]); snapshots[info['record']['snapshot_id']]=True
        for row in self.db.execute('SELECT DISTINCT a.snapshot_id FROM discovery_operation_acquisition b JOIN discovery_operation o ON o.operation_id=b.operation_id JOIN openalex_acquisition a ON a.acquisition_id=b.acquisition_id WHERE o.discovery_run_id=? AND a.snapshot_id IS NOT NULL',(rid,)): snapshots[row[0]]=True
        for sid in snapshots: self.foundation.verify_provider_snapshot(sid)
        return True

    def seal(self,rid):
        with _transaction(self.db):
            if self.get(rid)['state']=='OPEN': self.materialize(rid)
            if self.get(rid)['state']=='SEALED': raise ValidationError('Discovery run already sealed')
            self.verify(rid)
            with self._authorize(rid,'SEAL'):
                self.db.execute("UPDATE discovery_run SET state='SEALED',sealed_at_utc=? WHERE discovery_run_id=?",(utc_now(),rid))
        return self.inspect(rid)

    def inspect(self,rid):
        run=self.get(rid); events=self.events(rid); members=self.memberships(rid)
        view=[dict(r) for r in self.db.execute('SELECT * FROM discovery_event_view WHERE discovery_run_id=? ORDER BY event_id',(rid,))]
        unresolved={('record:'+e['provider_record_id']) if e['provider_record_id'] else ('requested:'+str(e['requested_target_id'])) for e in view if e['resolved_publication_entity_id'] is None}
        edges={tuple(r) for r in self.db.execute('SELECT DISTINCT c.source_identifier_value,c.target_identifier_value FROM citation_edge_observation c JOIN discovery_event e ON e.citation_observation_id=c.observation_id WHERE e.discovery_run_id=?',(rid,))}
        bindings=[dict(r) for r in self.db.execute('SELECT b.* FROM discovery_operation_acquisition b JOIN discovery_operation o ON o.operation_id=b.operation_id WHERE o.discovery_run_id=? ORDER BY b.operation_id,b.acquisition_id,b.role',(rid,))]
        entity_routes=Counter(e['resolved_publication_entity_id'] for e in view if e['resolved_publication_entity_id'])
        suppressed=self.db.execute('SELECT coalesce(sum(suppressed_event_count),0) FROM discovery_attempt WHERE discovery_run_id=?',(rid,)).fetchone()[0]
        suppressed+=sum(json.loads(o['statistics_json']).get('duplicate_reference_ids',0) for o in self.operations(rid))
        return dict(run=run,statistics=dict(event_counts={k:sum(e['event_type']==k for e in events) for k in ['PRIMARY_QUERY','BACKWARD_CITATION','FORWARD_CITATION']},
            canonical_discovered_publications=len(members),provider_level_unresolved_discoveries=len(unresolved),unique_citation_edges=len(edges),duplicate_exact_events_suppressed=suppressed,
            publications_with_multiple_routes=sum(n>1 for n in entity_routes.values()),depth_distribution=dict(sorted(Counter(str(e['depth']) for e in events).items())),
            unique_seeds=len({e['seed_work_id'] for e in events if e['seed_work_id']}),backward_edges=sum(e['event_type']=='BACKWARD_CITATION' and e['citation_observation_id'] is not None for e in events),
            forward_edges=sum(e['event_type']=='FORWARD_CITATION' and e['citation_observation_id'] is not None for e in events),
            missing_unresolved_targets=sum(e['provider_record_id'] is None for e in events),child_acquisition_count=len({b['acquisition_id'] for b in bindings if b['role']!='PRIMARY_SOURCE'})),
            primary_acquisition_ids=sorted({b['acquisition_id'] for b in bindings if b['role']=='PRIMARY_SOURCE'}),child_acquisition_ids=sorted({b['acquisition_id'] for b in bindings if b['role']!='PRIMARY_SOURCE'}),events=view,memberships=members,operations=self.operations(rid))
