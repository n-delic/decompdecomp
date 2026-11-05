"""Flag ambiguous forwarding stubs before their exact bytes are mistaken for a C interface."""
import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'
LIMIT = 3


def forwarding(source, name):
    """A plain void/no-argument stub calling an unspecified-prototype function, not proof of a defect."""
    if not re.fullmatch(IDENT, name) or len(source) > 64000:
        return None
    clean = _blank(source)
    span = find(source, name)
    if not span:
        return None
    stub = re.fullmatch(rf'\s*(?:static\s+)?void\s+{re.escape(name)}\s*\(\s*(?:void)?\s*\)\s*'
                        rf'{{\s*({IDENT})\s*\(\s*\)\s*;\s*}}\s*', clean[span[0]:span[1]])
    if not stub or stub[1] == name:
        return None
    callee = stub[1]
    if re.search(rf'^\s*#\s*define\s+(?:{re.escape(name)}|{re.escape(callee)})\b', clean, re.M):
        return None
    declarations = list(re.finditer(rf'\b{re.escape(callee)}\s*\(([^()]*)\)\s*([;{{])', clean))
    if len(declarations) != 2 or any(d[1].strip() or d[2] != ';' for d in declarations):
        return None  # one unspecified declaration and the one body call; explicit/ambiguous types need review
    return callee


def message(leads):
    if not leads:
        return ''
    names = ', '.join(f'{wrapper} -> {callee}' for wrapper, callee in leads[:LIMIT])
    return ('Wrapper-contract review (advisory): '+names+'. A void/no-argument stub calls a helper with an '
            'unspecified prototype. Exact wrapper bytes do not establish its C argument or return contract: '
            'incoming arguments and a result may pass through unchanged. Inspect callers and the downstream '
            'definition before allocation searches; validate explicit forwarding and caller declarations jointly. '
            'No signature was changed.')


def source_hint(source, name):
    callee = forwarding(source, name)
    return message([(name, callee)]) if callee else ''


def brief(net, proj, fn, source):
    """One bounded matched-callee lookup, also usable for project-mode C functions."""
    leads = []
    if callee := forwarding(source, fn['name']):
        leads.append((fn['name'], callee))
    if source:
        from ..candidate_scope import callees
        for row in callees(net, proj, fn, source, max_source_chars=64000):
            if callee := forwarding(row['matched_source'] or '', row['name']):
                leads.append((row['name'], callee))
            if len(leads) >= LIMIT:
                break
    return message(leads)
