"""Attribute ambiguous loader relocations to individual uses in private donor C."""

import re
from collections import defaultdict

from .. import seeds
from ..forks import csource
from .source_symbol_recovery import comparable_overlay_code, rename

MAX_PROBES = 16


def _cpp_declaration(header, clean, symbol):
    """Find simple declarations in a C linkage block above inlined C++ callers."""
    for block in re.finditer(r'\bextern\s+"C"\s*\{([^{}]*)\}', header):
        if clean[block.start():block.start() + len("extern")] != "extern":
            continue
        for decl in seeds.declarations(block[1]):
            if (symbol in decl.names and len(decl.names) == 1
                    and re.fullmatch(r'[^;{}=#]+[)];', decl.text, re.S)):
                return 'extern "C" ' + decl.text.removeprefix('extern ').lstrip(), block.end()
    return None


def retry(source, header, name, candidate, target, measure, stop):
    """Return one rewrite, supported by compiler probes, for the ordinary exact judge."""
    if not comparable_overlay_code(candidate, target):
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
    clean_source = csource._blank(source)
    clean_header = csource._blank(header)
    prototype = re.compile(r'[^;{}=#]+\([^;{}=#]*\)\s*;', re.S)
    external = re.compile(r'extern\b[^;{}=#]+;', re.S)
    declarations = seeds.declarations(header)
    probes = []
    extras = set()
    direct = {}
    for symbol, names in replacements.items():
        if names == {symbol}:
            continue
        if not all(re.fullmatch(r'[A-Za-z_]\w*', word) for word in {symbol, *names}):
            return None
        if len(names) == 1:
            direct[symbol] = next(iter(names))
            continue
        # Only duplicate a declaration, never a definition or initializer.
        declaration = next((d.text for d in declarations if symbol in d.names
                            and not re.match(r'(?:static|typedef)\b', d.text)
                            and (external.fullmatch(d.text) or prototype.fullmatch(d.text))), None)
        start = len(header)
        if declaration is None:
            linked = _cpp_declaration(header, clean_header, symbol)
            if linked is None:
                return None
            declaration, start = linked
        additions = '\n' + '\n'.join(sorted(rename(declaration, {symbol: alias}) for alias in names - {symbol})) + '\n'
        extras.add((start, additions))
        for occurrence in re.finditer(r'\b' + re.escape(symbol) + r'\b', clean_source):
            if span[0] <= occurrence.start() < span[1]:
                probes.append((False, symbol, occurrence.start(), occurrence.end(), names))
        if start < len(header):
            for occurrence in re.finditer(r'\b' + re.escape(symbol) + r'\b', clean_header):
                if occurrence.start() >= start:
                    probes.append((True, symbol, occurrence.start(), occurrence.end(), names))
    if not 0 < len(probes) <= MAX_PROBES:
        return None
    def with_declarations(text):
        for start, text_to_insert in sorted(extras, reverse=True):
            text = text[:start] + text_to_insert + text[start:]
        return text

    context = with_declarations(header)
    edits = []
    for in_header, symbol, start, end, names in probes:
        if stop.is_set():
            return None
        probe = next(iter(sorted(names - {symbol})))
        if in_header:
            result = measure(source, with_declarations(header[:start] + probe + header[end:]))
        else:
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
        edits.append((in_header, start, end, aliases.pop()))
    for in_header, start, end, alias in sorted(edits, reverse=True):
        if in_header:
            header = header[:start] + alias + header[end:]
        else:
            source = source[:start] + alias + source[end:]
    return (rename(source, direct), rename(with_declarations(header), direct)) if edits else None
