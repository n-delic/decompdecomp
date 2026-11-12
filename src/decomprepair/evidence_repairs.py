"""Bounded source hypotheses from reviewed repairs; each still needs the ordinary exact judge."""
import re

from .integer_repair_support import IDENT, INTEGER, edit, view
from .pattern_repairs import close

LIMIT = 12


def variants(source, name, header=''):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b', part.clean):
        return []
    result = []
    # A slot address can be kept live before its pointer is loaded. Only move it earlier when
    # the header proves an actual array: loading a mutable table pointer early is not equivalent.
    for hit in re.finditer(rf'\b({IDENT})\s*=\s*({IDENT})\s*\[\s*({IDENT})\s*\]\s*\+', part.body):
        row, table, index = hit.groups()
        decl = re.search(rf'\b((?:u8|s8|char|unsigned\s+char)\s*\*)\s*{row}\s*;', part.body)
        if not decl or re.search(rf'\b{table}\s*\[', part.body[:hit.start()]):
            continue
        start = part.opening+1+hit.start()
        prefix = part.clean[part.opening+1:start].rstrip()
        if not prefix or prefix[-1] not in ';{}':
            continue
        fresh = '__repair_table_slot'
        if re.search(rf'\b{fresh}\b', part.clean):
            continue
        load = re.search(rf'\b{table}\s*\[\s*{index}\s*\]', part.clean[start:])
        if not load:
            continue
        positions = [start]
        array = re.search(rf'(?:^|;)\s*(?:extern\s+)?(?:u8|s8|char|unsigned\s+char)\s*\*\s*{re.escape(table)}\s*\[[^;]*\]\s*;', header+'\n'+source[:part.opening])
        local_index = re.search(rf'\b{INTEGER}\s+{index}\s*;', prefix)
        parameters = part.clean[:part.opening].rsplit(name,1)[-1]
        shadow = re.search(rf'\b{table}\b', parameters) or re.search(rf'\b{IDENT}\s+\*+\s*{table}\b', part.body)
        if array and local_index and not shadow and not re.search(rf'&\s*{index}\b', part.body) and not re.search(r'\b(?:goto|for|while|do|if|switch)\b', prefix):
            writes = list(re.finditer(rf'\b{index}\s*=(?!=)[^;{{}}]+;', prefix))
            if writes:
                pos = part.opening+1+writes[-1].end()
                gap = part.clean[pos:start]
                if not re.search(rf'\b{index}\s*(?:[+\-*/%&|^]?=|\+\+|--)|(?:\+\+|--)\s*{index}\b', gap):
                    positions.insert(0, pos)
        for pos in dict.fromkeys(positions):
            result.append(('table_slot_lifetime', edit(source, [
                (part.opening+1, part.opening+1, f'\n{decl[1]}*{fresh};\n'),
                (pos, pos, f'\n{fresh} = &{table}[{index}];\n'),
                (start+load.start(), start+load.end(), f'(*{fresh})')])) )
        break
    # Reuse a later word temporary while preserving this expression's unsigned value. The next
    # original use must overwrite it, so the new assignment cannot replace a live value.
    for decl in re.finditer(rf'\b(s32|int)\s+({IDENT})\s*;', part.body):
        target = decl[2]
        if re.search(rf'&\s*{target}\b', part.body):
            continue
        for expression in re.finditer(rf'\b({IDENT})\s*-\s*({IDENT})\s*;', part.body):
            operand = expression[1]
            if operand == target or not re.search(rf'\bu32\s+{operand}\s*=', part.body[:expression.start()]):
                continue
            remaining = part.body[expression.end():]
            following = re.search(rf'\b{target}\b', remaining)
            if not following or not re.match(r'\s*=(?!=)', remaining[following.end():]):
                continue
            if re.search(rf'\b{target}\b', part.body[decl.end():expression.start()]):
                continue
            offset = part.opening+1
            result.append(('unsigned_assignment_reuse', edit(source, [
                (offset+decl.start(1), offset+decl.end(1), 'u32'),
                (offset+expression.start(1), offset+expression.end(1), f'({target} = {operand})')])) )
            break
    # Materialize the successful guard as a forward jump. Keep the body scoped, and require both
    # paths to return so moving the trailing return ahead of the body cannot introduce fallthrough.
    for hit in re.finditer(r'\bif\s*\(', part.body):
        begin = part.opening+1+hit.start()
        cond_end = close(part.clean, part.opening+1+hit.end()-1)
        arm = re.match(r'\s*{', part.clean[cond_end:]) if cond_end else None
        opening = cond_end+arm.end()-1 if arm else None
        end = close(part.clean, opening, '{', '}') if opening is not None else None
        tail = re.match(r'\s*(return\s+[^;{}]+;)', part.clean[end:]) if end else None
        if not tail or part.clean[end+tail.end():part.end-1].strip():
            continue
        body = part.clean[opening+1:end-1]
        terminal = re.search(r'\breturn\b[^;{}]*;\s*$', body)
        before = body[:terminal.start()].rstrip() if terminal else ''
        if not terminal or (before and before[-1] not in ';}') or before.count('{') != before.count('}') or re.search(r'\b(?:goto|case|default)\b|(?<!:):(?!:)', body):
            continue
        label = '__repair_guard_body'
        if re.search(rf'\b{label}\b', part.clean):
            continue
        guard = source[begin:cond_end]
        text = f'{guard} {{ goto {label}; }}\n{source[end+tail.start(1):end+tail.end(1)]}\n{label}: '+source[opening:end]
        result.append(('guard_forward_jump', edit(source, [(begin,end+tail.end(),text)])))
        break
    return list(dict.fromkeys(result))[:LIMIT]
