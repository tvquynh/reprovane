"""Schema adapters modeled on the actual pinned provider CSVs, not inferred metrics."""
import csv,re
from ..core import ValidationError
from ..venue_resolution.normalization import normalize_issn,title_exact

SCOPUS_REQUIRED={'Sourcerecord ID','Source Title','ISSN','EISSN','Active or Inactive','Coverage','Source Type','Publisher','All Science Journal Classification Codes (ASJC)'}
WOS_REQUIRED={'Journal title','ISSN','eISSN','Publisher name','Publisher address','Languages','Web of Science Categories'}


def split_values(value,separator): return sorted({v.strip() for v in value.split(separator) if v.strip()})


def coverage(value):
    segments=[]; unparsed=[]
    for part in split_values(value,';'):
        m=re.fullmatch(r'(\d{4})(?:\s*-\s*(\d{4}|Present|present))?',part)
        if not m or int(m[1])<1 or m[2] and m[2].isdigit() and int(m[2])<int(m[1]): unparsed.append(part); continue
        segments.append(dict(start=int(m[1]),end=int(m[2]) if m[2] and m[2].isdigit() else (int(m[1]) if not m[2] else None)))
    return dict(original=value,segments=segments,unparsed=unparsed)


def parse_rows(path,provider,collection=None):
    required=SCOPUS_REQUIRED if provider=='scopus' else WOS_REQUIRED
    try:
        with path.open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.reader(stream); headers=next(reader,[]); names=[' '.join(h.split()) for h in headers]
            if len(names)!=len(set(names)) or not required<=set(names): raise ValidationError('Provider CSV required structural columns missing/duplicate')
            for ordinal,cells in enumerate(reader,2):
                if not cells or not any(c.strip() for c in cells): continue
                if len(cells)!=len(names): raise ValidationError(f'Malformed provider row width at logical row {ordinal}')
                row={key:value.strip() for key,value in zip(names,cells)}
                title=row['Source Title' if provider=='scopus' else 'Journal title']
                native=row.get('Sourcerecord ID') if provider=='scopus' else None
                if not title or provider=='scopus' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',native or ''):
                    raise ValidationError(f'Missing title/native identifier at logical row {ordinal}')
                identifiers=[]; invalid=[]
                for field,namespace in [('ISSN','issn'),('EISSN' if provider=='scopus' else 'eISSN','eissn')]:
                    for token in re.split(r'[,;|]',row[field]):
                        if not token.strip(): continue
                        token=token.strip(); valid=normalize_issn(token)
                        identifiers.append(dict(namespace=namespace,original=token,normalized=valid or re.sub(r'[^A-Za-z0-9]','',token).upper() or token,field=field))
                        if not valid: invalid.append(token)
                publisher=row.get('Publisher' if provider=='scopus' else 'Publisher name') or None
                attributes={}; status=None
                if provider=='scopus':
                    raw=row['Active or Inactive']; status={'active':'ACTIVE','inactive':'INACTIVE'}.get(raw.casefold(),'UNKNOWN')
                    codes=split_values(row['All Science Journal Classification Codes (ASJC)'],';')
                    categories=[dict(code=m[1],label=row[h]) for h in names if (m:=re.match(r'^(\d{4})\s+(.+)$',h)) and row[h]]
                    attributes['ASJC']=dict(original=row['All Science Journal Classification Codes (ASJC)'],codes=codes,categories=categories,top_level={h:row[h] for h in names if h.startswith('Top level:') and row[h]})
                    attributes['COVERAGE']=coverage(row['Coverage']); attributes['RAW_STATUS']=dict(original=raw)
                    if row.get('Titles Discontinued by Scopus'): attributes['DISCONTINUED']=dict(original=row['Titles Discontinued by Scopus'])
                    language_field='Article Language in Source (Three-Letter ISO Language Codes)'
                    if row.get(language_field): attributes['LANGUAGES']=dict(original=row[language_field],values=split_values(row[language_field],';'),source_field=language_field)
                else:
                    attributes['CATEGORIES']=dict(original=row['Web of Science Categories'],values=split_values(row['Web of Science Categories'],'|'))
                    attributes['LANGUAGES']=dict(original=row['Languages'],values=split_values(row['Languages'],'|'))
                    if row['Publisher address']: attributes['PUBLISHER_ADDRESS']=dict(original=row['Publisher address'])
                # WoS union is an exact within-provider collection-table record join,
                # requiring identical title and identifier claims; no fabricated native ID.
                key=native if native else (title_exact(title),tuple(sorted((i['namespace'],i['normalized']) for i in identifiers)))
                if provider=='wos' and not any(normalize_issn(i['normalized']) for i in identifiers): key=tuple(sorted(row.items()))
                yield dict(ordinal=ordinal,title=title,native=native,publisher=publisher,source_type=row.get('Source Type') or None,
                    identifiers=identifiers,invalid_issns=invalid,attributes=attributes,status=status,collection=collection,key=key,
                    row_signature=tuple(sorted(row.items())),unknown_columns=sorted(set(names)-required),publisher_address=row.get('Publisher address'))
    except (OSError,UnicodeError,csv.Error): raise ValidationError('Provider CSV cannot be parsed') from None
