"""Keep actual reference macros and surface aggregate ABI evidence without inventing layouts."""
import re

from ..forks.csource import _blank, find
from ..m2cfix import _split_top_level
from ..network import NetworkError

ID = r'[A-Za-z_]\w*'


def macros(text):
    out = {}
    lines = text.splitlines(True)
    i = 0
    while i < len(lines):
        start = i
        hit = re.match(r'^\s*#\s*define\s+('+ID+r')\b', lines[i])
        i += 1
        if not hit:
            continue
        while lines[i-1].rstrip('\r\n').endswith('\\') and i < len(lines):
            i += 1
        out[hit[1]] = (''.join(lines[start:i]), start+1)
    return out


def macro_context(original, source, header):
    available, existing = macros(original), macros(header)
    used = set(re.findall(ID, _blank(source)))
    selected = {}
    for _ in range(16):
        old = len(used)
        for name in sorted(used):
            if name in available:
                definition, line = available[name]
                if name in existing:
                    from ..repair_changes import TOKEN, without_comments
                    normalize = lambda x: TOKEN.findall(without_comments(x.replace('\\\n','')))
                    if normalize(existing[name][0]) != normalize(definition):
                        raise NetworkError(f'Conflicting reference macro {name}; choose the matching source/header pair.')
                else:
                    selected[name] = f'#line {line} "reference-macro.c"\n'+definition.rstrip()+'\n'
                used.update(re.findall(ID, _blank(definition)))
            elif name in existing:
                used.update(re.findall(ID, _blank(existing[name][0])))
        if len(used) == old:
            break
    return '\n'+''.join(selected.values()) if selected else ''


def dependencies(source, header):
    used = set(re.findall(ID, _blank(source)))
    definitions = macros(header)
    for _ in range(16):
        previous = len(used)
        for name in list(used):
            if name in definitions:
                used.update(re.findall(ID, _blank(definitions[name][0])))
        if len(used) == previous:
            break
    return used


def aggregate_hints(source, name, header, candidate=''):
    span = find(source,name)
    if not span:
        return []
    clean = _blank(source[span[0]:span[1]])
    structs = set(re.findall(r'\btypedef\s+struct(?:\s+'+ID+r')?\s*{[^{}]*}\s*('+ID+r')\s*;', _blank(header), re.S))
    signature = re.search(r'\b'+re.escape(name)+r'\s*\(([^()]*)\)',clean)
    hints = []
    if signature:
        aggregates = [arg.strip() for arg in _split_top_level(signature[1])
                      if '*' not in arg and any(re.search(r'\b'+re.escape(typ)+r'\b',arg) for typ in structs)]
        if aggregates:
            hints.append('Reference ABI: aggregate by-value parameter(s): '+', '.join(aggregates)+'. Preserve the actual type and calling convention; do not replace it with guessed scalar arguments.')
    for typ in structs:
        for local in re.findall(r'\b'+re.escape(typ)+r'\s+('+ID+r')\s*;',clean):
            save = re.search(r'\b'+re.escape(local)+r'\s*=\s*\*\s*('+ID+r')\s*;',clean)
            if save and re.search(r'\*\s*'+re.escape(save[1])+r'\s*=\s*'+re.escape(local)+r'\s*;',clean[save.end():]):
                hints.append(f'Reference aggregate snapshot: {typ} {local} saves/restores *{save[1]}. Preserve the complete original type, size and alignment; partial field copies can miss observable state.')
    if candidate and hints:
        hints.append('Compare these reference operations with the saved draft before further permutation. These are diagnostics, not an inferred layout or match verdict.')
    return hints[:4]
