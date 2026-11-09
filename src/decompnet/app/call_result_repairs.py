"""Bounded hypotheses for narrowing call results at their comparison instead of in locals."""
import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'


def variants(source, name):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', span[0], span[1])
    body = clean[opening:span[1]]
    if '#' in body or re.search(r'\bvolatile\b', body):
        return []
    result = []
    # Only isolated two-local declarations. Do not remove a local used elsewhere, move a call across
    # another statement, or duplicate it. Both original calls occur once in the candidate comparison.
    declaration = re.compile(r'\b(?:register\s+)?(u8|u16)\s+('+IDENT+r')\s*,\s*('+IDENT+r')\s*;')
    for decl in list(declaration.finditer(body))[:2]:
        type_, left, right = decl.groups()
        mask = '(?:0[xX]0*[fF]{2}|255)' if type_ == 'u8' else '(?:0[xX]0*[fF]{4}|65535)'
        def operand(local, mask=mask):
            return r'(?:\(\s*'+re.escape(local)+r'\s*&\s*'+mask+r'\s*\)|\b'+re.escape(local)+r'\b)'
        call = IDENT+r'\s*\(\s*\*?\s*'+IDENT+r'\s*\)'
        pattern = re.compile(r'\b'+re.escape(left)+r'\s*=\s*(?P<a>'+call+r')\s*;\s*'
                             +re.escape(right)+r'\s*=\s*(?P<b>'+call+r')\s*;\s*'
                             +r'if\s*\(\s*'+operand(left)+r'\s*(?P<op>>|<|==|!=|>=|<=)\s*'
                             +operand(right)+r'\s*\)')
        pairs = list(pattern.finditer(body))
        if not pairs or len(pairs) > 4:
            continue
        if any(len(re.findall(r'\b'+re.escape(local)+r'\b', body)) != 1+2*len(pairs) for local in (left,right)):
            continue
        edits = [(decl.start(),decl.end(),'')]
        for pair in pairs:
            a = source[opening+pair.start('a'):opening+pair.end('a')]
            b = source[opening+pair.start('b'):opening+pair.end('b')]
            edits.append((pair.start(),pair.end(),f'if (({type_}){a} {pair["op"]} ({type_}){b})'))
        candidate = source
        for start,end,text in sorted(edits,reverse=True):
            candidate = candidate[:opening+start]+text+candidate[opening+end:]
        result.append(candidate)
    return result
