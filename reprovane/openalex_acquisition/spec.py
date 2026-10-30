"""Literal, bounded works requests; transport secrets never participate in identity."""
import hashlib,re
from dataclasses import dataclass
from ..core import ValidationError
from ..core.provenance import canonical_json


@dataclass(frozen=True)
class AcquisitionSpec:
    canonical: str

    @classmethod
    def from_value(cls,value):
        if isinstance(value,cls): return value
        if not isinstance(value,dict): raise ValidationError('Acquisition specification must be an object')
        value={k:v for k,v in value.items() if k not in {'api_key','mailto','email','authorization'}}
        allowed={'search','filters','sort','select','corpus','max_records','page_size','max_pages','query_mode','endpoint'}
        if set(value)-allowed: raise ValidationError('Unsupported acquisition parameter')
        body=dict(endpoint='works',search=None,filters={},sort=None,select=None,corpus='core',max_records=100,page_size=100,max_pages=1000,query_mode='search')
        body.update(value)
        if body['endpoint']!='works' or body['corpus'] not in ('core','all','expansion') or body['query_mode'] not in ('search','title_and_abstract'):
            raise ValidationError('Unsupported endpoint/corpus/query mode')
        for key in ['max_records','page_size','max_pages']:
            if type(body[key]) is not int or body[key]<1: raise ValidationError('Positive acquisition bounds required')
        if body['page_size']>100 or body['max_pages']>10000: raise ValidationError('Acquisition paging bound exceeded')
        for key in ['search','sort']:
            if body[key] is not None and (not isinstance(body[key],str) or not body[key].strip()): raise ValidationError('Invalid literal query string')
        filters=body['filters']
        if isinstance(filters,dict):
            if any(not isinstance(k,str) or not re.fullmatch(r'[a-z][a-z0-9_.]*',k) or k in ('api_key','mailto','is_xpac') or not isinstance(v,(str,int,bool)) or isinstance(v,str) and not v for k,v in filters.items()): raise ValidationError('Invalid filter mapping')
            body['filters']=','.join(k+':'+(str(v).lower() if isinstance(v,bool) else str(v)) for k,v in sorted(filters.items()))
        elif not isinstance(filters,str): raise ValidationError('Filters must be a mapping or literal expression')
        if re.search(r'(?i)\b(api_key|access_token|authorization|password|mailto|include_xpac|is_xpac)\s*[:=]',body['filters']): raise ValidationError('Secret/deprecated controls are not acquisition filters')
        if not body['search'] and not filters: raise ValidationError('A narrowed search or filter is required')
        select=body['select']
        if select is not None:
            if isinstance(select,str): select=select.split(',')
            if not isinstance(select,(list,tuple)) or not select or any(not isinstance(k,str) or not re.fullmatch(r'[a-z][a-z0-9_]*',k.strip()) for k in select): raise ValidationError('Select supports root fields only')
            body['select']=sorted(set(k.strip() for k in select)|{'id'})
        return cls(canonical_json(body))

    def as_dict(self):
        import json
        return json.loads(self.canonical)

    @property
    def sha256(self): return hashlib.sha256(self.canonical.encode()).hexdigest()

    def parameters(self,cursor,remaining):
        body=self.as_dict(); params={}
        if body['search']: params['search' if body['query_mode']=='search' else 'search.title_and_abstract']=body['search']
        filters=body['filters']
        if isinstance(filters,dict): filters=','.join(k+':'+(str(v).lower() if isinstance(v,bool) else str(v)) for k,v in sorted(filters.items()))
        if filters: params['filter']=filters
        if body['sort']: params['sort']=body['sort']
        if body['select']: params['select']=','.join(body['select'])
        params.update(corpus=body['corpus'],per_page=min(body['page_size'],remaining),cursor=cursor)
        return params
