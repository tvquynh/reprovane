import csv,json,hashlib,tempfile,unittest
from pathlib import Path
from scholarflow.core.db import Database
from scholarflow.core.foundation import Foundation
from scholarflow.observations import ObservationStore,NormalizationProvenance
from scholarflow.venue_resolution import VenueResolutionStore
from scholarflow.providers.scimago import NORMALIZER

HEADER=['Rank','Sourceid','Title','Type','Issn','Publisher','Open Access','Open Access Diamond','SJR','SJR Best Quartile','H index','Country','Region','Coverage','Categories','Areas']
PROFILE={'normalizer_name':'venue-fixture','normalizer_version':'1','observation_schema_version':'1'}
SCIMAGO_PROFILE={'normalizer_name':NORMALIZER.normalizer_name,'normalizer_version':'1','observation_schema_version':'1'}
SELECTOR={'profiles':[PROFILE,SCIMAGO_PROFILE]}


def issn(first='1234567'):
    digit=(-sum(int(c)*w for c,w in zip(first,range(8,1,-1))))%11
    return first+('X' if digit==10 else str(digit))


def checksums(root):
    files=sorted(p for p in root.glob('*.csv'))
    (root/'SHA256SUMS.txt').write_text('\n'.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name for p in files)+'\n',encoding='utf-8')


def build_package(root,years=(2022,2023),version=2,apc=False,rows=None):
    root.mkdir(parents=True,exist_ok=True)
    for y in years:
        data=rows.get(y,[]) if rows is not None else [dict(Sourceid='100',Title='Alpha Journal',Issn=issn(),**{'SJR Best Quartile':'Q1' if y==max(years) else 'Q2'})]
        with (root/f'scimagojr-{y}.csv').open('w',encoding='utf-8-sig',newline='') as h:
            writer=csv.DictWriter(h,fieldnames=HEADER,delimiter=';'); writer.writeheader()
            for raw in data:
                row=dict.fromkeys(HEADER,''); row.update({'Rank':'1','Type':'journal','Publisher':'Synthetic Publisher','Open Access':'Yes','Open Access Diamond':'No','SJR':'1,25','SJR Best Quartile':'Q1','H index':'12','Country':'VN','Region':'Synthetic region','Coverage':str(y),'Categories':'Software (Q1); Information Systems (Q2); Unranked category','Areas':'Computer Science'}); row.update(raw); writer.writerow(row)
    manifest={'schema_version':version,'base_url':'https://historical.example.test/old/','sjr':{'latest_snapshot_year':max(years),'delimiter':';','encoding':'utf-8','primary_source_key':'Sourceid'},'checksums':{'algorithm':'sha256','file':'SHA256SUMS.txt','verification_required':True}}
    if version==1: manifest['sjr'].update(first_year=min(years),last_year=max(years),url_pattern='scimagojr-{year}.csv')
    else: manifest['sjr'].update(available_years=list(years),file_pattern='scimagojr-{year}.csv')
    manifest['journal_value']={'snapshots':[]} if version==2 else {}
    if apc:
        y=min(years); name=f'journal-value-{y}.csv'
        (root/name).write_text(f'Rank;Sourceid;Title;Type;Issn;Publisher;Open Access;Open Access Diamond;SJR;SJR Best Quartile;Total Docs. ({y});Estimated APC (USD);Estimated value (USD)\n1;100;Alpha Journal;journal;{issn()};Yes;No;Synthetic Publisher;1,25;Q1;10;1500;2000\n',encoding='utf-8')
        manifest['journal_value']={'year':y,'file':name} if version==1 else {'snapshots':[{'year':y,'file':name}],'apc_is_estimate':True}
    (root/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8'); checksums(root)
    return manifest


class VenueCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.db=Database(self.root/'workspace')
        self.f=Foundation(self.db); self.obs=ObservationStore(self.db); self.store=VenueResolutionStore(self.db)
        p=self.f.create_project('synthetic','Synthetic'); self.revision=self.f.create_project_revision(p['project_id'],1,'yaml','topic: synthetic')
        self.norm=NormalizationProvenance('venue-fixture','1','1'); self.providers={}; self.counter=0

    def tearDown(self): self.db.close(); self.temp.cleanup()

    def record(self,title='Alpha Journal',*,provider=None,native=None,identifier=None,namespace='issn',sealed=True):
        self.counter+=1; provider=provider or 'provider_'+str(self.counter)
        if provider not in self.providers: self.providers[provider]=self.f.register_provider(provider,'Synthetic')
        s=self.f.create_provider_snapshot(self.providers[provider]['provider_id'],'synthetic','MANUAL_IMPORT',parser_name='fixture',parser_version='1',schema_profile='opaque',schema_version='1')
        a=self.f.artifacts.put_bytes(b'{"synthetic":"venue"}'); self.f.register_raw_artifact(a); self.f.attach_artifact_to_snapshot(s['snapshot_id'],a.artifact_sha256,'file',0)
        r=self.f.add_provider_record(s['snapshot_id'],a.artifact_sha256,'/',provider_native_id=native)
        if sealed:
            self.f.seal_provider_snapshot(s['snapshot_id']); self.obs.add_venue_observation(r['provider_record_id'],self.norm,title=title)
            if identifier is not None: self.obs.add_venue_identifier_observation(r['provider_record_id'],self.norm,identifier_namespace=namespace,identifier_value_original=identifier,identifier_value_normalized=identifier)
        return r['provider_record_id']

    def run_for(self,records=(),policy=None):
        r=self.store.create_venue_resolution_run(self.revision['revision_id'],SELECTOR,policy=policy); rid=r['venue_resolution_run_id']
        if records: self.store.add_venue_resolution_inputs(rid,list(records))
        return rid

    def ready(self,records,materialize=False):
        rid=self.run_for(records); self.store.generate_provider_nodes(rid); self.store.generate_candidates(rid); self.store.evaluate_candidates_automatically(rid)
        if materialize: self.store.materialize_venue_resolution_run(rid)
        return rid

    def raw_insert(self,table,row,replace=False,connection=None):
        return (connection or self.db.connection).execute(('INSERT OR REPLACE' if replace else 'INSERT')+' INTO '+table+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
