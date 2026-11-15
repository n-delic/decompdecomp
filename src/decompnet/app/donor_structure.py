"""Compact structural differences from an already matched donor, without transplanting its ABI."""
import re

from ..forks.csource import _blank

PATTERNS = (
    ('single-pass scopes', r'\bdo\s*{[\s\S]*?}\s*while\s*\(\s*0\s*\)'),
    ('backward/explicit jumps', r'\bgoto\s+\w+\s*;'),
    ('explicit unsigned conversion branches', r'\bif\s*\([^;{}]+\)[\s\S]{0,300}?0x80000000[Uu]?\b'),
    ('typed pointer locals', r'\b\w+\s*\*\s*\w+\s*(?:;|=)'),
)


def brief(candidate, donor, similarity):
    """Bounded advisory counts even when a donor is too large to include in a brief."""
    if not donor or not candidate or similarity < .8 or max(len(donor),len(candidate))>262144:return ''
    left,right=_blank(candidate),_blank(donor)
    differences=[]
    for label,pattern in PATTERNS:
        a,b=len(re.findall(pattern,left)),len(re.findall(pattern,right))
        if a!=b:differences.append(f'{label}: yours {a}, donor {b}')
    if not differences:return ''
    return ('Matched-neighbour structural comparison (counts, not a causal diagnosis): '+
            '; '.join(differences[:4])+'. Inspect the differing operation before broad permutations. '
            'Keep this function\'s actual argument/return types and side-effect order; do not copy dead uses or donor ABI assumptions. '
            'For a return-copy/address-order plateau, try prepared_return_scope or prepared_call_scope with the normal byte judge.')
