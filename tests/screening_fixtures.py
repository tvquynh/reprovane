"""Synthetic keyless upstream state; no network or real human judgments."""
import json,tempfile,unittest
from pathlib import Path
from scholarflow.core.db import Database
from scholarflow.screening import ScreeningStore
from scholarflow.openalex import OpenAlexClient
from openalex_acquisition_fixtures import work,page,Session,Response


def plan(**changes):
    value=json.loads((Path(__file__).parent/'fixtures/screening/plan.json').read_text(encoding='utf-8')); value.update(changes); return value


class ScreeningCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.db=Database(self.root/'research'); self.store=ScreeningStore(self.db); self.discovery=self.store.discovery
        self.session=Session(); self.client=OpenAlexClient('',session=self.session,max_attempts=1,sleeper=lambda _:None)
        self.project=self.store.foundation.create_project('synthetic-screening','Synthetic implementation validation'); self.revision=self.store.foundation.create_project_revision(self.project['project_id'],1,'json','{"purpose":"synthetic screening tests"}')['revision_id']; self.counter=0
        self.rid=self.discovery.create(self.revision)['discovery_run_id']
    def tearDown(self): self.client.close(); self.db.close(); self.temp.cleanup()
    def primary(self,works,register=True):
        self.counter+=1; self.session.responses=[Response(page(works))]; a=self.store.acquisitions.acquire(dict(search='synthetic '+str(self.counter),max_records=max(1,len(works)),page_size=min(100,max(1,len(works)))),self.client,project_revision_id=self.revision)
        if register: self.discovery.register_primary(self.rid,a['acquisition_id'])
        return a
    def screening(self,works=None,protocol=None):
        if self.discovery.get(self.rid)['state']=='OPEN':
            self.primary([work(1)] if works is None else works); self.discovery.seal(self.rid)
        result=self.store.create(self.revision,self.rid,protocol or plan()); self.sid=result['run']['screening_run_id']; self.cs=self.store.candidates(self.sid); return result
    def vote(self,reviewer='reviewer_1',decision='SCREEN_INCLUDE',candidate=0,**kwargs): return self.store.submit_decision(self.sid,self.cs[candidate]['candidate_id'],reviewer,decision,**kwargs)
    def unanimous(self,candidate=0,decision='SCREEN_INCLUDE',**kwargs):
        return [self.vote(r['label'],decision,candidate,**kwargs) for r in self.store.plan(self.sid)['reviewers']]
    def conflict(self):
        self.vote(); self.vote('reviewer_2','SCREEN_EXCLUDE',reason_codes=['X']); return self.store.export_conflicts(self.sid,'adjudicator_1')[0]
    def adjudicate(self,decision='SCREEN_EXCLUDE',**kwargs):
        rows=self.store.export_conflicts(self.sid,'adjudicator_1'); current=rows[0]; return self.store.submit_adjudication(self.sid,current['candidate_id'],'adjudicator_1',decision,input_decision_ids=current['input_decision_ids'],reason_codes=['X'] if decision=='SCREEN_EXCLUDE' else [],**kwargs)
