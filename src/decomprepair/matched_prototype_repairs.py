"""Test candidate-owned integer prototypes against referenced selected matched C.

Selected C is evidence for a hypothesis, not proof of the original interface. These
bounded candidates only change declarations; normal compilation and byte judging
must still accept them. Shared headers and the callee's source are never edited.
"""
import re

from .csource import _blank, find
from .integer_repair_support import IDENT, calls, edit, split_args, view

FAMILY = 'matched_integer_prototype'
LIMIT = 3
SPELLINGS = {
    'signed char': 'signed char', 'unsigned char': 'unsigned char',
    'short': 'short', 'short int': 'short', 'signed short': 'short', 'signed short int': 'short',
    'unsigned short': 'unsigned short', 'unsigned short int': 'unsigned short',
    'int': 'int', 'signed': 'int', 'signed int': 'int',
    'unsigned': 'unsigned int', 'unsigned int': 'unsigned int',
    's8': 'signed char', 'u8': 'unsigned char', 's16': 'short',
    'u16': 'unsigned short', 's32': 'int', 'u32': 'unsigned int',
}
TYPE = '|'.join(re.escape(s).replace(r'\ ', r'\s+') for s in sorted(SPELLINGS, key=len, reverse=True))
SCALAR = re.compile(rf'\s*(?P<type>{TYPE})(?:\s+(?P<name>{IDENT}))?\s*')
PROTO = re.compile(rf'(?:^|(?<=[;}}]))\s*(?:extern\s+)?(?P<result>{TYPE}|void)\s+'
                   rf'(?P<callee>{IDENT})\s*\((?P<params>[^();{{}}]*)\)\s*;', re.M)


def scalar(text, context, named=False):
    hit = SCALAR.fullmatch(text)
    if not hit or (named and not hit['name']):
        return None
    spelling = ' '.join(hit['type'].split())
    if spelling in {'s8', 'u8', 's16', 'u16', 's32', 'u32'}:
        aliases = re.findall(rf'\btypedef\s+([^;{{}}]+)\s+{spelling}\s*;', context)
        # Do not assume a conventional typedef has its conventional meaning.
        if not aliases or any(SPELLINGS.get(' '.join(a.split())) != SPELLINGS[spelling] for a in aliases):
            return None
    return hit, SPELLINGS[spelling]


def variants(source, name, header='', callees=''):
    """At most three individual callees and one combined declaration-only candidate."""
    part = view(source, name)
    if not part or max(len(source), len(header), len(callees)) > 64000:
        return []
    context = _blank(source+'\n'+header)
    selected = _blank(callees)
    # Conditional declarations and exotic scopes need a real preprocessor/type resolver.
    if re.search(r'^\s*#\s*(?:if|ifdef|ifndef|elif|else|endif)\b', context+'\n'+selected, re.M):
        return []
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b', context+'\n'+selected, re.M))
    if macros & ({word for spelling in SPELLINGS for word in spelling.split()} | {'void', name}):
        return []
    groups = []
    for proto in PROTO.finditer(part.clean, 0, part.opening):
        callee = proto['callee']
        if callee == name or callee in macros or find(source, callee):
            continue
        # Require one file-scope declaration and only direct calls in the target body.
        if part.clean[:proto.start()].count('{') != part.clean[:proto.start()].count('}'):
            continue
        outside = part.clean[:proto.start()]+part.clean[proto.end():part.opening]
        outside += part.clean[part.end:]
        if re.search(rf'\b{callee}\b', outside) or re.search(rf'\b{callee}\b', _blank(header)):
            continue
        sites = list(calls(part, callee))
        uses = list(re.finditer(rf'\b{callee}\b', part.body))
        if (not sites or len(sites) != len(uses)
            or re.search(rf'\b(?:{TYPE})\s+{callee}\s*\(', part.body)):
            continue
        span = find(selected, callee)
        if not span:
            continue
        pattern = rf'(?:static\s+)?(?:inline\s+)?(?P<result>{TYPE}|void)\s+{callee}\s*\((?P<params>[^()]*)\)\s*{{'
        definition = re.fullmatch(pattern+r'[\s\S]*}\s*', selected[span[0]:span[1]].strip())
        # Duplicate definitions, even identical ones, cannot identify one selected contract.
        if not definition or len(re.findall(rf'\b{callee}\s*\([^;{{}}]*\)\s*{{', selected)) != 1:
            continue
        declared = [proto['params'][a:b] for a, b in split_args(proto['params'])]
        actual = [definition['params'][a:b] for a, b in split_args(definition['params'])]
        if not 1 <= len(actual) == len(declared) <= 8:
            continue
        if any(len(split_args(part.clean[a:b])) != len(actual) for a, b in sites):
            continue
        old = [scalar(p, context) for p in declared]
        new = [scalar(p, context, named=True) for p in actual]
        if not all(old) or not all(new):
            continue  # pointers, callbacks, floats, qualifiers and unknown typedefs stay review items
        if any(p[0]['name'] == callee for p in old):
            continue
        if proto['result'] == 'void' or definition['result'] == 'void':
            if proto['result'] != definition['result']:
                continue
            returns = None
        else:
            returns = scalar(proto['result'], context), scalar(definition['result'], context)
            if not all(returns):
                continue
        changes = []
        if returns and returns[0][1] != returns[1][1]:
            changes.append((proto.start('result'), proto.end('result'), returns[1][1]))
        for (a, _), before, after in zip(split_args(proto['params']), old, new):
            if before[1] != after[1]:
                start = proto.start('params')+a
                changes.append((start+before[0].start('type'), start+before[0].end('type'), after[1]))
        if changes:
            groups.append(changes)
        if len(groups) == LIMIT:
            break
    proposals = [edit(source, group) for group in groups]
    if len(groups) > 1:
        proposals.append(edit(source, [change for group in groups for change in group]))
    return [(FAMILY, text) for text in dict.fromkeys(proposals)]
