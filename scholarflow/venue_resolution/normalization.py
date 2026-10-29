import re
from ..resolution.normalization import normalize_title

VERSIONS = {'issn':'issn_normalizer_v1','exact':'venue_title_exact_v1',
            'normalized':'venue_title_normalized_v1','status':'venue_title_status_normalized_v1'}


def normalize_issn(value: str) -> str | None:
    value = re.sub(r'[^0-9A-Za-z]', '', value).upper()
    if not re.fullmatch(r'[0-9]{7}[0-9X]', value): return None
    digits = [int(c) for c in value[:7]] + [10 if value[-1]=='X' else int(value[-1])]
    return value if sum(n*w for n,w in zip(digits,range(8,0,-1))) % 11 == 0 else None


def title_exact(value: str) -> str:
    return ' '.join(value.casefold().split())


def title_normalized(value: str) -> str:
    return normalize_title(value)


def title_status_normalized(value: str) -> str:
    return title_normalized(re.sub(r'\s*\((?:discontinued|ceased)\)\s*$', '', value, flags=re.I))
