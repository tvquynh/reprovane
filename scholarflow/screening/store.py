"""Offline human screening over sealed discovery; immutable evidence and corrections."""
import json
from collections import Counter
from contextlib import contextmanager
from ..stage09 import Stage09Store,stable
from ..frontier import FrontierStore
from ..core import ValidationError,ArtifactIntegrityError
from ..core.ids import new_id,utc_now
from ..core.provenance import canonical_json
from ..core.migrations import apply_migrations
from ..discovery.store import digest
from ..providers.scimago import _transaction
from .plan import ScreeningPlan,private_text,label
from . import evidence


class ScreeningStore(Stage09Store):
    def __init__(self,database,*,forbidden_values=()):
        super().__init__(database); self.frontier=FrontierStore(database); self.forbidden_values=tuple(forbidden_values)
        if not hasattr(database,'_screening_auth'): database._screening_auth=None
        database.connection.create_function('screening_authorized',2,lambda rid,phase:int(database._screening_auth==(rid,phase)))
        apply_migrations(database.connection,target_version=10)

    @contextmanager
    def auth(self,rid,phase='WRITE'):
        old=self.db._screening_auth; self.db._screening_auth=(rid,phase)
        try: yield
        finally: self.db._screening_auth=old

    def get(self,sid): return self.one('screening_run','screening_run_id',sid)
    def plan(self,sid): return json.loads(self.get(sid)['plan_json'])
    def candidates(self,sid): return [dict(r) for r in self.db.execute('SELECT * FROM screening_candidate WHERE screening_run_id=? ORDER BY publication_entity_id',(sid,))]
    def assignments(self,sid): return [dict(r) for r in self.db.execute('SELECT * FROM screening_assignment WHERE screening_run_id=? ORDER BY candidate_id,reviewer_label',(sid,))]
    def decisions(self,sid): return [dict(r) for r in self.db.execute('SELECT * FROM screening_decision WHERE screening_run_id=? ORDER BY decision_id',(sid,))]
    def adjudications(self,sid): return [dict(r) for r in self.db.execute('SELECT * FROM screening_adjudication WHERE screening_run_id=? ORDER BY adjudication_id',(sid,))]
    def outcomes(self,sid):
        return [dict(r) for r in self.db.execute('SELECT * FROM screening_outcome WHERE resolution_id=? ORDER BY publication_entity_id',(self.get(sid)['current_resolution_id'],))]
    def _writable(self,sid):
        run=self.get(sid)
        if run['state']=='SEALED': raise ValidationError('Screening run sealed; new screening context required')
        return run
    def _input(self,revision,rid):
        d=self.discovery.get(rid)
        if d['state']!='SEALED' or d['project_revision_id']!=revision: raise ValidationError('Sealed same-revision discovery required')
        self.discovery.verify(rid); f=self.db.execute('SELECT * FROM frontier_run WHERE discovery_run_id=?',(rid,)).fetchone()
        if f:
            if f['state']!='SEALED': raise ValidationError('Owning frontier must be sealed')
            self.frontier.verify(f['frontier_run_id'])
        return d,dict(f) if f else None
    def _unresolved(self,rid):
        events=[dict(r) for r in self.db.execute('SELECT * FROM discovery_event_view WHERE discovery_run_id=? AND resolved_publication_entity_id IS NULL ORDER BY event_id',(rid,))]
        summary=[dict(event_id=e['event_id'],provider_record_id=e['provider_record_id'],requested_target_id=e['requested_target_id'],metadata_state=e['metadata_state']) for e in events]
        count=self.discovery.inspect(rid)['statistics']['provider_level_unresolved_discoveries']
        return dict(provider_level_unresolved_count=count,event_count=len(summary),events=summary)
    def _candidate_hash(self,sid,candidates):
        return digest(dict(screening_run_id=sid,candidates=candidates))
    def create(self,project_revision_id,discovery_run_id,plan):
        plan=ScreeningPlan.from_value(plan); p=plan.as_dict(); sid=new_id('scr')
        with _transaction(self.db):
            self.foundation.get_project_revision(project_revision_id); d,f=self._input(project_revision_id,discovery_run_id); candidates=[]; assignments=[]; members=self.discovery.memberships(discovery_run_id)
            if p['assignment_policy']=='EXPLICIT_ASSIGNMENT' and set(p['assignments'])!={m['publication_entity_id'] for m in members}: raise ValidationError('Explicit assignments must cover exactly the final candidate entities')
            for index,m in enumerate(members):
                entity=m['publication_entity_id']; refs=evidence.freeze_refs(self,d,entity,p['allowed_evidence_fields']); body=evidence.project(self,refs,p['allowed_evidence_fields']); cid='scn_'+digest([sid,entity])[:40]
                candidates.append(dict(candidate_id=cid,screening_run_id=sid,publication_entity_id=entity,discovery_resolution_run_id=m['resolution_run_id'],ordinal=index,membership_basis_json=m['membership_basis_json'],evidence_refs_json=canonical_json(refs),evidence_json=canonical_json(body),evidence_sha256=digest(body)))
                reviewers=[a['label'] for a in p['reviewers']] if p['assignment_policy']=='ALL_REVIEWERS' else p['assignments'][entity]
                assignments.extend(dict(screening_run_id=sid,candidate_id=cid,reviewer_label=r) for r in reviewers)
            assignments.sort(key=lambda r:(r['candidate_id'],r['reviewer_label'])); unresolved=self._unresolved(discovery_run_id)
            with self.auth(sid,'CREATE'):
                self.insert('screening_run',dict(screening_run_id=sid,project_revision_id=project_revision_id,discovery_run_id=discovery_run_id,discovery_sha256=d['materialization_sha256'],frontier_run_id=f['frontier_run_id'] if f else None,frontier_sha256=f['finalization_sha256'] if f else None,plan_json=plan.canonical,plan_sha256=plan.sha256,candidate_set_sha256=self._candidate_hash(sid,candidates),assignment_sha256=digest(assignments),unresolved_discovery_json=canonical_json(unresolved),unresolved_discovery_sha256=digest(unresolved),state='OPEN',current_resolution_id=None,finalization_sha256=None,created_at_utc=utc_now(),sealed_at_utc=None))
                for key,role in [('reviewers','REVIEWER'),('adjudicators','ADJUDICATOR')]:
                    for actor in p[key]: self.insert('screening_reviewer',dict(screening_run_id=sid,reviewer_label=actor['label'],role=role,actor_type='HUMAN'))
                for c in candidates: self.insert('screening_candidate',c)
                for a in assignments: self.insert('screening_assignment',a)
            with self.auth(sid): self._materialize(sid)
        return self.inspect(sid)
    def _validate_vote(self,sid,decision,reasons,note,*,adjudication=False):
        p=self.plan(sid)
        if decision not in p['allowed_decisions'] or adjudication and decision=='SCREEN_UNSURE': raise ValidationError('Invalid explicit screening decision')
        if not isinstance(reasons,(list,tuple)) or any(not isinstance(r,str) for r in reasons): raise ValidationError('Reason codes must be a frozen-code list')
        reasons=sorted(set(reasons)); valid={r['code'] for r in p['criteria'] if r['kind']=='EXCLUSION_REASON'}
        if decision=='SCREEN_EXCLUDE':
            if not reasons or not set(reasons)<=valid or not p['exclusion_reason_policy']['multiple'] and len(reasons)>1: raise ValidationError('Valid frozen exclusion reason required')
        elif reasons: raise ValidationError('Only SCREEN_EXCLUDE carries exclusion reasons')
        note=private_text(note,p['note_policy']['max_length'] if p['note_policy']['allow'] else 0,self.forbidden_values)
        return reasons,note
    @staticmethod
    def _effective(rows,key,supersedes):
        replaced={r[supersedes] for r in rows if r[supersedes]}; return [r for r in rows if r[key] not in replaced]
    def _decision(self,sid,cid,reviewer,decision,reasons,note,supersedes):
        self._writable(sid); label(reviewer)
        if not self.db.execute('SELECT 1 FROM screening_assignment WHERE screening_run_id=? AND candidate_id=? AND reviewer_label=?',(sid,cid,reviewer)).fetchone(): raise ValidationError('Unassigned reviewer/candidate')
        reasons,note=self._validate_vote(sid,decision,reasons,note)
        body=dict(screening_run_id=sid,candidate_id=cid,reviewer_label=reviewer,decision=decision,reason_codes_json=canonical_json(reasons),note=note,supersedes_decision_id=supersedes); did='sdec_'+digest(body)[:40]
        existing=self.db.execute('SELECT * FROM screening_decision WHERE decision_id=?',(did,)).fetchone()
        if existing: return dict(existing)
        active=[r for r in self._effective(self.decisions(sid),'decision_id','supersedes_decision_id') if r['candidate_id']==cid and r['reviewer_label']==reviewer]
        if active:
            if self.plan(sid)['correction_policy']!='EXPLICIT_SUPERSESSION' or supersedes!=active[0]['decision_id']: raise ValidationError('Refresh and explicitly supersede current same-reviewer decision')
        elif supersedes is not None: raise ValidationError('Invalid superseded decision')
        return self.insert('screening_decision',dict(decision_id=did,**body,created_at_utc=utc_now()))
    def submit_decision(self,sid,candidate_id,reviewer_label,decision,*,reason_codes=(),note=None,supersedes_decision_id=None):
        with _transaction(self.db),self.auth(sid):
            row=self._decision(sid,candidate_id,reviewer_label,decision,reason_codes,note,supersedes_decision_id); self._materialize(sid)
        return row
    def _projection(self,sid):
        p=self.plan(sid); votes=self._effective(self.decisions(sid),'decision_id','supersedes_decision_id'); adjudications=self._effective(self.adjudications(sid),'adjudication_id','supersedes_adjudication_id'); rows=[]
        for c in self.candidates(sid):
            cid=c['candidate_id']; assigned=sorted(a['reviewer_label'] for a in self.assignments(sid) if a['candidate_id']==cid); current=sorted([r for r in votes if r['candidate_id']==cid],key=lambda r:r['decision_id']); ids=[r['decision_id'] for r in current]; values={r['decision'] for r in current}; outcome='UNRESOLVED'; status='PENDING'; basis='PENDING'; reasons=[]; adj=None
            if len(current)==len(assigned):
                if p['consensus_policy']=='UNANIMOUS' and len(values)==1 and 'SCREEN_UNSURE' not in values:
                    outcome=next(iter(values)); status='FINAL'; basis='AUTO_CONSENSUS'; reasons=sorted({code for r in current for code in json.loads(r['reason_codes_json'])})
                else:
                    status='UNSURE' if values=={'SCREEN_UNSURE'} else 'CONFLICT'
                    valid=[a for a in adjudications if a['candidate_id']==cid and json.loads(a['input_decision_ids_json'])==ids]
                    if valid and p['adjudication_policy']=='EXPLICIT':
                        a=valid[0]; adj=a['adjudication_id']; outcome=a['decision']; status='FINAL'; basis='ADJUDICATED'; reasons=json.loads(a['reason_codes_json'])
            body=dict(screening_run_id=sid,candidate_id=cid,publication_entity_id=c['publication_entity_id'],outcome=outcome,status=status,resolution_basis=basis,decision_ids_json=canonical_json(ids),adjudication_id=adj,reason_codes_json=canonical_json(reasons)); rows.append(dict(**body,outcome_sha256=digest(body)))
        input_hash=digest(dict(plan_sha256=self.get(sid)['plan_sha256'],candidate_set_sha256=self.get(sid)['candidate_set_sha256'],assignment_sha256=self.get(sid)['assignment_sha256'],effective_decision_ids=sorted(r['decision_id'] for r in votes),effective_adjudication_ids=sorted(a['adjudication_id'] for a in adjudications)))
        return input_hash,rows
    def _materialize(self,sid):
        self._writable(sid); input_hash,rows=self._projection(sid); resolution_id='sres_'+digest([sid,input_hash])[:40]
        old=self.db.execute('SELECT * FROM screening_resolution WHERE resolution_id=?',(resolution_id,)).fetchone()
        if not old:
            self.insert('screening_resolution',dict(resolution_id=resolution_id,screening_run_id=sid,input_sha256=input_hash,outcome_set_sha256=digest(rows),created_at_utc=utc_now()))
            for row in rows: self.insert('screening_outcome',dict(resolution_id=resolution_id,**row))
        missing=sum(len(json.loads(r['decision_ids_json']))<self.plan(sid)['required_reviewers'] for r in rows); state='OPEN' if missing else 'REVIEW_COMPLETE' if any(r['outcome']=='UNRESOLVED' for r in rows) else 'RESOLVED'
        self.update('screening_run','screening_run_id',sid,current_resolution_id=resolution_id,state=state)
        return rows
    def resolve(self,sid):
        with _transaction(self.db),self.auth(sid): self._materialize(sid)
        return self.inspect(sid)
    def submit_adjudication(self,sid,candidate_id,adjudicator_label,decision,*,input_decision_ids,reason_codes=(),note=None,supersedes_adjudication_id=None):
        with _transaction(self.db),self.auth(sid):
            self._writable(sid); p=self.plan(sid)
            if p['adjudication_policy']!='EXPLICIT' or adjudicator_label not in {a['label'] for a in p['adjudicators']}: raise ValidationError('Allowed human adjudicator required')
            reasons,note=self._validate_vote(sid,decision,reason_codes,note,adjudication=True); ids=sorted(set(input_decision_ids))
            _,projection=self._projection(sid); current=next((r for r in projection if r['candidate_id']==candidate_id),None)
            if not current or len(json.loads(current['decision_ids_json']))!=p['required_reviewers'] or ids!=json.loads(current['decision_ids_json']): raise ValidationError('Adjudication requires all exact effective reviewer inputs')
            body=dict(screening_run_id=sid,candidate_id=candidate_id,adjudicator_label=adjudicator_label,decision=decision,reason_codes_json=canonical_json(reasons),note=note,input_decision_ids_json=canonical_json(ids),supersedes_adjudication_id=supersedes_adjudication_id); aid='sadj_'+digest(body)[:40]
            existing=self.db.execute('SELECT * FROM screening_adjudication WHERE adjudication_id=?',(aid,)).fetchone()
            if existing: return dict(existing)
            active=[r for r in self._effective(self.adjudications(sid),'adjudication_id','supersedes_adjudication_id') if r['candidate_id']==candidate_id]
            if current['resolution_basis']=='AUTO_CONSENSUS': raise ValidationError('No adjudication of automatic consensus')
            if active:
                if p['adjudication_correction_policy']!='EXPLICIT_SUPERSESSION' or supersedes_adjudication_id!=active[0]['adjudication_id']: raise ValidationError('Explicit current adjudication supersession required')
            elif supersedes_adjudication_id is not None: raise ValidationError('Invalid adjudication supersession')
            row=self.insert('screening_adjudication',dict(adjudication_id=aid,**body,created_at_utc=utc_now())); self._materialize(sid)
        return row
    def review_packet(self,sid,reviewer_label):
        run=self.get(sid); p=self.plan(sid)
        if reviewer_label not in {r['label'] for r in p['reviewers']}: raise ValidationError('Unknown reviewer')
        assigned={a['candidate_id'] for a in self.assignments(sid) if a['reviewer_label']==reviewer_label}; common={k:run[k] for k in ['screening_run_id','plan_sha256','candidate_set_sha256','assignment_sha256']}; common['reviewer_label']=reviewer_label
        items=[dict(**common,candidate_id=c['candidate_id'],publication_entity_id=c['publication_entity_id'],evidence_sha256=c['evidence_sha256'],evidence=json.loads(c['evidence_json']),allowed_decisions=p['allowed_decisions'],criteria=p['criteria'],allowed_exclusion_codes=sorted(r['code'] for r in p['criteria'] if r['kind']=='EXCLUSION_REASON')) for c in self.candidates(sid) if c['candidate_id'] in assigned]
        body=dict(manifest=common,items=items); return dict(**body,packet_sha256=digest(body))
    def decision_template(self,sid,reviewer_label):
        packet=self.review_packet(sid,reviewer_label)
        return [dict(**{k:item[k] for k in ['screening_run_id','plan_sha256','candidate_set_sha256','assignment_sha256','reviewer_label','candidate_id','evidence_sha256']},packet_sha256=packet['packet_sha256'],decision=None,reason_codes=[],note=None,supersedes_decision_id=None) for item in packet['items']]
    def export_review_packet(self,sid,reviewer_label,directory):
        from pathlib import Path
        if Path(directory).is_symlink(): raise ValidationError('Packet export symlink directory refused')
        directory=Path(directory).resolve(); directory.mkdir(parents=True,exist_ok=True); packet=self.review_packet(sid,reviewer_label); template=self.decision_template(sid,reviewer_label)
        path=directory/(reviewer_label+'.review.jsonl'); target=directory/(reviewer_label+'.decisions.jsonl')
        contents=[(path,'\n'.join(canonical_json(row) for row in [dict(kind='REVIEW_MANIFEST',**packet['manifest'],packet_sha256=packet['packet_sha256']),*packet['items']])+'\n'),(target,''.join(canonical_json(row)+'\n' for row in template))]
        for file,text in contents:
            if file.is_symlink(): raise ValidationError('Packet export symlink file refused')
            if file.exists() and file.read_bytes()!=text.encode('utf-8'): raise ValidationError('Export refuses to overwrite different packet or completed decisions; choose a new directory')
        for file,text in contents:
            if not file.exists(): file.write_text(text,encoding='utf-8',newline='\n')
        return dict(packet_path=str(path),template_path=str(target),packet_sha256=packet['packet_sha256'])
    def import_decisions(self,sid,reviewer_label,rows):
        if not isinstance(rows,list): raise ValidationError('JSONL rows must form a list')
        expected={r['candidate_id']:r for r in self.decision_template(sid,reviewer_label)}; result=[]
        with _transaction(self.db),self.auth(sid):
            self._writable(sid)
            for row in rows:
                if not isinstance(row,dict) or set(row)!=set(next(iter(expected.values()),self._empty_template_keys())): raise ValidationError('Invalid decision packet fields')
                target=expected.get(row['candidate_id'])
                if not target or any(row[k]!=target[k] for k in ['screening_run_id','plan_sha256','candidate_set_sha256','assignment_sha256','reviewer_label','candidate_id','evidence_sha256','packet_sha256']): raise ValidationError('Stale/mismatched decision packet')
                result.append(self._decision(sid,row['candidate_id'],reviewer_label,row['decision'],row['reason_codes'],row['note'],row['supersedes_decision_id']))
            self._materialize(sid)
        return result
    @staticmethod
    def _empty_template_keys(): return dict.fromkeys(['screening_run_id','plan_sha256','candidate_set_sha256','assignment_sha256','reviewer_label','candidate_id','evidence_sha256','packet_sha256','decision','reason_codes','note','supersedes_decision_id'])
    def export_conflicts(self,sid,adjudicator_label):
        if adjudicator_label not in {a['label'] for a in self.plan(sid)['adjudicators']}: raise ValidationError('Allowed human adjudicator required')
        current=self._effective(self.decisions(sid),'decision_id','supersedes_decision_id'); _,rows=self._projection(sid)
        return [dict(candidate_id=r['candidate_id'],publication_entity_id=r['publication_entity_id'],status=r['status'],input_decision_ids=json.loads(r['decision_ids_json']),decisions=[v for v in current if v['candidate_id']==r['candidate_id']]) for r in rows if r['status'] in ('CONFLICT','UNSURE')]
    def _manifest(self,sid):
        run={k:v for k,v in stable(self.get(sid)).items() if k not in ('state','finalization_sha256')}
        return digest(dict(run=run,candidates=self.candidates(sid),assignments=self.assignments(sid),decisions=[stable(r) for r in self.decisions(sid)],adjudications=[stable(r) for r in self.adjudications(sid)],resolutions=[stable(dict(r)) for r in self.db.execute('SELECT * FROM screening_resolution WHERE screening_run_id=? ORDER BY resolution_id',(sid,))],outcomes=[dict(r) for r in self.db.execute('SELECT * FROM screening_outcome WHERE screening_run_id=? ORDER BY resolution_id,candidate_id',(sid,))]))
    def verify(self,sid):
        run=self.get(sid); p=ScreeningPlan.from_value(json.loads(run['plan_json'])); d,f=self._input(run['project_revision_id'],run['discovery_run_id'])
        if p.sha256!=run['plan_sha256'] or d['materialization_sha256']!=run['discovery_sha256'] or (f['frontier_run_id'] if f else None)!=run['frontier_run_id'] or (f['finalization_sha256'] if f else None)!=run['frontier_sha256']: raise ArtifactIntegrityError('Screening input/plan changed')
        candidates=self.candidates(sid); assignments=self.assignments(sid); members=self.discovery.memberships(run['discovery_run_id'])
        if {c['publication_entity_id'] for c in candidates}!={m['publication_entity_id'] for m in members} or self._candidate_hash(sid,candidates)!=run['candidate_set_sha256'] or digest(assignments)!=run['assignment_sha256']: raise ArtifactIntegrityError('Screening frozen set mismatch')
        expected_actors=sorted([dict(screening_run_id=sid,reviewer_label=a['label'],role=role,actor_type='HUMAN') for key,role in [('reviewers','REVIEWER'),('adjudicators','ADJUDICATOR')] for a in p.as_dict()[key]],key=lambda r:r['reviewer_label'])
        actual=[dict(r) for r in self.db.execute('SELECT * FROM screening_reviewer WHERE screening_run_id=? ORDER BY reviewer_label',(sid,))]
        if actual!=expected_actors: raise ArtifactIntegrityError('Frozen human actors mismatch')
        plan=p.as_dict()
        for c in candidates:
            expected=[a['label'] for a in plan['reviewers']] if plan['assignment_policy']=='ALL_REVIEWERS' else plan['assignments'][c['publication_entity_id']]
            if sorted(a['reviewer_label'] for a in assignments if a['candidate_id']==c['candidate_id'])!=expected or c['candidate_id']!='scn_'+digest([sid,c['publication_entity_id']])[:40]: raise ArtifactIntegrityError('Frozen assignment/candidate identity mismatch')
            body=evidence.project(self,json.loads(c['evidence_refs_json']),plan['allowed_evidence_fields'])
            if canonical_json(body)!=c['evidence_json'] or digest(body)!=c['evidence_sha256']: raise ArtifactIntegrityError('Frozen candidate evidence mismatch')
        summary=self._unresolved(run['discovery_run_id'])
        if canonical_json(summary)!=run['unresolved_discovery_json'] or digest(summary)!=run['unresolved_discovery_sha256']: raise ArtifactIntegrityError('Unresolved discovery evidence mismatch')
        for table,rows,key,prefix in [('screening_decision',self.decisions(sid),'decision_id','sdec_'),('screening_adjudication',self.adjudications(sid),'adjudication_id','sadj_')]:
            for row in rows:
                body={k:v for k,v in stable(row).items() if k!=key}
                if row[key]!=prefix+digest(body)[:40]: raise ArtifactIntegrityError('Immutable human decision tampered')
                self._validate_vote(sid,row['decision'],json.loads(row['reason_codes_json']),row['note'],adjudication=table=='screening_adjudication')
        ih,expected=self._projection(sid); resolution=self.one('screening_resolution','resolution_id',run['current_resolution_id']); actual=[{k:v for k,v in r.items() if k!='resolution_id'} for r in self.outcomes(sid)]
        if resolution['input_sha256']!=ih or resolution['outcome_set_sha256']!=digest(expected) or actual!=expected: raise ArtifactIntegrityError('Screening outcome recomputation mismatch')
        missing=any(len(json.loads(r['decision_ids_json']))!=plan['required_reviewers'] for r in expected); state='OPEN' if missing else 'REVIEW_COMPLETE' if any(r['outcome']=='UNRESOLVED' for r in expected) else 'RESOLVED'
        if run['state']!='SEALED' and run['state']!=state or run['state']=='SEALED' and (state!='RESOLVED' or self._manifest(sid)!=run['finalization_sha256']): raise ArtifactIntegrityError('Screening lifecycle/manifest mismatch')
        return True
    def finalize(self,sid):
        if self.get(sid)['state']=='SEALED': self.verify(sid); return self.inspect(sid)
        with _transaction(self.db),self.auth(sid):
            self._materialize(sid); self.verify(sid)
            if self.get(sid)['state']!='RESOLVED': raise ValidationError('Every assigned candidate must finally resolve before seal')
            manifest=self._manifest(sid)
            with self.auth(sid,'SEAL'): self.update('screening_run','screening_run_id',sid,state='SEALED',finalization_sha256=manifest,sealed_at_utc=utc_now())
        return self.inspect(sid)
    def handoff(self,sid):
        if self.get(sid)['state']!='SEALED': raise ValidationError('Read-only handoff requires sealed screening')
        self.verify(sid); run=self.get(sid)
        outcomes=self.outcomes(sid); used={v for r in outcomes for v in json.loads(r['decision_ids_json'])}; adjudicated={r['adjudication_id'] for r in outcomes if r['adjudication_id']}
        return dict(screening_run_id=sid,project_revision_id=run['project_revision_id'],screening_manifest_sha256=run['finalization_sha256'],screen_include_entity_ids=[r['publication_entity_id'] for r in outcomes if r['outcome']=='SCREEN_INCLUDE'],screen_exclude_entity_ids=[r['publication_entity_id'] for r in outcomes if r['outcome']=='SCREEN_EXCLUDE'],outcomes=outcomes,decision_provenance=[r for r in self.decisions(sid) if r['decision_id'] in used],adjudication_provenance=[r for r in self.adjudications(sid) if r['adjudication_id'] in adjudicated],interpretation='SCREEN_INCLUDE is a resolved screening outcome only.')
    def inspect(self,sid):
        run=self.get(sid); votes=self._effective(self.decisions(sid),'decision_id','supersedes_decision_id'); _,rows=self._projection(sid); n=len(rows); assignments=self.assignments(sid); total=len(assignments); missing=total-len(votes); conflicts=sum(r['status'] in ('CONFLICT','UNSURE') for r in rows); reason_counts=Counter(code for r in rows if r['outcome']=='SCREEN_EXCLUDE' for code in json.loads(r['reason_codes_json'])); adjudicated=sum(r['resolution_basis']=='ADJUDICATED' for r in rows)
        return dict(run=run,candidate_count=n,unresolved_discovery=json.loads(run['unresolved_discovery_json']),assignments=assignments,statistics=dict(review_completion_rate=(total-missing)/total if total else 1.0,missing_required_decisions=missing,decisions_by_reviewer={a['label']:dict(Counter(r['decision'] for r in votes if r['reviewer_label']==a['label'])) for a in self.plan(sid)['reviewers']},decision_distribution=dict(Counter(r['decision'] for r in votes)),historical_decision_count=len(self.decisions(sid)),effective_decision_count=len(votes),conflict_count=conflicts,conflict_rate=conflicts/n if n else 0.0,adjudication_count=len(self.adjudications(sid)),active_adjudicated_count=adjudicated,adjudication_rate=adjudicated/n if n else 0.0,resolved_screen_include=sum(r['outcome']=='SCREEN_INCLUDE' for r in rows),resolved_screen_exclude=sum(r['outcome']=='SCREEN_EXCLUDE' for r in rows),unresolved_candidate_count=sum(r['outcome']=='UNRESOLVED' for r in rows),exclusion_reason_counts=dict(sorted(reason_counts.items()))),outcomes=rows)
