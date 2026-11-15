"""Try separate pointer temporaries in returning branches; the exact judge remains authoritative."""

import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'


def variant(source: str, name: str) -> str | None:
    span = find(source, name)
    if not span:
        return None
    clean = _blank(source)
    opening = clean.find('{', span[0], span[1])
    if opening < 0 or '#' in clean[opening:span[1]]:
        return None
    # Only ordinary pointer locals declared directly at the start of the function. Not parameters,
    # volatile variables, initialized declarations, aggregates, function pointers or qualified types.
    declarations = re.match(r'(?:\s*(?:[A-Za-z_]\w*\s+)+\**\s*\w+\s*;)*', clean[opening + 1:span[1]])[0]
    pointers = {m[2]: m[1] for m in re.finditer(
        rf'(?:^|;)\s*((?:void|char|u8|s8|s32|u32)\s*\*)\s*({IDENT})\s*(?=;)', declarations)}
    if not pointers:
        return None
    edits, used, covered = [], set(re.findall(IDENT, clean)), -1
    for test in re.finditer(r'\bif\s*\(', clean[opening:span[1]]):
        pos = opening + test.end(); depth = 1
        while pos < span[1] and depth:
            depth += (clean[pos] == '(') - (clean[pos] == ')'); pos += 1
        while pos < span[1] and clean[pos].isspace():
            pos += 1
        if pos >= span[1] or clean[pos] != '{' or pos < covered:
            continue
        end, depth = pos + 1, 1
        while end < span[1] and depth:
            depth += (clean[end] == '{') - (clean[end] == '}'); end += 1
        body = clean[pos + 1:end - 1]
        if depth or not re.search(r'\breturn\s*(?:[^;{}]+)?;\s*$', body) or re.search(r'\bgoto\b|\w\s*:', body):
            continue
        declared, replacements = [], []
        for var, typ in pointers.items():
            if not re.search(rf'\b{re.escape(var)}\s*=\s*{IDENT}\s*;', body):
                continue
            # Taking the local's address could let it escape the branch: changing its storage is unsafe.
            if re.search(r'&\s*' + re.escape(var) + r'\b', body):
                continue
            fresh = '__branch_' + var
            while fresh in used:
                fresh += '_'
            used.add(fresh)
            declared.append(f'{typ}{fresh};')
            for token in re.finditer(r'\b' + re.escape(var) + r'\b', body):
                if body[:token.start()].rstrip().endswith(('.', '->')):
                    continue
                replacements.append((pos + 1 + token.start(), pos + 1 + token.end(), fresh))
        if declared:
            edits.extend(replacements)
            edits.append((pos + 1, pos + 1, '\n' + '\n'.join(declared) + '\n'))
            covered = end
    if not edits:
        return None
    result = source
    for start, end, replacement in sorted(edits, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result
