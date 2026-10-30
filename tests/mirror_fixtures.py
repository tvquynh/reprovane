"""Synthetic HTTP responses; no sockets, credentials, Git or real upstream dataset."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from urllib.parse import unquote
from venue_fixtures import build_package
from reprovane.mirror import GitHubClient,MirrorCache,sync_scimago_from_github

REPO='tvquynh/wosaide-journal-lists'
A='a'*40
B='b'*40
C='c'*40


class Response:
    def __init__(self,body=b'',status=200,headers=None,error=None):
        self.body=body; self.status_code=status; self.headers=headers or {}; self.error=error; self.closed=False
    def iter_content(self,chunk_size):
        if self.error: yield self.body[:2]; raise self.error
        for pos in range(0,len(self.body),chunk_size): yield self.body[pos:pos+chunk_size]
    def close(self): self.closed=True


class Session:
    def __init__(self): self.calls=[]; self.routes={}; self.current=A; self.files={}; self.closed=False; self.responses=[]
    def get(self,url,**kwargs):
        self.calls.append(dict(url=url,**kwargs))
        if url in self.routes:
            sequence=self.routes[url]; result=sequence.pop(0) if isinstance(sequence,list) else sequence
            if isinstance(result,BaseException): raise result
            self.responses.append(result); return result
        if url.startswith('https://api.github.com/repos/'+REPO+'/commits/'):
            requested=unquote(url.rsplit('/',1)[1]); sha=requested if len(requested)==40 else self.current
            result=Response(json.dumps({'sha':sha}).encode())
        elif url in self.files: result=Response(self.files[url])
        else: raise AssertionError('Unexpected network target: '+url)
        self.responses.append(result); return result
    def close(self): self.closed=True


def raw(sha,path,repo=REPO): return 'https://raw.githubusercontent.com/'+repo+'/'+sha+'/'+path
def api(requested='control'): return 'https://api.github.com/repos/'+REPO+'/commits/'+requested


class SyncCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.workspace=self.root/'workspace'
        self.session=Session(); self.sleeps=[]; self.client=GitHubClient(session=self.session,sleeper=self.sleeps.append)
        self.cache=MirrorCache(self.workspace); self.seed(A)
    def tearDown(self): self.client.close(); self.temp.cleanup()
    def seed(self,sha,years=(2022,2023),version=2,apc=False,edit=None):
        root=self.root/'upstream'/sha
        rows={y:[dict(Sourceid='100',Title='Alpha Journal',**{'SJR Best Quartile':'Q1'})] for y in years}
        manifest=build_package(root,years=years,version=version,apc=apc,rows=rows)
        if edit: edit(root)
        for p in root.iterdir():
            if p.is_file(): self.session.files[raw(sha,'SCIMAGO/'+p.name)]=p.read_bytes()
        self.session.current=sha
        return root
    def sync(self,**kwargs): return sync_scimago_from_github(self.workspace,client=self.client,**kwargs)
    def data_calls(self,start=0): return [c['url'] for c in self.session.calls[start:] if '.csv' in c['url']]
    def state(self): return self.cache.read_json(self.cache.state_path(REPO,'SCIMAGO'))
    def snapshot_bytes(self,result):
        root=Path(result.snapshot_directory)
        return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
