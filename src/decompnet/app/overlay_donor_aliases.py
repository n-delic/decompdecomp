"""Attribute ambiguous loader relocations to individual uses in private donor C."""

import re
from collections import defaultdict

from .. import seeds
from ..forks import csource
from .source_symbol_recovery import rename

MAX_PROBES = 16


def retry(source, header, name, candidate, target, measure, stop):
    """Return one rewrite, supported by compiler probes, for the ordinary exact judge."""
    if candidate is None or candidate.code != target.code:
        return None
    actual = {(r.offset, r.type): r.symbol for r in candidate.relocs}
    expected = {(r.offset, r.type): r.symbol for r in target.relocs}
    if (set(actual) != set(expected) or len(actual) != len(candidate.relocs)
            or len(expected) != len(target.relocs)):
        return None
    replacements = defaultdict(set)
    for site, symbol in actual.items():
        replacements[symbol].add(expected[site])
    if not any(len(names) > 1 for names in replacements.values()):
        return None
    span = csource.find(source, name)
    if not span:
        return None
    clean = csource._blank(source)
    declarations = seeds.declarations(header)
    probes = []
    extras = set()
    for symbol, names in replacements.items():
        if names == {symbol}:
            continue
        if not all(re.fullmatch(r'[A-Za-z_]\w*', word) for word in {symbol, *names}):
            return None
        # Only duplicate a declaration, never a definition or initializer.
        declaration = next((d.text for d in declarations if symbol in d.names
                            and re.fullmatch(r'extern\b[^;{}=#]+;', d.text, re.S)), None)
        if declaration is None:
            return None
        extras.update(rename(declaration, {symbol: alias}) for alias in names - {symbol})
        for occurrence in re.finditer(r'\b' + re.escape(symbol) + r'\b', clean,):
            if span[0] <= occurrence.start() < span[1]:
                probes.append((symbol, occurrence.start(), occurrence.end(), names))
    if not 0 < len(probes) <= MAX_PROBES:
        return None
    context = header + '\n' + '\n'.join(sorted(extras)) + '\n'
    edits = []
    for symbol, start, end, names in probes:
        if stop.is_set():
            return None
        probe = next(iter(sorted(names - {symbol})))
        result = measure(source[:start] + probe + source[end:], context)
        if result is None or result.code != candidate.code or len(result.relocs) != len(actual):
            return None
        current = {(r.offset, r.type): r.symbol for r in result.relocs}
        if current.keys() != actual.keys():
            return None
        changed = {site for site in actual if current[site] != actual[site]}
        if not changed:
            continue  # a source occurrence optimized away
        if any(actual[site] != symbol or current[site] != probe for site in changed):
            return None
        aliases = {expected[site] for site in changed}
        if len(aliases) != 1:
            return None
        edits.append((start, end, aliases.pop()))
    for start, end, alias in sorted(edits, reverse=True):
        source = source[:start] + alias + source[end:]
    return (source, context) if edits else None
