"""Small, bounded source hypotheses from repair captures, accepted only by the ordinary judge."""
import re

from ..forks.csource import _blank, find
from .pattern_repairs import close

ID = r'[A-Za-z_]\w*'
FLOATS = {'0x3DCCCCCD': '0.1f', '0x3F000000': '0.5f', '0x3F800000': '1.0f', '0x40000000': '2.0f'}


def variants(source, name):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    body = clean[opening:span[1]]
    if re.search(r'\bvolatile\b|^\s*#', clean, re.M):
        return []
    result = []
    # Named compiler-runtime leads, not guessed arbitrary game signatures. Wrong identities simply miss.
    runtime = {
        '__d_to_ull': 'u64 __d_to_ull(f64 value) { return (u64)value; }',
        '__ll_mod': 's64 __ll_mod(s64 a, s64 b) { s64 r = a % b; if ((r < 0 && b > 0) || (r > 0 && b < 0)) r += b; return r; }',
    }
    if name in runtime:
        result.append(('runtime_scalar_signature', source[:span[0]]+runtime[name]+'\n'+source[span[1]:]))
    # Share an existing final return, without introducing a jump into another lexical scope.
    final = re.search(r'\breturn\s+(-?\d+)\s*;\s*}$', body)
    if final and not re.search(r'\b(?:goto|switch)\b|'+ID+r'\s*:(?!:)', body):
        for early in list(re.finditer(r'\breturn\s+'+re.escape(final[1])+r'\s*;', body[:final.start()]))[:2]:
            label = 'decomp_shared_return'
            if re.search(r'\b'+label+r'\b', clean):
                continue
            edits = [(opening+final.start(), opening+final.start(), label+':\n'),
                     (opening+early.start(), opening+early.end(), 'goto '+label+';')]
            text = source
            for a,b,value in sorted(edits, reverse=True):
                text = text[:a]+value+text[b:]
            result.append(('shared_return_tail', text))
    # Direct, side-effect-free scalar/field stores. Complex lvalues and volatile code stay unchanged.
    lhs = ID+r'(?:\s*(?:->|\.)\s*'+ID+r'|\[\d+\])*'
    for hit in list(re.finditer(r'(?P<lhs>'+lhs+r')\s*=\s*(?P=lhs)\s*(?P<op>[|&^])\s*(?P<rhs>0[xX][0-9A-Fa-f]+|\d+)\s*;', body))[:4]:
        a,b = opening+hit.start(), opening+hit.end()
        result.append(('direct_compound_store', source[:a]+f"{hit['lhs']} {hit['op']}= {hit['rhs']};"+source[b:]))
    # Remove only a scalar nonzero guard around a zero-start counted loop with the same bound.
    # Leave signed bounds, casts and expressions to the agent: a negative bound can change behaviour.
    pattern = rf'\bif\s*\(\s*(?P<n>{ID})\s*!=\s*0\s*\)\s*{{\s*(?P<loop>for\s*\(\s*(?P<i>{ID})\s*=\s*0\s*;\s*(?P=i)\s*<\s*(?P=n)\s*;\s*(?P=i)\+\+\s*\)\s*{{)'
    for hit in list(re.finditer(pattern, body))[:2]:
        loop_start = opening+hit.start('loop')
        loop_end = close(clean, opening+hit.end()-1, '{', '}')
        guard_end = close(clean, opening+body.find('{', hit.start()), '{', '}')
        if loop_end and guard_end and not clean[loop_end:guard_end-1].strip():
            result.append(('redundant_loop_guard', source[:opening+hit.start()]+source[loop_start:loop_end]+source[guard_end:]))
    # Candidate-local prototypes only. Bit-pattern constants and their float interpretation are paired;
    # this is an ABI/compiler hypothesis, not a semantic equivalence assertion.
    for call in re.finditer(r'\b('+ID+r')\s*\(', body):
        callee = call[1]
        if callee in {'if','while','for','sizeof','switch'}:
            continue
        end = close(body, call.end()-1)
        if not end:
            continue
        args = body[call.end():end-1].split(',')
        proto = list(re.finditer(r'\b(?:extern\s+)?(?:s32|int|void)\s+'+re.escape(callee)+r'\s*\(([^;{}()]*)\)\s*;', clean[:opening]))
        if len(proto) != 1 or len(re.findall(r'\b'+re.escape(callee)+r'\s*\(', body)) != 1:
            continue
        types = proto[0][1].split(',')
        if len(types) != len(args):
            continue
        for index, arg in enumerate(args):
            value = FLOATS.get(arg.strip().upper().replace('0X','0x'))
            if not value or types[index].strip() not in {'s32','int','u32','unsigned int'}:
                continue
            new_args, new_types = list(args), list(types)
            new_args[index], new_types[index] = value, 'f32'
            edits = [(opening+call.end(), opening+end-1, ','.join(new_args)),
                     (proto[0].start(1), proto[0].end(1), ','.join(new_types))]
            text = source
            for a,b,replacement in sorted(edits, reverse=True):
                text = text[:a]+replacement+text[b:]
            result.append(('float_argument_bits', text))
    return list(dict.fromkeys(result))[:10]
