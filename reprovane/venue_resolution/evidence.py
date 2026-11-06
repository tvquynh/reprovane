import json
from ..core import ValidationError
from .normalization import normalize_issn,title_exact,title_normalized,title_status_normalized,VERSIONS


def extract_evidence(db,record_id,selector):
    row=db.execute('SELECT r.provider_record_id,r.provider_native_id,r.snapshot_id,r.artifact_sha256,r.record_locator,r.metadata_json AS record_metadata_json,s.source_locator,s.mirror_commit_sha,p.semantic_namespace,s.dataset,s.upstream_release,'
        's.retrieved_at_utc,s.metadata_json,s.sealed_at_utc FROM provider_record r JOIN provider_snapshot s ON s.snapshot_id=r.snapshot_id '
        'JOIN provider p ON p.provider_id=s.provider_id WHERE r.provider_record_id=?',(record_id,)).fetchone()
    if row is None or row['sealed_at_utc'] is None: raise ValidationError('Venue input requires sealed provider evidence')
    def selected(table):
        return [dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE provider_record_id=? ORDER BY observation_id',(record_id,))
            if (r['normalizer_name'],r['normalizer_version'],r['observation_schema_version']) in selector.profiles]
    descriptions=selected('venue_observation'); identifiers=selected('venue_identifier_observation')
    memberships=selected('venue_membership_observation'); metrics=selected('venue_metric_observation')
    has_attributes=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='venue_provider_attribute_observation'").fetchone() is not None
    attributes=selected('venue_provider_attribute_observation') if has_attributes else []
    titles=sorted({r['title'] for r in descriptions if r['title']}); sets={'issn':set(),'eissn':set(),'issn_l':set()}; invalid=[]; claims=[]
    for r in identifiers:
        namespace=r['identifier_namespace']
        if namespace not in sets: continue
        value=normalize_issn(r['identifier_value_normalized'])
        claims.append(dict(namespace=namespace,original=r['identifier_value_original'],normalized=value,observation_id=r['observation_id']))
        if value: sets[namespace].add(value)
        else: invalid.append(r['observation_id'])
    native=row['provider_native_id']; native=native if native and native.strip() else None
    snapshot_metadata=json.loads(row['metadata_json'])
    if has_attributes and 'stage06_import_key' in snapshot_metadata:
        # Import-wide diagnostics are reproducible from the immutable snapshot;
        # they are not venue evidence and need not be copied into every input.
        snapshot_metadata={k:v for k,v in snapshot_metadata.items() if k!='statistics'}
    evidence=dict(record_id=record_id,provider_namespace=row['semantic_namespace'],provider_native_id=native,
        titles=titles,title_exact_keys=sorted({title_exact(t) for t in titles if title_exact(t)}),
        title_normalized_keys=sorted({title_normalized(t) for t in titles if title_normalized(t)}),
        title_status_normalized_keys=sorted({title_status_normalized(t) for t in titles if title_status_normalized(t)}),
        issn=sorted(sets['issn']),eissn=sorted(sets['eissn']),issn_l=sorted(sets['issn_l']),issn_claims=claims,
        valid_issn_family_union=sorted(set.union(*sets.values())),invalid_issn_observation_ids=sorted(invalid),
        source_types=sorted({r['source_type'] for r in descriptions if r['source_type']}),
        publisher_names=sorted({r['publisher_name'] for r in descriptions if r['publisher_name']}),
        country_codes=sorted({r['country_code'] for r in descriptions if r['country_code']}),
        snapshot_dataset=row['dataset'],snapshot_release=row['upstream_release'],snapshot_retrieved_at=row['retrieved_at_utc'],
        snapshot_metadata=snapshot_metadata,
        membership_claims=[{k:r[k] for k in ['observation_id','membership_namespace','collection_code','status_code','coverage_start_year','coverage_end_year']} for r in memberships],
        metric_claims=[{k:r[k] for k in ['observation_id','metric_namespace','metric_code','period_year','category_name','value_type','value_text','value_integer','value_real','unit']} for r in metrics],
        observation_ids_used=sorted(r['observation_id'] for rows in [descriptions,identifiers,memberships,metrics,attributes] for r in rows),
        evidence_warnings=['INVALID_ISSN_CLAIMS'] if invalid else [],normalization_versions=VERSIONS)
    if has_attributes:
        evidence.update(attribute_claims=[{k:r[k] for k in ['observation_id','attribute_namespace','attribute_code','value_json','source_field_path']} for r in attributes],
            provider_provenance=dict(snapshot_id=row['snapshot_id'],artifact_sha256=row['artifact_sha256'],record_locator=row['record_locator'],
                raw_record_metadata=json.loads(row['record_metadata_json']),source_locator=row['source_locator'],mirror_commit_sha=row['mirror_commit_sha']))
    return evidence
