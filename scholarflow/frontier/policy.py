"""Finite structural policy. All operative bounds are explicit protocol inputs."""
from dataclasses import dataclass
import json
from ..core import ValidationError
from ..core.provenance import canonical_json
from ..discovery.store import digest

DIRECTIONS=('BACKWARD_CITATION','FORWARD_CITATION')


@dataclass(frozen=True)
class FrontierPolicy:
    canonical: str

    @classmethod
    def from_value(cls,value):
        if isinstance(value,cls): return value
        required={'directions','max_depth','max_rounds','max_seeds_per_round','max_results_per_seed','batch_or_page_size','corpus','global_new_work_budget','global_child_acquisition_budget','saturation'}
        if not isinstance(value,dict) or required-set(value) or set(value)-required-{'ordering'}: raise ValidationError('Complete explicit finite frontier policy required')
        body=dict(value); directions=body['directions']
        if not isinstance(directions,list) or not directions or any(x not in DIRECTIONS for x in directions): raise ValidationError('Invalid citation directions')
        body['directions']=[x for x in DIRECTIONS if x in directions]
        for k in required-{'directions','corpus','saturation'}:
            if type(body[k]) is not int or body[k]<1: raise ValidationError('Positive finite frontier bounds required')
        if body['max_depth']>10000 or body['max_results_per_seed']>10000 or body['max_seeds_per_round']>100 or body['batch_or_page_size']>100: raise ValidationError('Accepted Stage08 bounds exceeded')
        if body['corpus'] not in (None,'core','all','expansion'): raise ValidationError('Invalid corpus override')
        body.setdefault('ordering','DEPTH_EVENT_WORK')
        if body['ordering']!='DEPTH_EVENT_WORK': raise ValidationError('Only structural ordering supported')
        saturation=body['saturation']
        if saturation is not None:
            if not isinstance(saturation,dict) or set(saturation)!={'minimum_new_items','consecutive_rounds'} or type(saturation['minimum_new_items']) is not int or saturation['minimum_new_items']<0 or type(saturation['consecutive_rounds']) is not int or saturation['consecutive_rounds']<1: raise ValidationError('Explicit structural saturation thresholds required')
        return cls(canonical_json(body))

    @property
    def sha256(self): return digest(self.as_dict())
    def as_dict(self): return json.loads(self.canonical)
    def worst_case(self):
        p=self.as_dict(); actions=p['max_rounds']*p['max_seeds_per_round']*len(p['directions']); n=p['max_results_per_seed']; batch=p['batch_or_page_size']
        return dict(selected_seeds=p['max_rounds']*p['max_seeds_per_round'],actions=actions,returned_work_upper_bound=actions*(n+1),child_acquisition_upper_bound=actions*(1+(n+batch-1)//batch),global_new_work_cap=p['global_new_work_budget'],global_child_cap=p['global_child_acquisition_budget'])
