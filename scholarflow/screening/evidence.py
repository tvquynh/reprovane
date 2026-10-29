"""Read only exact frozen typed claims. No metadata enrichment or identity inference."""
import json
from ..core import ArtifactIntegrityError
from ..stage09 import stable

METADATA={'title':'title','abstract':'abstract_text','publication_year':'publication_year','publication_date':'publication_date','work_type':'publication_type'}


def freeze_refs(store,discovery,entity,fields):
    resolution=store.discovery.resolver.get_resolution_run(discovery['resolution_run_id']); profiles={tuple(p[k] for k in ['normalizer_name','normalizer_version','observation_schema_version']) for p in json.loads(resolution['observation_selector_json'])['profiles']}
    inputs={r['provider_record_id']:set(json.loads(r['evidence_json'])['observation_ids_used']) for r in store.discovery.resolver.list_resolution_inputs(discovery['resolution_run_id'])}
    tables=set()
    if set(fields)&set(METADATA): tables.add('publication_metadata_observation')
    if set(fields)&{'doi','provider_identifiers'}: tables.add('publication_identifier_observation')
    if 'authors' in fields: tables.add('publication_authorship_observation')
    if 'venue' in fields: tables.add('venue_observation')
    if 'integrity' in fields: tables.add('integrity_event_observation')
    refs=[]
    for m in store.discovery.resolver.list_entity_members(discovery['resolution_run_id'],entity):
        rid=m['provider_record_id']; observations={}
        for table in sorted(tables):
            rows=[dict(r) for r in store.db.execute('SELECT * FROM '+table+' WHERE provider_record_id=? ORDER BY observation_id',(rid,))]
            observations[table]=[r['observation_id'] for r in rows if tuple(r[k] for k in ['normalizer_name','normalizer_version','observation_schema_version']) in profiles and (table in ('venue_observation','integrity_event_observation') or r['observation_id'] in inputs[rid])]
        refs.append(dict(provider_record_id=rid,observations=observations))
    return sorted(refs,key=lambda r:r['provider_record_id'])


def project(store,refs,fields):
    evidence=[]
    for ref in refs:
        record=store.foundation._one('provider_record','provider_record_id',ref['provider_record_id']); provider=store.db.execute('SELECT p.semantic_namespace FROM provider p JOIN provider_snapshot s ON s.provider_id=p.provider_id WHERE s.snapshot_id=?',(record['snapshot_id'],)).fetchone()[0]
        rows={}
        for table,ids in ref['observations'].items():
            if table not in {'publication_metadata_observation','publication_identifier_observation','publication_authorship_observation','venue_observation','integrity_event_observation'}: raise ArtifactIntegrityError('Invalid frozen evidence table')
            values=[store.foundation._one(table,'observation_id',oid) for oid in ids]
            if any(v['provider_record_id']!=record['provider_record_id'] for v in values): raise ArtifactIntegrityError('Evidence anchor mismatch')
            rows[table]=values
        output={}
        for field in fields:
            claims=[]
            if field in METADATA:
                column=METADATA[field]; claims=[dict(observation_id=r['observation_id'],value=r[column]) for r in rows.get('publication_metadata_observation',[]) if r[column] is not None]
            elif field in ('doi','provider_identifiers'):
                claims=[dict(observation_id=r['observation_id'],namespace=r['identifier_namespace'],value=r['identifier_value_normalized'],original=r['identifier_value_original']) for r in rows.get('publication_identifier_observation',[]) if (r['identifier_namespace']=='doi')==(field=='doi')]
            else:
                table={'authors':'publication_authorship_observation','venue':'venue_observation','integrity':'integrity_event_observation'}[field]
                common={'provider_record_id','normalizer_name','normalizer_version','observation_schema_version','normalization_config_sha256','normalized_at_utc'}
                claims=[{k:v for k,v in stable(r).items() if k not in common} for r in rows.get(table,[])]
                if field=='authors': claims.sort(key=lambda r:(r['author_position'],r['observation_id']))
            output[field]=claims or None
        evidence.append(dict(provider_record_id=record['provider_record_id'],source=dict(provider=provider,provider_native_id=record['provider_native_id'],snapshot_id=record['snapshot_id'],artifact_sha256=record['artifact_sha256'],record_locator=record['record_locator']),fields=output))
    return dict(records=evidence)
