"""Bounded simple C prototype discrepancies, advisory rather than inferred original signatures."""
import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'
# Only known scalar spellings: an unfamiliar typedef may alias another spelling.
ALIASES = {'void':'void', 'int':'s32', 'signed int':'s32', 'signed':'s32', 's32':'s32',
           'unsigned':'u32', 'unsigned int':'u32', 'u32':'u32',
           'signed char':'s8', 's8':'s8', 'unsigned char':'u8', 'u8':'u8',
           'short':'s16', 'short int':'s16', 'signed short':'s16', 's16':'s16',
           'unsigned short':'u16', 'unsigned short int':'u16', 'u16':'u16',
           'float':'f32', 'f32':'f32', 'double':'f64', 'f64':'f64'}
TYPE = r'(?:[A-Za-z_]\w*\s+)*[A-Za-z_]\w*(?:\s*\*)*'


def type_name(text):
    text = ' '.join(text.strip().split())
    # Qualifiers, arrays, function pointers and attributes need fuller type resolution.
    if not re.fullmatch(r'[\w\s]+(?:\s*\*)*', text):
        return None
    base = text.rstrip('*').strip()
    if base not in ALIASES:
        return None
    return ALIASES[base] + '*' * text.count('*')


def signature(result, parameters, *, named):
    result = type_name(result)
    if result is None or not parameters.strip():
        return None  # () is unspecified in the period C dialect, not a zero-argument prototype
    if parameters.strip() == 'void':
        return result, ()
    parts = parameters.split(',')
    if len(parts) > 8:
        return None
    types = []
    for part in parts:
        # A declaration may omit names; definitions must name each ordinary parameter.
        normalized = type_name(part)
        if normalized is None or named:
            match = re.fullmatch(rf'(.+?[\s*])({IDENT})\s*', part.strip())
            normalized = type_name(match[1]) if match else None
        if normalized in (None, 'void'):
            return None
        types.append(normalized)
    return result, tuple(types)


def mismatch(caller, selected, name):
    if not re.fullmatch(IDENT, name) or max(len(caller), len(selected)) > 64000:
        return ''
    caller, selected = _blank(caller), _blank(selected)
    macro = rf'^\s*#\s*define\s+{re.escape(name)}\b'
    if re.search(macro, caller, re.M) or re.search(macro, selected, re.M):
        return ''
    span = find(selected, name)
    if not span:
        return ''
    pattern = rf'\b(?!return\b)(?:(?:extern|static|inline)\s+)*({TYPE})\s*\b{re.escape(name)}\s*\(([^()]*)\)\s*'
    definition = re.fullmatch(pattern+r'{[\s\S]*}\s*', selected[span[0]:span[1]].strip())
    declarations = list(re.finditer(pattern+r';', caller))
    if not definition or len(declarations) != 1:
        return ''
    actual = signature(definition[1], definition[2], named=True)
    declared = signature(declarations[0][1], declarations[0][2], named=False)
    if actual is None or declared is None or actual == declared:
        return ''
    render = lambda s: s[0]+'('+(','.join(s[1]) or 'void')+')'
    return name+': caller '+render(declared)+', selected definition '+render(actual)


def message(leads):
    if not leads:
        return ''
    return ('Prototype review (advisory): '+'; '.join(leads)+'. Explicit caller declarations disagree '
            'with selected matched definitions. Neither source alone proves the original interface. '
            'Inspect actual arguments, return uses and all callers; validate compatible corrections jointly '
            'before allocation searches. No type or signature was changed.')
