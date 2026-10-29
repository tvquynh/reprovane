import json,tempfile,unittest
from pathlib import Path
from scholarflow.core.db import Database
from scholarflow.openalex import OpenAlexClient
from scholarflow.openalex_acquisition import OpenAlexAcquisitionStore,AcquisitionSpec

SYNTHETIC_KEY='SYNTHETIC_OPENALEX_TEST_CREDENTIAL_NOT_REAL'
SYNTHETIC_EMAIL='synthetic-private@example.invalid'


def work(number=1,**changes):
    return dict(id=f'https://openalex.org/W{number}',title=f'Synthetic Work {number}',publication_year=2024,publication_date='2024-01-02',type='article',doi=f'https://doi.org/10.1234/test{number}',
        authorships=[dict(author=dict(id='https://openalex.org/A1',display_name='First Synthetic',orcid='https://orcid.org/0000-0002-1825-0097')),dict(author=dict(id='https://openalex.org/A2',display_name='Second Synthetic'))],
        primary_location=dict(source=dict(id='https://openalex.org/S1',display_name='Synthetic Journal',type='journal',issn_l='1234-5679',issn=['1234-5679'],is_in_scopus=True,listed_in=['scopus','SCIE'])),
        cited_by_count=12,is_retracted=False,referenced_works=['https://openalex.org/W999'],open_access=dict(is_oa=True,oa_url='https://example.invalid/article'))|changes


def page(works=None,next_cursor=None,**meta): return dict(meta=dict(count=10,next_cursor=next_cursor,**meta),results=[work()] if works is None else works)


class Response:
    def __init__(self,data=None,*,status=200,body=None,headers=None,error=None):
        self.status_code=status; self.body=body if body is not None else json.dumps(data if data is not None else page()).encode(); self.headers=headers or {}; self.error=error; self.closed=False
    def iter_content(self,chunk_size):
        if self.error: raise self.error
        yield self.body
    def close(self): self.closed=True


class Session:
    def __init__(self,responses=()): self.responses=list(responses); self.calls=[]; self.closed=False
    def get(self,url,**kwargs):
        self.calls.append(dict(url=url,**kwargs))
        if not self.responses: raise AssertionError('Unexpected extra OpenAlex request')
        response=self.responses.pop(0)
        if isinstance(response,BaseException): raise response
        return response
    def close(self): self.closed=True


class AcquisitionCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.db=Database(self.root/'research'); self.store=OpenAlexAcquisitionStore(self.db)
        self.session=Session(); self.sleeps=[]; self.client=OpenAlexClient(SYNTHETIC_KEY,SYNTHETIC_EMAIL,base_url='https://api.openalex.org/works',session=self.session,max_attempts=3,sleeper=self.sleeps.append)
    def tearDown(self): self.client.close(); self.db.close(); self.temp.cleanup()
    def acquire(self,responses=None,spec=None):
        if responses is not None: self.session.responses=list(responses)
        return self.store.acquire(spec or dict(search='literal test',max_records=3,page_size=2),self.client)
