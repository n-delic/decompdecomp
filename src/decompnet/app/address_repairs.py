"""Propose bounded address corrections from aligned compiler feedback; only the byte judge accepts them."""

import re
from collections import defaultdict

from ..rom.symbols import AUTO_NAME

SYMBOL = re.compile(r'%(hi|lo)\(([A-Za-z_]\w*)\)')


def variants(source, report, target, big, known_symbols):
    """Require agreeing HI/LO evidence and resolvable destination names, then combine with literal edits.

    Literal addends can use evidenced pointer strides. Changed instruction layouts and ambiguous mappings
    are left alone.
    These are candidate-local hypotheses: never rename the project's symbols or change shared declarations.
    """
    from .addend_repairs import variants as addend_variants
    from .argument_repairs import variants as argument_variants
    from .constant_repairs import variants as constant_variants
    from .global_addresses import alias_variant, normalize
    from .layout_repairs import variants as layout_variants
    from .pointer_steps import variants as step_variants
    from .raw_address_repairs import variants as raw_variants
    from .similar_matches import INTEGER, TOKENS, literal_variants

    # Raw target words remain useful even when cached symbolic feedback is misleading.
    # Put bounded expression-local singles/pairs before label-based guesses.
    raw = [text for _, text in raw_variants(source, target['name'], 'mips', target['asm'])] if {'name','asm'} <= set(target.keys()) else []
    report = normalize(report)
    votes = defaultdict(set)
    halves = defaultdict(set)
    for _, wanted, compiled in report.get('rows', []):
        a, b = SYMBOL.search(wanted), SYMBOL.search(compiled)
        if not a or not b or a[1] != b[1]:
            continue
        if SYMBOL.sub('%address', wanted) != SYMBOL.sub('%address', compiled):
            continue
        old, new = b[2], a[2]
        votes[old].add(new)
        halves[old, new].add(a[1])
    names = {old: next(iter(values)) for old, values in votes.items()
             if len(values) == 1 and old not in values and (next(iter(values)) in known_symbols or AUTO_NAME.fullmatch(next(iter(values))))
             and halves[old, next(iter(values))] == {'hi', 'lo'}}
    addends = addend_variants(source, report)
    if len(names) > 2:
        names = {}
    # Token replacement preserves comments, strings and identifier substrings, and handles swaps simultaneously.
    addresses = {}
    for old, new in names.items():
        a, b = AUTO_NAME.fullmatch(old), AUTO_NAME.fullmatch(new)
        if a and b and int(a[1], 16) >= 0x10000000:
            addresses.setdefault(int(a[1], 16), set()).add(int(b[1], 16))
    def replace(token):
        text = token.group()
        if text in names:
            return names[text]
        number = INTEGER.fullmatch(text)
        if number:
            value = int(number[1], 16 if number[1].lower().startswith('0x') else 10)
            targets = addresses.get(value, set())
            if len(targets) == 1:
                return hex(next(iter(targets))) + number[2]
        return text
    renamed = TOKENS.sub(replace, source)
    alias = alias_variant(source, report)
    result = ([renamed] if renamed != source else []) + addends + ([alias] if alias else [])
    if renamed != source:
        result.extend(addend_variants(renamed, report))
    if report.get('candidate_target'):
        candidate = {'target': bytes.fromhex(report['candidate_target']), 'relocs': report.get('candidate_relocs', '[]')}
        for variant in list(result):
            result.extend(literal_variants(variant, candidate, target, big))
            result.extend(constant_variants(variant, candidate, target, big))
        if not result:
            result.extend(constant_variants(source, candidate, target, big))
    for variant in list(result) or [source]:
        result.extend(argument_variants(variant, report, target, big))
    layouts = layout_variants(source, report, target, big)
    for variant in list(result):
        layouts.extend(layout_variants(variant, report, target, big))
    steps = step_variants(source, report, target, big)
    for variant in list(result):
        steps.extend(step_variants(variant, report, target, big))
    # A complete, agreeing HI/LO mapping repairs donor globals as one bundle.
    # Reserve its slot before raw-address guesses consume the bounded allowance.
    mapped = [renamed] if renamed != source else []
    return list(dict.fromkeys(mapped + raw + result + steps + layouts))[:8]
