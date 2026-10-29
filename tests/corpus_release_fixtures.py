"""Tiny scripted synthetic screening inputs, not actual human judgments."""
import json
from pathlib import Path
from screening_fixtures import ScreeningCase
from openalex_acquisition_fixtures import work
from scholarflow.corpus_release import CorpusReleaseStore


def plan(**changes):
    p=json.loads((Path(__file__).parent/'fixtures/corpus_release/plan.json').read_text(encoding='utf-8')); p.update(changes); return p


class CorpusCase(ScreeningCase):
    def setUp(self): super().setUp(); self.releases=CorpusReleaseStore(self.db)
    def prepare(self,works=None,include_count=2,adjudicated=False):
        self.screening([work(1),work(2),work(3)] if works is None else works)
        for index,c in enumerate(self.cs):
            outcome='SCREEN_INCLUDE' if index<include_count else 'SCREEN_EXCLUDE'
            if adjudicated and index==0:
                self.vote(candidate=index); self.vote('reviewer_2','SCREEN_EXCLUDE',candidate=index,reason_codes=['X']); conflict=next(r for r in self.store.export_conflicts(self.sid,'adjudicator_1') if r['candidate_id']==c['candidate_id']); self.store.submit_adjudication(self.sid,c['candidate_id'],'adjudicator_1','SCREEN_INCLUDE',input_decision_ids=conflict['input_decision_ids'])
            else: self.unanimous(index,outcome,reason_codes=['X'] if outcome=='SCREEN_EXCLUDE' else [])
        self.store.finalize(self.sid)
    def create_release(self,protocol=None):
        result=self.releases.create(self.revision,self.sid,protocol or plan()); self.crid=result['release']['release_id']; return result
    def release(self,works=None,include_count=2,finalize=True,protocol=None,adjudicated=False):
        self.prepare(works,include_count,adjudicated); result=self.create_release(protocol)
        return self.releases.finalize(self.crid) if finalize else result
