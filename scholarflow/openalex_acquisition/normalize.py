"""Typed claims only. No authors/entities/venue memberships or discovery decisions."""
import re
from ..observations import NormalizationProvenance
from ..resolution.normalization import normalize_doi

NORMALIZER=NormalizationProvenance('openalex-work','1','1')


def native_id(value,kind='W'):
    if not isinstance(value,str): raise ValueError('Missing OpenAlex native identity')
    token=value.removeprefix('https://openalex.org/').upper()
    if not re.fullmatch(kind+r'[0-9]+',token): raise ValueError('Malformed OpenAlex native identity')
    return 'https://openalex.org/'+token


def source_claims(observations,rid,source):
    if not isinstance(source,dict): return
    values={k:v for k,v in dict(title=source.get('display_name'),source_type=source.get('type'),publisher_name=source.get('host_organization_name')).items() if isinstance(v,str) and v.strip()}
    if values: observations.add_venue_observation(rid,NORMALIZER,**values,field_provenance_json='{"source":"primary_location.source"}')
    claims=[]
    if source.get('id'): claims.append(('openalex_source_id',native_id(source['id'],'S'),'primary_location.source.id'))
    if source.get('issn_l'): claims.append(('issn_l',source['issn_l'],'primary_location.source.issn_l'))
    for value in source.get('issn') or []: claims.append(('issn',value,'primary_location.source.issn'))
    seen=set()
    for namespace,value,path in claims:
        if not isinstance(value,str) or not value.strip() or (namespace,value) in seen: continue
        seen.add((namespace,value))
        observations.add_venue_identifier_observation(rid,NORMALIZER,identifier_namespace=namespace,identifier_value_original=value,identifier_value_normalized=(re.sub(r'[^A-Za-z0-9]','',value).upper() or value.strip()) if namespace in ('issn','issn_l') else value,source_field_path=path)


def normalize_work(observations,rid,work):
    fields={}
    for field,raw in [('title',work.get('title') or work.get('display_name')),('publication_type',work.get('type')),('publication_year',work.get('publication_year')),('publication_date',work.get('publication_date')),('language_code',work.get('language'))]:
        if raw is not None and raw!='': fields[field]=raw
    abstract=work.get('abstract_inverted_index')
    if abstract:
        positioned=[]
        for word,positions in abstract.items():
            if not isinstance(word,str) or not isinstance(positions,list) or any(type(p) is not int or not 0<=p<1000000 for p in positions): raise ValueError('Invalid abstract positions')
            positioned.extend((p,word) for p in positions)
        fields['abstract_text']=' '.join(word for _,word in sorted(positioned))
    if fields: observations.add_publication_metadata_observation(rid,NORMALIZER,**fields,field_provenance_json='{"source":"OpenAlex work"}')
    oid=native_id(work['id'])
    observations.add_publication_identifier_observation(rid,NORMALIZER,identifier_namespace='openalex_work_id',identifier_value_original=work['id'],identifier_value_normalized=oid,source_field_path='id')
    doi=work.get('doi')
    if isinstance(doi,str) and doi.strip():
        observations.add_publication_identifier_observation(rid,NORMALIZER,identifier_namespace='doi',identifier_value_original=doi,identifier_value_normalized=normalize_doi(doi) or doi.strip().lower(),source_field_path='doi')
    for position,authorship in enumerate(work.get('authorships') or [],1):
        author=authorship.get('author') or {}; name=author.get('display_name')
        if not isinstance(name,str) or not name.strip(): continue  # Raw evidence retained; no invented author name.
        kwargs={}
        if author.get('id'):
            kwargs.update(provider_author_id_namespace='openalex_author',provider_author_id_original=author['id'],provider_author_id_normalized=native_id(author['id'],'A'))
        orcid=author.get('orcid')
        if isinstance(orcid,str):
            token=orcid.removeprefix('https://orcid.org/').upper()
            if re.fullmatch(r'[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]',token): kwargs.update(orcid_original=orcid,orcid_normalized=token)
        observations.add_publication_authorship_observation(rid,NORMALIZER,author_position=position,author_display_name=name,source_field_path=f'authorships[{position-1}]',**kwargs)
    source_claims(observations,rid,(work.get('primary_location') or {}).get('source'))
    if work.get('is_retracted') is True:
        observations.add_integrity_event_observation(rid,NORMALIZER,event_type='RETRACTION',subject_identifier_namespace='openalex_work_id',subject_identifier_value=oid,status_code='REPORTED_BY_OPENALEX',source_field_path='is_retracted')
