"""Compile-guided C literal/shift proposals; all results still require the exact byte judge."""

import itertools

from ..arch.mips_literals import changes

MAX_VARIANTS, MAX_OCCURRENCES = 8, 4


def variants(source, candidate, target, big):
    from .similar_matches import INTEGER, TOKENS, relocations

    relocated = {off for off,_,_ in relocations(candidate)} | {off for off,_,_ in relocations(target)}
    evidence = changes(candidate['target'] or b'', target['target'] or b'', relocated, big)
    masked = TOKENS.sub(lambda t: ' ' * len(t.group()) if t.group().startswith(('/*','//','"',"'")) else t.group(), source)
    groups = []
    for kind,old,new,count in evidence:
        edits = []
        for token in TOKENS.finditer(source):
            m = INTEGER.fullmatch(token.group())
            if not m:
                continue
            try:
                value = int(m[1],16 if m[1].lower().startswith('0x') else 8 if len(m[1]) > 1 and m[1].startswith('0') else 10)
            except ValueError:
                continue  # disabled preprocessor branches can contain tokens that are not valid C literals
            before,after = masked[:token.start()].rstrip(),masked[token.end():].lstrip()
            if before.endswith('-'):
                continue
            replacement = None
            if kind == 'literal' and value == old:
                replacement = new
            elif kind in ('left','right'):
                operator = '<<' if kind == 'left' else '>>'
                if value == old and before.endswith(operator):
                    replacement = new
                elif value == 1 << old:
                    # Shifts often come from multiply/divide expressions in the donor C.
                    if kind == 'left' and (before.endswith('*') or after.startswith('*')):
                        replacement = 1 << new
                    elif kind == 'right' and before.endswith('/'):
                        replacement = 1 << new
            if replacement is not None:
                edits.append((token.start(),token.end(),str(replacement) + m[2]))
        if not edits:
            continue
        # Repeated occurrences are separate hypotheses. Trying all occurrences is useful for a mask
        # used by several instructions, but is never an unverified global source rewrite.
        options = [[edit] for edit in edits[:MAX_OCCURRENCES]]
        if 1 < len(edits) <= MAX_OCCURRENCES:
            options.insert(0 if count > 1 else len(options), edits)
        groups.append(options)
    if not groups:
        return []
    proposals = []
    # Try combined evidence first, then single edits. Bound the cartesian product before materializing it.
    proposals.extend(list(itertools.islice(itertools.product(*groups), MAX_VARIANTS)))
    proposals.extend((option,) for group in groups for option in group)
    result = []
    for proposal in proposals:
        edits = {edit[0]:edit for option in proposal for edit in option}
        # Different evidence must not prescribe two values for the same occurrence.
        if sum(len(option) for option in proposal) != len(edits):
            continue
        text = source
        for start,end,replacement in sorted(edits.values(),reverse=True):
            text = text[:start] + replacement + text[end:]
        if text != source and text not in result:
            result.append(text)
            if len(result) == MAX_VARIANTS:
                break
    return result
