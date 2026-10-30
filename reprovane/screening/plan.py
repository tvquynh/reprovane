"""Explicit human protocol; no content classifier or scientific defaults."""
from dataclasses import dataclass
import json,re
from ..core import ValidationError
from ..core.provenance import canonical_json
from ..discovery.store import digest

DECISIONS=('SCREEN_INCLUDE','SCREEN_EXCLUDE','SCREEN_UNSURE')
FIELDS=('title','abstract','publication_year','publication_date','doi','authors','venue','work_type','integrity','provider_identifiers')
OPERATIONAL_CODES={'MAX_DEPTH','MAX_ROUNDS','ROUND_SEED_LIMIT','GLOBAL_BUDGET_LIMIT','GLOBAL_NEW_WORK_BUDGET','GLOBAL_CHILD_ACQUISITION_BUDGET','STRUCTURAL_SATURATION','NO_ELIGIBLE_FRONTIER','DIRECTION_DISABLED','ALREADY_EXPANDED','MAX_DEPTH_REACHED','SELECTED','NO_RESOLVED_OPENALEX_SEED'}


def label(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',value): raise ValidationError('Safe non-email actor label required')
    return value


def private_text(value,maximum,forbidden_values=()):
    if value is None or value=='': return None
    if not isinstance(value,str) or len(value)>maximum or '\x00' in value: raise ValidationError('Annotation length/type rejected')
    patterns=[r'(?i)\b(?:api[_ -]?key|password|passwd|authorization|access[_ -]?token|cookie|set-cookie)\s*[:=]',r'(?i)\bbearer(?:\s|%20)+\S+',r'-----BEGIN .*PRIVATE KEY',r'\bgh[pousr]_[A-Za-z0-9_]{20,}',r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}',r'\bAKIA[0-9A-Z]{16}\b',r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}']
    if any(re.search(p,value) for p in patterns) or any(v and v in value for v in forbidden_values): raise ValidationError('Sensitive annotation refused')
    # Bare compact high-entropy tokens are not appropriate reviewer annotations.
    for word in re.findall(r'\b[A-Za-z0-9_-]{20,}\b',value):
        if any(c.isdigit() for c in word) and any(c.isalpha() for c in word) and (any(c.isupper() for c in word) or '_' in word or '-' in word): raise ValidationError('Credential-like annotation refused')
    return value


@dataclass(frozen=True)
class ScreeningPlan:
    canonical: str

    @classmethod
    def from_value(cls,value):
        if isinstance(value,cls): value=json.loads(value.canonical)
        keys={'level','allowed_decisions','criteria','required_reviewers','assignment_policy','assignments','reviewers','adjudicators','consensus_policy','exclusion_reason_policy','adjudication_policy','allowed_evidence_fields','note_policy','correction_policy','adjudication_correction_policy'}
        if not isinstance(value,dict) or set(value)!=keys: raise ValidationError('Complete explicit screening plan required')
        p=json.loads(canonical_json(value))
        if p['level']!='TITLE_ABSTRACT' or not isinstance(p['allowed_decisions'],list) or set(p['allowed_decisions'])!=set(DECISIONS): raise ValidationError('Metadata screening vocabulary required')
        p['allowed_decisions']=list(DECISIONS)
        labels=set()
        for role in ['reviewers','adjudicators']:
            if not isinstance(p[role],list): raise ValidationError('Explicit actor definitions required')
            for actor in p[role]:
                if not isinstance(actor,dict) or set(actor)!={'label','actor_type'} or actor['actor_type']!='HUMAN': raise ValidationError('Only explicitly declared HUMAN actors supported')
                name=label(actor['label'])
                if name in labels: raise ValidationError('Duplicate actor label')
                labels.add(name)
            p[role]=sorted(p[role],key=lambda a:a['label'])
        if type(p['required_reviewers']) is not int or not 1<=p['required_reviewers']<=len(p['reviewers']): raise ValidationError('Invalid reviewer count')
        if p['assignment_policy'] not in ('ALL_REVIEWERS','EXPLICIT_ASSIGNMENT'): raise ValidationError('Unsupported assignment policy')
        if p['assignment_policy']=='ALL_REVIEWERS':
            if p['assignments'] is not None or p['required_reviewers']!=len(p['reviewers']): raise ValidationError('All-reviewers count must match actor list')
        else:
            if not isinstance(p['assignments'],dict): raise ValidationError('Explicit entity assignments required')
            reviewers={a['label'] for a in p['reviewers']}
            for entity,assigned in p['assignments'].items():
                if not isinstance(entity,str) or not isinstance(assigned,list) or len(assigned)!=p['required_reviewers'] or len(set(assigned))!=len(assigned) or not set(assigned)<=reviewers: raise ValidationError('Invalid explicit assignment')
                p['assignments'][entity]=sorted(assigned)
        if p['consensus_policy'] not in ('UNANIMOUS','DISABLED') or p['adjudication_policy'] not in ('EXPLICIT','NONE'): raise ValidationError('Explicit consensus/adjudication policy required')
        if p['adjudication_policy']=='EXPLICIT' and not p['adjudicators']: raise ValidationError('Human adjudicator required')
        for key in ['correction_policy','adjudication_correction_policy']:
            if p[key] not in ('EXPLICIT_SUPERSESSION','FORBIDDEN'): raise ValidationError('Explicit correction policy required')
        reasons=p['exclusion_reason_policy']
        if not isinstance(reasons,dict) or set(reasons)!={'multiple','consensus'} or type(reasons['multiple']) is not bool or reasons['consensus']!='UNION': raise ValidationError('Explicit exclusion reason policy required')
        codes=set()
        if not isinstance(p['criteria'],list): raise ValidationError('Explicit criteria list required')
        for c in p['criteria']:
            if not isinstance(c,dict) or set(c)!={'code','label','description','kind'} or c['kind'] not in ('INCLUSION_GUIDANCE','EXCLUSION_REASON'): raise ValidationError('Invalid criterion definition')
            code=label(c['code'])
            if code in codes or code.upper() in OPERATIONAL_CODES|{'FAILED','COMPLETED_POLICY'}: raise ValidationError('Duplicate or operational criterion code refused')
            codes.add(code)
            if not private_text(c['label'],200) or not private_text(c['description'],2000): raise ValidationError('Explicit criterion text required')
        p['criteria']=sorted(p['criteria'],key=lambda c:c['code'])
        if not isinstance(p['allowed_evidence_fields'],list) or not p['allowed_evidence_fields'] or not set(p['allowed_evidence_fields'])<=set(FIELDS): raise ValidationError('Explicit supported evidence fields required')
        p['allowed_evidence_fields']=sorted(set(p['allowed_evidence_fields']))
        notes=p['note_policy']
        if not isinstance(notes,dict) or set(notes)!={'allow','max_length'} or type(notes['allow']) is not bool or type(notes['max_length']) is not int or not 0<=notes['max_length']<=4000 or not notes['allow'] and notes['max_length']!=0: raise ValidationError('Explicit bounded note policy required')
        return cls(canonical_json(p))

    def as_dict(self): return json.loads(self.canonical)
    @property
    def sha256(self): return digest(self.as_dict())
