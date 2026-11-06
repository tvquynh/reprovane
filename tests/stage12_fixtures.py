"""Deterministic synthetic integration. ID/time injection is fixture-only, never product behavior."""
import contextlib,hashlib,json,shutil,sys,tempfile
from pathlib import Path
from unittest.mock import patch
from fulltext_acquisition_fixtures import FullCase,policy,location,Response,PDF2
from openalex_acquisition_fixtures import work,Session
from reprovane.core.db import Database
from reprovane.core.foundation import Foundation
from reprovane.legacy_migration import audit,plan,execute,LegacyMigrationStore
from reprovane.screening import ScreeningStore
from reprovane.corpus_release import CorpusReleaseStore
from corpus_release_fixtures import plan as corpus_plan
from reprovane.fulltext_acquisition import FullTextStore
from reprovane.openalex import OpenAlexClient
from reprovane.frontier import FrontierStore
from reprovane.search_validation import SearchValidationStore
from reprovane.venue_resolution import VenueResolutionStore
from reprovane.operations.workspace import doctor
from reprovane.corpus_release.model import digest

LEGACY=Path(__file__).parent/'fixtures/legacy_v4_1_0'


@contextlib.contextmanager
def deterministic_context():
    counts={}
    def new_id(prefix):
        counts[prefix]=counts.get(prefix,0)+1
        return prefix+'_'+hashlib.sha256(f'stage12-synthetic:{prefix}:{counts[prefix]}'.encode()).hexdigest()[:32]
    with contextlib.ExitStack() as stack:
        for name,module in list(sys.modules.items()):
            if not name.startswith('reprovane.'):continue
            if callable(getattr(module,'new_id',None)):stack.enter_context(patch.object(module,'new_id',new_id))
            if callable(getattr(module,'utc_now',None)):stack.enter_context(patch.object(module,'utc_now',lambda:'2026-01-01T00:00:00.000000Z'))
        yield


