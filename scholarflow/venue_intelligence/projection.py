"""Queries use frozen selected venue evidence; membership never comes from identity alone."""
import json
from collections import Counter,defaultdict
from ..core import ValidationError
from ..core.foundation import Foundation
from ..core.provenance import canonical_json
from ..venue_resolution import VenueResolutionStore
from ..venue_resolution.policy import VenuePolicy
from .store import VenueIntelligenceStore,PROFILES


class VenueIntelligence:
    def __init__(self,venue_store): self.store=venue_store

    def query(self,run_id,entity_id):
        run=self.store.get_venue_resolution_run(run_id)
        if run['materialized_at_utc'] is None: raise ValidationError('Query requires a materialized venue run')
        members=self.store.list_entity_members(run_id,entity_id)
        if not members: raise ValidationError('Venue entity does not exist in this run')
        nodes={m['provider_node_id'] for m in members}
        ids={m['provider_record_id'] for m in self.store.list_provider_node_members(run_id) if m['provider_node_id'] in nodes}
        providers={'scopus':[],'wos':[]}
        for row in self.store.list_venue_resolution_inputs(run_id):
            if row['provider_record_id'] not in ids: continue
            evidence=json.loads(row['evidence_json']); provider=evidence['provider_namespace']
            if provider not in providers: continue
            claims=[m for m in evidence['membership_claims'] if m['membership_namespace']==provider]
            if not claims: continue  # A title/identifier by itself never invents provider membership.
            attrs=[dict(observation_id=a['observation_id'],code=a['attribute_code'],value=json.loads(a['value_json']),source_field_path=a['source_field_path']) for a in evidence.get('attribute_claims',[]) if a['attribute_namespace']==provider]
            providers[provider].append(dict(provider_record_id=row['provider_record_id'],native_id=evidence['provider_native_id'],titles=evidence['titles'],source_types=evidence['source_types'],
                membership_claims=claims,attributes=attrs,provenance=evidence.get('provider_provenance',{}),snapshot_metadata=evidence['snapshot_metadata']))
        return dict(venue_resolution_run_id=run_id,venue_entity_id=entity_id,
            scopus=dict(presence='PRESENT_IN_SELECTED_EVIDENCE' if providers['scopus'] else 'NO_SELECTED_EVIDENCE',
                statuses=sorted({m['status_code'] for r in providers['scopus'] for m in r['membership_claims']}),records=providers['scopus']),
            wos=dict(collections=sorted({m['collection_code'] for r in providers['wos'] for m in r['membership_claims'] if m['collection_code']}),records=providers['wos']))

    def resolution_statistics(self,run_id,snapshot_ids=None):
        inputs=self.store.list_venue_resolution_inputs(run_id); member_entity={m['provider_node_id']:m['venue_entity_id'] for m in self.store.list_entity_members(run_id)}
        sizes=Counter(member_entity.values()); node_records={m['provider_record_id']:m['provider_node_id'] for m in self.store.list_provider_node_members(run_id)}
        run=self.store.get_venue_resolution_run(run_id)
        decisions=json.loads(run['decision_manifest_json']) if run['decision_manifest_json'] else self.store.list_active_decisions(run_id)
        ambi_ids={d['candidate_id'] for d in decisions if d['decision_type']=='AMBIGUOUS'}
        ambiguous={c[k] for c in self.store.list_candidates(run_id) if c['candidate_id'] in ambi_ids for k in ['left_provider_node_id','right_provider_node_id']}
        result=defaultdict(Counter)
        for row in inputs:
            e=json.loads(row['evidence_json']); sid=e.get('provider_provenance',{}).get('snapshot_id')
            if snapshot_ids is not None and sid not in snapshot_ids: continue
            node=node_records[row['provider_record_id']]
            state='matched' if sizes.get(member_entity.get(node),0)>1 else ('ambiguous' if node in ambiguous else 'unresolved')
            result[e['provider_namespace']][state]+=1
        return {provider:{k:counts[k] for k in ['matched','ambiguous','unresolved']} for provider,counts in sorted(result.items())}


def resolve_imported_snapshots(database,snapshot_ids,*,max_candidate_block_size=50,extra_profiles=()):
    """Explicit diagnostic run; unchanged Stage04 conservative node/candidate policy."""
    intelligence=VenueIntelligenceStore(database); store=intelligence.venues; foundation=Foundation(database)
    snapshot_ids=sorted(set(snapshot_ids))
    if not snapshot_ids: raise ValidationError('At least one explicit snapshot is required')
    records=[]
    for sid in snapshot_ids:
        snapshot=foundation.get_provider_snapshot(sid)
        if not snapshot['sealed_at_utc']: raise ValidationError('Resolution input snapshot must be sealed')
        records.extend(r['provider_record_id'] for r in foundation.list_provider_records(sid))
    with store._transaction():
        row=database.execute("SELECT * FROM review_project WHERE project_key='venue-diagnostics'").fetchone()
        project=dict(row) if row else foundation.create_project('venue-diagnostics','Explicit venue diagnostics')
        revisions=foundation.list_project_revisions(project['project_id'])
        revision=foundation.create_project_revision(project['project_id'],len(revisions)+1,'json',canonical_json(dict(snapshot_ids=snapshot_ids)))
        run=store.create_venue_resolution_run(revision['revision_id'],dict(profiles=[*PROFILES,*extra_profiles]),policy=VenuePolicy(max_candidate_block_size))
        rid=run['venue_resolution_run_id']; store.add_venue_resolution_inputs(rid,records)
        store.generate_provider_nodes(rid); store.generate_candidates(rid); store.evaluate_candidates_automatically(rid)
        store.materialize_venue_resolution_run(rid); store.seal_venue_resolution_run(rid)
    return dict(run=store.get_venue_resolution_run(rid),statistics=VenueIntelligence(store).resolution_statistics(rid,snapshot_ids))
