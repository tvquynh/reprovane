"""Adapter for actual work_to_record rows; no invented raw fields/authorship ordering."""
from ..observations import NormalizationProvenance
from ..openalex_acquisition.normalize import normalize_work
from ..resolution.normalization import normalize_doi
from .inventory import native

PROFILE=NormalizationProvenance('legacy-v4-flat','1','1')


def normalize(observations,rid,row):
    if 'id' in row and 'openalex_id' not in row:
        normalize_work(observations,rid,row);return 'ATTACHED_OPENALEX_WORK_1'
    fields={}
    for source,target in [('title','title'),('display_name','subtitle'),('abstract','abstract_text'),('type','publication_type'),('publication_date','publication_date'),('language','language_code')]:
        if isinstance(row.get(source),str) and row[source].strip(): fields[target]=row[source]
    year=row.get('publication_year')
    if year not in (None,''):
        try: fields['publication_year']=int(year)
        except (ValueError,TypeError): pass
    if fields: observations.add_publication_metadata_observation(rid,PROFILE,**fields,field_provenance_json='{"source":"v4 work_to_record flattened row; not live API"}')
    oid=native(row)
    observations.add_publication_identifier_observation(rid,PROFILE,identifier_namespace='openalex_work_id',identifier_value_original=row['openalex_id'],identifier_value_normalized=oid,source_field_path='openalex_id')
    doi=row.get('doi')
    if isinstance(doi,str) and doi.strip(): observations.add_publication_identifier_observation(rid,PROFILE,identifier_namespace='doi',identifier_value_original=doi,identifier_value_normalized=normalize_doi(doi) or doi,source_field_path='doi')
    if row.get('source'): observations.add_venue_observation(rid,PROFILE,title=row['source'],source_type=row.get('source_type') or None,field_provenance_json='{"source":"legacy description only; no membership/ranking authority"}')
    # Flattened author/location/status strings remain in exact legacy bytes, never promoted.
    return 'V4_FLAT_OPENALEX_1'
