"""Derived SCImago views over frozen run evidence, not independent truth tables."""
import json
from .normalization import title_normalized
from ..core import ValidationError


class ScimagoVenueProjection:
    def __init__(self,store): self.store=store

    def _records(self,run_id,entity_id=None):
        inputs={r['provider_record_id']:json.loads(r['evidence_json']) for r in self.store.list_venue_resolution_inputs(run_id)}
        if entity_id is None: return list(inputs.values())
        nodes={r['provider_node_id'] for r in self.store.list_entity_members(run_id,entity_id)}
        return [inputs[r['provider_record_id']] for r in self.store.list_provider_node_members(run_id) if r['provider_node_id'] in nodes]

    def global_latest_snapshot_year(self,run_id):
        years=[r['snapshot_metadata'].get('manifest_latest_year',int(r['snapshot_release'])) for r in self._records(run_id)
            if r['provider_namespace']=='scimago' and r['snapshot_dataset']=='source_list']
        return max(years) if years else None

    def scimago_years_present(self,run_id,entity_id):
        return sorted({int(r['snapshot_release']) for r in self._records(run_id,entity_id)
            if r['provider_namespace']=='scimago' and r['snapshot_dataset']=='source_list'})

    def scimago_presence_status(self,run_id,entity_id):
        years=self.scimago_years_present(run_id,entity_id); latest=self.global_latest_snapshot_year(run_id)
        if latest in years: return 'SCIMAGO_PRESENT_LATEST'
        if years: return 'SCIMAGO_HISTORICAL_ONLY'
        return 'SCIMAGO_ABSENT'

    def scimago_metrics_for_year(self,run_id,entity_id,year):
        if type(year) is not int: raise ValidationError('Metric year must be an integer')
        return sorted([m for r in self._records(run_id,entity_id) if r['provider_namespace']=='scimago'
            and r['snapshot_dataset']=='source_list' and int(r['snapshot_release'])==year
            for m in r['metric_claims'] if m['metric_namespace']=='scimago' and m['period_year']==year],
            key=lambda m:(m['metric_code'],m['category_name'] or '',m['observation_id']))

    def scimago_metrics_latest_snapshot(self,run_id,entity_id):
        year=self.global_latest_snapshot_year(run_id)
        return self.scimago_metrics_for_year(run_id,entity_id,year) if year is not None else []

    def scimago_resolution_status(self,run_id,provider_node_id):
        memberships=self.store.list_entity_members(run_id)
        entity=next((m['venue_entity_id'] for m in memberships if m['provider_node_id']==provider_node_id),None)
        if entity and self.scimago_years_present(run_id,entity): return 'SCIMAGO_MATCHED'
        nodes={n['provider_node_id']:n for n in self.store.list_provider_nodes(run_id)}
        decisions={d['candidate_id']:d for d in self.store.list_active_decisions(run_id)}
        run=self.store.get_venue_resolution_run(run_id)
        if run['decision_manifest_json']:
            decisions={d['candidate_id']:d for d in json.loads(run['decision_manifest_json'])}
        for c in self.store.list_candidates(run_id):
            if provider_node_id not in (c['left_provider_node_id'],c['right_provider_node_id']): continue
            other=c['right_provider_node_id'] if c['left_provider_node_id']==provider_node_id else c['left_provider_node_id']
            d=decisions.get(c['candidate_id'])
            if nodes[other]['provider_namespace']=='scimago' and (d is None or d['decision_type']=='AMBIGUOUS'):
                return 'SCIMAGO_AMBIGUOUS'
        return 'SCIMAGO_UNMATCHED'

    def category_quartile_exact(self,run_id,entity_id,year,requested):
        if isinstance(requested,str): requested=[requested]
        keys={title_normalized(c) for c in requested}
        matches=[m for m in self.scimago_metrics_for_year(run_id,entity_id,year) if m['metric_code']=='QUARTILE'
                 and title_normalized(m['category_name'] or '') in keys and m['value_text'] in ('Q1','Q2','Q3','Q4')]
        return dict(matches=matches,best_quartile=min((m['value_text'] for m in matches),default=None))

    scimago_category_quartile_for_year=category_quartile_exact

    @staticmethod
    def legacy_status(presence,resolution_status=None):
        return {'SCIMAGO_PRESENT_LATEST':'ACTIVE_LATEST','SCIMAGO_HISTORICAL_ONLY':'HISTORICAL_ONLY',
                'SCIMAGO_AMBIGUOUS':'AMBIGUOUS','SCIMAGO_UNMATCHED':'UNMATCHED'}.get(resolution_status if presence=='SCIMAGO_ABSENT' else presence,'UNMATCHED')
