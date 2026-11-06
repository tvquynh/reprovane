"""Append-once corpus snapshots from exact sealed screening. Offline only."""
import json
from pathlib import Path
from contextlib import contextmanager
from ..core import ValidationError,ArtifactIntegrityError
from ..core.ids import new_id,utc_now
from ..core.migrations import apply_migrations
from ..core.provenance import canonical_json
from ..screening import ScreeningStore
from ..screening import evidence
from ..search_validation import SearchValidationStore
from ..stage09 import stable
from ..resolution.normalization import normalize_doi
from ..providers.scimago import _transaction
from .plan import CorpusReleasePlan
from .model import digest,sha,finish_item,item_id,semantic_manifest,private_check,statistics,values
from .portable import render_files,verify_export

SNAPSHOTS=('identity','metadata','screening_provenance','discovery_provenance','oa_locations')


class CorpusReleaseStore:
    def __init__(self,database,*,forbidden_values=()):
        self.db=database; self.screening=ScreeningStore(database); self.discovery=self.screening.discovery; self.foundation=self.screening.foundation; self.validation=SearchValidationStore(database); self.forbidden_values=tuple(forbidden_values)
        if not hasattr(database,'_corpus_release_auth'): database._corpus_release_auth=None
        database.connection.create_function('corpus_release_authorized',2,lambda rid,phase:int(database._corpus_release_auth==(rid,phase)))
        apply_migrations(database.connection,target_version=11)
    @contextmanager
    def auth(self,rid,phase):
        old=self.db._corpus_release_auth; self.db._corpus_release_auth=(rid,phase)
        try: yield
        finally: self.db._corpus_release_auth=old
    def get(self,rid): return self.foundation._one('corpus_release','release_id',rid)
    def items(self,rid): return [dict(r) for r in self.db.execute('SELECT * FROM corpus_release_item WHERE release_id=? ORDER BY ordinal',(rid,))]
    def _export_row(self,rid): return self.foundation._one('corpus_release_export','release_id',rid)
    def _source(self,revision,sid,plan):
        run=self.screening.get(sid)
        if run['state']!='SEALED' or run['project_revision_id']!=revision: raise ValidationError('Exact same-revision SEALED screening required')
        self.screening.verify(sid); outcomes=self.screening.outcomes(sid); include=[]; exclude=[]
        for row in outcomes:
            if row['outcome'] not in ('SCREEN_INCLUDE','SCREEN_EXCLUDE'): raise ArtifactIntegrityError('Unresolved screening source')
            ref=dict(candidate_id=row['candidate_id'],publication_entity_id=row['publication_entity_id'],resolution_id=row['resolution_id'],outcome_sha256=row['outcome_sha256'],kind='SCREENING_INCLUDE_REFERENCE' if row['outcome']=='SCREEN_INCLUDE' else 'SCREENING_EXCLUSION_AUDIT')
            (include if row['outcome']=='SCREEN_INCLUDE' else exclude).append(ref)
        audits=[]
        for vid in plan['search_validation_refs']:
            v=self.validation.get(vid)
            if v['state']!='SEALED' or v['project_revision_id']!=revision: raise ValidationError('Search audit must be sealed in exact revision')
            self.validation.verify(vid); audits.append(dict(validation_run_id=vid,plan_sha256=v['plan_sha256'],finalization_sha256=v['finalization_sha256']))
        rev=self.foundation.get_project_revision(revision)
        return dict(project_id=rev['project_id'],screening_run_id=sid,screening_manifest_sha256=run['finalization_sha256'],screening_plan_sha256=run['plan_sha256'],screening_candidate_set_sha256=run['candidate_set_sha256'],screening_assignment_sha256=run['assignment_sha256'],screening_resolution_id=run['current_resolution_id'],screening_candidate_count=len(outcomes),screen_include_count=len(include),screen_exclude_count=len(exclude),screen_include_refs=include,screen_exclude_refs=exclude,unresolved_discovery_summary=json.loads(run['unresolved_discovery_json']),unresolved_discovery_sha256=run['unresolved_discovery_sha256'],discovery_run_id=run['discovery_run_id'],discovery_manifest_sha256=run['discovery_sha256'],frontier_run_id=run['frontier_run_id'],frontier_manifest_sha256=run['frontier_sha256'],search_validation_audits=audits)
    def create(self,project_revision_id,screening_run_id,plan):
        p=CorpusReleasePlan.from_value(plan)
        with _transaction(self.db):
            source=self._source(project_revision_id,screening_run_id,p.as_dict()); private_check(source,self.forbidden_values); private_check(p.as_dict(),self.forbidden_values)
            old=self.db.execute('SELECT * FROM corpus_release WHERE screening_run_id=? AND release_plan_sha256=?',(screening_run_id,p.sha256)).fetchone()
            if old:
                run=dict(old)
                if run['project_revision_id']!=project_revision_id or run['source_json']!=canonical_json(source): raise ArtifactIntegrityError('Existing release source mismatch')
                if run['state']!='OPEN': self.verify(run['release_id'])
                return self.inspect(run['release_id'])
            rid=new_id('crl')
            with self.auth(rid,'CREATE'):
                self.foundation._insert('corpus_release',dict(release_id=rid,project_revision_id=project_revision_id,screening_run_id=screening_run_id,screening_manifest_sha256=source['screening_manifest_sha256'],release_plan_json=p.canonical,release_plan_sha256=p.sha256,source_json=canonical_json(source),source_sha256=digest(source),state='OPEN',item_set_sha256=None,release_manifest_json=None,release_manifest_sha256=None,created_at_utc=utc_now(),sealed_at_utc=None))
        return self.inspect(rid)
    def _context(self,run):
        p=CorpusReleasePlan.from_value(json.loads(run['release_plan_json'])); source=self._source(run['project_revision_id'],run['screening_run_id'],p.as_dict())
        if p.sha256!=run['release_plan_sha256'] or canonical_json(source)!=run['source_json'] or digest(source)!=run['source_sha256'] or source['screening_manifest_sha256']!=run['screening_manifest_sha256']: raise ArtifactIntegrityError('Release frozen source/plan mismatch')
        return p.as_dict(),source
    def _identity_and_refs(self,candidate,fields):
        resolution=candidate['discovery_resolution_run_id']; entity=candidate['publication_entity_id']; resolver=self.discovery.resolver
        inputs={r['provider_record_id']:set(json.loads(r['evidence_json'])['observation_ids_used']) for r in resolver.list_resolution_inputs(resolution)}
        frozen={r['provider_record_id']:r['observations'] for r in json.loads(candidate['evidence_refs_json'])}; members=[]; refs=[]; doi=set(); openalex=set(); identifiers=[]
        for member in resolver.list_entity_members(resolution,entity):
            rid=member['provider_record_id']; record=self.foundation._one('provider_record','provider_record_id',rid); provider=self.db.execute('SELECT p.semantic_namespace FROM provider p JOIN provider_snapshot s ON s.provider_id=p.provider_id WHERE s.snapshot_id=?',(record['snapshot_id'],)).fetchone()[0]
            claims=[stable(dict(r)) for r in self.db.execute('SELECT * FROM publication_identifier_observation WHERE provider_record_id=? ORDER BY observation_id',(rid,)) if r['observation_id'] in inputs[rid]]
            valid=sorted({v for claim in claims if claim['identifier_namespace']=='doi' for v in [normalize_doi(claim['identifier_value_normalized'])] if v}); doi.update(valid)
            native=record['provider_native_id'] if provider=='openalex' else None
            if native: openalex.add(native)
            identifiers.extend(dict(provider_record_id=rid,namespace=c['identifier_namespace'],original=c['identifier_value_original'],value=c['identifier_value_normalized'],observation_id=c['observation_id']) for c in claims)
            members.append(dict(resolution_run_id=resolution,publication_entity_id=entity,provider_record_id=rid,provider_namespace=provider,provider_native_id=record['provider_native_id'],snapshot_id=record['snapshot_id'],artifact_sha256=record['artifact_sha256'],record_locator=record['record_locator'],membership_basis=json.loads(member['membership_basis_json']),identifier_observations=claims,normalized_dois=valid))
            tables={}
            wanted=[]
            if set(fields)&set(evidence.METADATA): wanted.append('publication_metadata_observation')
            if set(fields)&{'doi','provider_identifiers'}: wanted.append('publication_identifier_observation')
            if 'authors' in fields: wanted.append('publication_authorship_observation')
            for table in wanted: tables[table]=[r[0] for r in self.db.execute('SELECT observation_id FROM '+table+' WHERE provider_record_id=? ORDER BY observation_id',(rid,)) if r[0] in inputs[rid]]
            for field,table in [('venue','venue_observation'),('integrity','integrity_event_observation')]:
                if field in fields: tables[table]=frozen.get(rid,{}).get(table,[])
            refs.append(dict(provider_record_id=rid,observations=tables))
        identity=dict(publication_entity_id=entity,resolution_run_id=resolution,members=members,normalized_dois=sorted(doi),openalex_work_ids=sorted(openalex),provider_identifiers=identifiers)
        return identity,refs
    def _expected_items(self,run,plan,source):
        candidates={c['candidate_id']:c for c in self.screening.candidates(run['screening_run_id'])}; outcomes={r['candidate_id']:r for r in self.screening.outcomes(run['screening_run_id'])}; bodies=[]
        for ref in source['screen_include_refs']:
            candidate=candidates[ref['candidate_id']]; outcome=outcomes[ref['candidate_id']]; identity,refs=self._identity_and_refs(candidate,plan['metadata_fields']); metadata=evidence.project(self.screening,refs,plan['metadata_fields']); ids=json.loads(outcome['decision_ids_json'])
            s=dict(screening_run_id=run['screening_run_id'],screening_manifest_sha256=run['screening_manifest_sha256'],candidate_id=candidate['candidate_id'],resolution_id=outcome['resolution_id'],outcome=outcome['outcome'],outcome_sha256=outcome['outcome_sha256'],resolution_basis=outcome['resolution_basis'],effective_decision_ids=ids,effective_adjudication_id=outcome['adjudication_id'],reason_codes=json.loads(outcome['reason_codes_json']))
            if plan['provenance_policy']=='REFERENCES_WITH_ACTOR_LABELS':
                s['reviewers']=[dict(decision_id=r['decision_id'],reviewer_label=r['reviewer_label']) for r in self.screening.decisions(run['screening_run_id']) if r['decision_id'] in ids]
                s['adjudicator']=[dict(adjudication_id=r['adjudication_id'],adjudicator_label=r['adjudicator_label']) for r in self.screening.adjudications(run['screening_run_id']) if r['adjudication_id']==outcome['adjudication_id']]
            discovery=dict(discovery_run_id=source['discovery_run_id'],discovery_manifest_sha256=source['discovery_manifest_sha256'],resolution_run_id=candidate['discovery_resolution_run_id'],publication_entity_id=candidate['publication_entity_id'],membership_basis=json.loads(candidate['membership_basis_json']))
            oa=None
            if plan['oa_policy']=='FROZEN_RAW_WORK_LOCATIONS':
                records=[]
                for member in identity['members']:
                    info=self.discovery._work(member['provider_record_id']); raw=info['raw']; records.append(dict(provider_record_id=member['provider_record_id'],raw_anchor=info['locator'],fields={key:raw[key] for key in ['open_access','primary_location','locations','best_oa_location'] if key in raw}))
                oa=dict(records=records)
            body=dict(corpus_item_id=item_id(run['release_id'],candidate['publication_entity_id']),release_id=run['release_id'],publication_entity_id=candidate['publication_entity_id'],screening_candidate_id=candidate['candidate_id'],screening_resolution_id=outcome['resolution_id'],resolution_run_id=candidate['discovery_resolution_run_id'],identity=identity,metadata=metadata,screening_provenance=s,discovery_provenance=discovery,oa_locations=oa)
            bodies.append((body,refs))
        key='publication_entity_id' if plan['ordering_policy']=='PUBLICATION_ENTITY_ID' else 'corpus_item_id'; bodies.sort(key=lambda pair:pair[0][key]); result=[]
        for index,(body,refs) in enumerate(bodies):
            item=finish_item(dict(**body,ordinal=index)); private_check(item,self.forbidden_values); result.append((item,refs))
        return result
    @staticmethod
    def _row(item,refs):
        row={k:v for k,v in item.items() if k not in SNAPSHOTS}; row['projection_refs_json']=canonical_json(refs)
        for field in SNAPSHOTS: row[field+'_json']=canonical_json(item[field])
        return row
    @staticmethod
    def _portable(row):
        item={k:v for k,v in row.items() if k!='projection_refs_json' and not k.endswith('_json')}
        for field in SNAPSHOTS: item[field]=json.loads(row[field+'_json'])
        return item
    def materialize(self,rid):
        run=self.get(rid)
        if run['state']!='OPEN': self.verify(rid); return self.inspect(rid)
        with _transaction(self.db),self.auth(rid,'MATERIALIZE'):
            plan,source=self._context(run); expected=self._expected_items(run,plan,source); items=[i for i,_ in expected]
            for item,refs in expected: self.foundation._insert('corpus_release_item',self._row(item,refs))
            manifest=semantic_manifest(rid,run['project_revision_id'],run['screening_run_id'],run['screening_manifest_sha256'],plan,source,items); private_check(manifest,self.forbidden_values); files,p=render_files(manifest,items)
            self.foundation._insert('corpus_release_export',dict(release_id=rid,export_schema='corpus-portable/1',file_inventory_json=canonical_json(p['file_inventory']),manifest_json=files['manifest.json'].decode('utf-8'),export_manifest_sha256=sha(files['manifest.json'])))
            self.db.execute("UPDATE corpus_release SET state='MATERIALIZED',item_set_sha256=?,release_manifest_json=?,release_manifest_sha256=? WHERE release_id=?",(digest(items),canonical_json(manifest),digest(manifest),rid))
        return self.inspect(rid)
    def verify(self,rid):
        run=self.get(rid)
        if run['state'] not in ('MATERIALIZED','SEALED'): raise ValidationError('Corpus release not materialized')
        plan,source=self._context(run); expected=self._expected_items(run,plan,source); rows=[self._row(item,refs) for item,refs in expected]
        if rows!=self.items(rid): raise ArtifactIntegrityError('Corpus membership/snapshot mismatch')
        items=[i for i,_ in expected]; manifest=semantic_manifest(rid,run['project_revision_id'],run['screening_run_id'],run['screening_manifest_sha256'],plan,source,items)
        if digest(items)!=run['item_set_sha256'] or canonical_json(manifest)!=run['release_manifest_json'] or digest(manifest)!=run['release_manifest_sha256']: raise ArtifactIntegrityError('Corpus release manifest mismatch')
        files,p=render_files(manifest,items); exported=self._export_row(rid)
        if exported['manifest_json']!=files['manifest.json'].decode('utf-8') or exported['export_manifest_sha256']!=sha(files['manifest.json']) or exported['file_inventory_json']!=canonical_json(p['file_inventory']): raise ArtifactIntegrityError('Corpus export manifest mismatch')
        return True
    def finalize(self,rid):
        if self.get(rid)['state']=='SEALED': self.verify(rid); return self.inspect(rid)
        with _transaction(self.db):
            if self.get(rid)['state']=='OPEN': self.materialize(rid)
            self.verify(rid)
            with self.auth(rid,'SEAL'): self.db.execute("UPDATE corpus_release SET state='SEALED',sealed_at_utc=? WHERE release_id=?",(utc_now(),rid))
        return self.inspect(rid)
    def export(self,rid,directory):
        if Path(directory).is_symlink(): raise ValidationError('Export symlink directory refused')
        run=self.get(rid)
        if run['state']!='SEALED': raise ValidationError('Portable export requires sealed release')
        self.verify(rid); items=[self._portable(r) for r in self.items(rid)]; files,_=render_files(json.loads(run['release_manifest_json']),items); root=Path(directory)
        if root.exists():
            if not root.is_dir() or any(p.name not in files or p.is_symlink() or not p.is_file() or p.read_bytes()!=files[p.name] for p in root.iterdir()): raise ValidationError('Export refuses differing existing bytes or extra files')
        else: root.mkdir(parents=True)
        for name,data in files.items():
            path=root/name
            if not path.exists():
                with path.open('xb') as stream: stream.write(data)
        return verify_export(root,expected_manifest_sha256=run['release_manifest_sha256'])
    def handoff(self,rid):
        run=self.get(rid)
        if run['state']!='SEALED': raise ValidationError('Read-only full-text handoff requires SEALED corpus release')
        self.verify(rid); result=[]
        for row in self.items(rid):
            item=self._portable(row); result.append(dict(corpus_item_id=item['corpus_item_id'],publication_entity_id=item['publication_entity_id'],release_id=rid,release_manifest_sha256=run['release_manifest_sha256'],dois=item['identity']['normalized_dois'],openalex_work_ids=item['identity']['openalex_work_ids'],provider_identifiers=item['identity']['provider_identifiers'],known_oa_locations=item['oa_locations'],titles=values(item,'title'),venues=values(item,'venue'),item_sha256=item['item_sha256']))
        return dict(release_id=rid,release_manifest_sha256=run['release_manifest_sha256'],items=result,interpretation='Read-only sealed member identifiers and existing locations only; no acquisition performed.')
    def inspect(self,rid):
        run=self.get(rid); items=[self._portable(r) for r in self.items(rid)]
        return dict(release=run,statistics=statistics(json.loads(run['source_json']),items),membership_authoritative=run['state']=='SEALED',item_ids=[i['corpus_item_id'] for i in items],interpretation='Membership is authoritative for this release only after seal. Diagnostics never change screening-derived membership.')