class E2ECase(FullCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'đường dẫn có dấu'/'nested workspace'/'e2e';self.root.mkdir(parents=True)
        self.legacy=self.root/'v4 source';shutil.copytree(LEGACY,self.legacy)
        self.original=audit(self.legacy);self.planned=plan(self.legacy)
        self.migrated=execute(self.legacy,self.root/'modern workspace',self.planned)
        self.db=Database(self.root/'modern workspace');self.store=ScreeningStore(self.db);self.discovery=self.store.discovery;self.releases=CorpusReleaseStore(self.db)
        self.session=Session();self.client=OpenAlexClient('',session=self.session,max_attempts=1,sleeper=lambda _:None)
        self.project=Foundation(self.db).get_project(self.migrated['project_id']);self.revision=self.migrated['revision_id'];self.counter=0;self.rid=self.discovery.create(self.revision)['discovery_run_id']


def e2e(export_directory=None):
    with deterministic_context(),patch('socket.socket.connect',side_effect=AssertionError('Offline integration only')),patch('urllib3.connectionpool.HTTPConnectionPool.urlopen',side_effect=AssertionError('Offline integration only')):
        c=E2ECase();c.setUp()
        try:
            # Migration has no screening/corpus authority, before explicit new human workflow.
            before={t:c.db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('screening_decision','screening_outcome','corpus_release_item','fulltext_attempt')}
            assert not any(before.values())
            versions=['publishedVersion','acceptedVersion','submittedVersion','publishedVersion']
            works=[work(i+1,title='Nghiên cứu Việt Nam '+str(i+1),referenced_works=[],authorships=[dict(author=dict(id='https://openalex.org/A1',display_name='Nguyễn Văn An'))],primary_location=dict(source=dict(id='https://openalex.org/S1',type='journal',display_name='Tạp chí khoa học',issn_l='1234-5679',issn=['1234-5679'])),locations=[location(v,'repository' if v!='publishedVersion' else 'journal',f'https://papers.example.org/{i+1}.pdf')]) for i,v in enumerate(versions)]
            a=c.primary([works[0]]);b=c.primary(works)
            frontier=FrontierStore(c.db);fp=json.loads((Path(__file__).parent/'fixtures/frontier/policy.json').read_text(encoding='utf-8'));fp.update(max_rounds=1,max_depth=1,max_seeds_per_round=1)
            fid=frontier.create(c.revision,c.rid,fp)['frontier_run_id'];frontier.run_to_policy_stop(fid,c.client);frontier.finalize(fid)
            validation=SearchValidationStore(c.db);vid=validation.create(c.revision,[dict(label='A',acquisition_id=a['acquisition_id']),dict(label='B',acquisition_id=b['acquisition_id'])],sentinels=[dict(namespace='openalex_work_id',value='W1')])['validation_run_id'];validation.execute(vid);validation.finalize(vid)
            venue=VenueResolutionStore(c.db);selector=dict(profiles=[dict(normalizer_name='openalex-work',normalizer_version='1',observation_schema_version='1')]);vr=venue.create_venue_resolution_run(c.revision,selector)['venue_resolution_run_id'];records=c.store.foundation.list_provider_records(b['snapshot_id']);venue.add_venue_resolution_inputs(vr,[r['provider_record_id'] for r in records]);venue.generate_provider_nodes(vr);venue.generate_candidates(vr);venue.evaluate_candidates_automatically(vr);venue.materialize_venue_resolution_run(vr);venue.seal_venue_resolution_run(vr)
            c.release(works,include_count=3,protocol=corpus_plan(search_validation_refs=[vid]))
            c.full=FullTextStore(c.db);c.fid=c.full.create(c.crid,policy())['run_id'];handoff=c.releases.handoff(c.crid)
            c.full.discover(c.fid,c.transport());c.full.acquire(c.fid,c.transport([Response(),Response(PDF2)]));c.full.finalize(c.fid)
            all_checks=doctor(c.db.workspace,full=True);migration=LegacyMigrationStore(c.db).verify(c.migrated['migration_id'],c.legacy)
            assert audit(c.legacy)==c.original and c.releases.handoff(c.crid)==handoff
            corpus=json.loads(c.releases.get(c.crid)['release_manifest_json']);fulltext=json.loads(c.full.get(c.fid)['manifest_json'])
            assert len(handoff['items'])==3 and fulltext['artifact_count']==2 and len(fulltext['manual_queue'])==1
            assert c.db.execute('SELECT count(*) FROM venue_membership_observation').fetchone()[0]==0
            exported={}
            root=Path(export_directory) if export_directory else c.root/'exports';root.mkdir(parents=True,exist_ok=True)
            c.releases.export(c.crid,root/'corpus');c.full.export(c.fid,root/'fulltext')
            for p in sorted(root.rglob('*')):
                if p.is_file():exported[p.relative_to(root).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
            result=dict(schema='synthetic-final-e2e/1',mode='OFFLINE_MOCKED_FIXTURE_IDS_AND_CLOCK',legacy_source_fingerprint=c.original['source_fingerprint'],migration_plan_sha256=c.planned['plan_sha256'],migration_manifest_sha256=migration['manifest_sha256'],zero_modern_authority_after_legacy=before,screening_candidates=4,screen_include=3,screen_exclude=1,corpus_items=3,auto_acquired=2,manual_queue=1,artifacts=2,corpus_manifest_sha256=c.releases.get(c.crid)['release_manifest_sha256'],fulltext_manifest_sha256=c.full.get(c.fid)['manifest_sha256'],corpus_item_hashes=[c.releases._portable(i)['item_sha256'] for i in c.releases.items(c.crid)],fulltext_item_hashes=[s['status_sha256'] for s in fulltext['items']],acquisition_request_hashes=sorted(r[0] for r in c.db.execute('SELECT request_spec_sha256 FROM openalex_acquisition')),policy_hashes=dict(corpus=corpus['release_plan_sha256'],fulltext=fulltext['fulltext_policy_sha256']),all_sealed_layers=all_checks['sealed_integrity'],source_unchanged=True,fulltext_does_not_change_membership=True,provider_membership_not_invented=True,export_file_hashes=exported,no_ai=True,no_ocr=True)
            return result
        finally:c.tearDown()
