"""Keyless, synthetic-only full-text validation fixtures."""
import copy,json
from pathlib import Path
from corpus_release_fixtures import CorpusCase
from openalex_acquisition_fixtures import work
from reprovane.fulltext_acquisition import FullTextStore,DEFAULT_POLICY
from reprovane.fulltext_acquisition.transport import SafeTransport

FIX=Path(__file__).parent/'fixtures/fulltext_acquisition'
PDF=(FIX/'tiny1.pdf').read_bytes()
PDF2=(FIX/'tiny2.pdf').read_bytes()


def policy(**kw):
    p=copy.deepcopy(DEFAULT_POLICY); p['provider_sources_enabled']=['FROZEN']; p.update(kw); return p


def location(version='publishedVersion',host='journal',url='https://papers.example.org/a.pdf',**kw):
    return dict(is_oa=True,pdf_url=url,landing_page_url=None,version=version,license='cc-by',source=dict(type=host,display_name='Synthetic host'))|kw


def public_dns(*args,**kw): return [(2,1,6,'',('93.184.216.34',443))]


class Response:
    def __init__(self,body=PDF,status=200,headers=None): self.status=status; self.body=body; self.headers=headers or {'Content-Type':'application/pdf','Content-Length':str(len(body))}; self.closed=False
    def stream(self,amt,decode_content=False):
        for i in range(0,len(self.body),amt): yield self.body[i:i+amt]
    def close(self): self.closed=True


class Opener:
    def __init__(self,responses=()): self.responses=list(responses); self.calls=[]
    def __call__(self,url,address,headers,timeout):
        self.calls.append(dict(url=url,address=address,headers=headers,timeout=timeout))
        if not self.responses: raise AssertionError('Unexpected full-text network request')
        r=self.responses.pop(0)
        if isinstance(r,BaseException): raise r
        return r


class FullCase(CorpusCase):
    def setup_run(self,versions=('publishedVersion','acceptedVersion','submittedVersion'),protocol=None,locations=None):
        works=[work(i+1,locations=[location(v,'repository' if v!='publishedVersion' else 'journal',f'https://papers.example.org/{i+1}.pdf')] if locations is None else locations[i]) for i,v in enumerate(versions)]
        self.release(works,include_count=len(works)); self.full=FullTextStore(self.db)
        self.fid=self.full.create(self.crid,protocol or policy())['run_id']; return self.fid
    def transport(self,responses=(),credentials=None):
        self.opener=Opener(responses); self.sleeps=[]
        return SafeTransport(self.full.policy(self.fid),credentials=credentials,resolver=public_dns,opener=self.opener,sleeper=self.sleeps.append)
    def discovered(self,**kw): self.setup_run(**kw); self.full.discover(self.fid,self.transport()); return self.fid
    def completed(self,**kw):
        self.discovered(**kw); self.full.acquire(self.fid,self.transport([Response(),Response(PDF2)])); self.full.finalize(self.fid)
        return json.loads(self.full.get(self.fid)['manifest_json'])
