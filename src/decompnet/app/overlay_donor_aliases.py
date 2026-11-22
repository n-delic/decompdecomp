"""Attribute ambiguous loader relocations to individual uses in private donor C."""

import re
from collections import defaultdict

from .. import seeds
from ..arch.mips_source_symbols import HIGH, JUMP, LOW, local_call_names, signed
from ..compiler import SourceRejected
from ..forks import csource
from ..network import NetworkError
from . import donor_sources
from .source_symbol_recovery import comparable_overlay_code, rename

MAX_PROBES = 16


def _cpp_declaration(header, clean, symbol):
    """Find an external function or data declaration in a C linkage block."""
    for block in re.finditer(r'\bextern\s+"C"\s*\{([^{}]*)\}', header):
        if clean[block.start():block.start() + len("extern")] != "extern":
            continue
        for decl in seeds.declarations(block[1]):
            if (symbol in decl.names and len(decl.names) == 1
                    and (re.fullmatch(r'[^;{}=#]+[)];', decl.text, re.S)
                         or _DATA.fullmatch(csource._blank(decl.text).strip()))):
                return 'extern "C" ' + decl.text.removeprefix('extern ').lstrip(), block.end()
    return None


def local_functions(source, header, name, obj, candidate, target):
    """Externalize direct calls to identifiable functions in the donor text section."""
    if target.arch != 'mips' or not target.linker_relocs or not comparable_overlay_code(candidate, target):
        return None
    expected = {r.offset: r.symbol for r in target.linker_relocs if r.type == JUMP}
    calls = local_call_names(obj, name, candidate)
    if not calls or calls.keys() - expected.keys():
        return None
    aliases = {}
    for offset, original in calls.items():
        destination = expected[offset]
        if not all(re.fullmatch(r'[A-Za-z_]\w*', symbol) for symbol in (original, destination)):
            return None
        if aliases.setdefault(original, destination) != destination:
            return None

    try:
        body, context = donor_sources.compact(header + '\n' + source, name)
    except (NetworkError, SourceRejected):
        return None
    prototyped = set()
    declarations = []
    for decl in seeds.declarations(context):
        changed = decl.names & aliases.keys()
        if not changed:
            declarations.append(decl.text)
            continue
        if len(decl.names) != 1:
            return None
        symbol, = changed
        clean = csource._blank(decl.text)
        if not re.fullmatch(r'[^;{}=#]*\b' + symbol + r'\s*\([^;{}=#]*\)\s*;', clean, re.S):
            return None
        if re.search(r'\b(?:typedef|inline|__inline|__inline__)\b', clean):
            return None
        prototyped.add(symbol)
        storage = re.match(r'\s*(static)\b', clean)
        if storage:
            start, end = storage.span(1)
            declarations.append(decl.text[:start] + 'extern' + decl.text[end:])
        else:
            declarations.append(decl.text)
    if prototyped != aliases.keys():
        return None
    return rename(body, aliases), rename('\n'.join(declarations), aliases)


def retry(source, header, name, candidate, target, measure, stop):
    """Return one rewrite, supported by compiler probes, for the ordinary exact judge."""
    if not comparable_overlay_code(candidate, target):
        if target.arch != 'mips' or not comparable_overlay_code(candidate, target, masked=_data_fields(target)):
            return None
    actual = {(r.offset, r.type): r.symbol for r in candidate.relocs}
    expected = {(r.offset, r.type): r.symbol for r in target.relocs}
    if (set(actual) != set(expected) or len(actual) != len(candidate.relocs)
            or len(expected) != len(target.relocs)):
        return None
    before = {(r.offset, r.type): r for r in candidate.relocs}
    after = {(r.offset, r.type): r for r in target.relocs}
    data_changes = {
        r.symbol for site, r in before.items()
        if target.arch == 'mips' and r.type in (HIGH, LOW) and r.addend != after[site].addend
    }
    replacements = defaultdict(set)
    for site, symbol in actual.items():
        replacements[symbol].add(expected[site])
    if not data_changes and not any(len(names) > 1 for names in replacements.values()):
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
    types = {}
    originals = {}
    for symbol, names in replacements.items():
        if names == {symbol} and symbol not in data_changes:
            continue
        if not all(re.fullmatch(r'[A-Za-z_]\w*', word) for word in {symbol, *names}):
            return None
        if len(names) == 1 and symbol not in data_changes:
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
        if symbol in data_changes:
            plain = csource._blank(declaration.removeprefix('extern "C" ')).strip()
            if not plain.startswith('extern '):
                plain = 'extern ' + plain
            types[symbol] = _DATA.fullmatch(plain)
            if types[symbol] is None:
                return None
        originals[symbol] = declaration, start
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
    def with_declarations(edits=()):
        text = header
        changes = [(start, start, decl) for start, decl in extras]
        changes.extend(edits)
        for start, end, replacement in sorted(changes, reverse=True):
            text = text[:start] + replacement + text[end:]
        return text

    context = with_declarations()
    edits = []
    for in_header, symbol, start, end, names in probes:
        if stop.is_set():
            return None
        probe = next(iter(sorted(names - {symbol})), None)
        temporary = None
        if probe is None:
            probe = f'decomp_donor_probe_{len(edits)}'
            pattern = re.compile(r'\b' + probe + r'\b')
            if pattern.search(clean_header) or pattern.search(clean_source):
                return None
            original, insert_at = originals[symbol]
            temporary = insert_at, '\n' + rename(original, {symbol: probe}) + '\n'
        probe_source = source if in_header else source[:start] + probe + source[end:]
        probe_edits = [(start, end, probe)] if in_header else []
        if temporary:
            insert_at, decl = temporary
            probe_edits.append((insert_at, insert_at, decl))
        probe_context = with_declarations(probe_edits) if probe_edits else context
        result = measure(probe_source, probe_context)
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
        alias = aliases.pop()
        if symbol in data_changes:
            delta = _offset_delta([before[site] for site in changed], after)
            if delta is None:
                return None
            if delta:
                alias = _offset_expression(types[symbol], alias, delta)
        edits.append((in_header, start, end, alias))
    for in_header, start, end, alias in sorted(edits, reverse=True):
        if not in_header:
            source = source[:start] + alias + source[end:]
    header_edits = [(start, end, alias) for in_header, start, end, alias in edits if in_header]
    return (rename(source, direct), rename(with_declarations(header_edits), direct)) if edits else None


_DATA = re.compile(
    r'extern\s+([A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*)\s+(\**\s*)([A-Za-z_]\w*)'
    r'\s*((?:\[\s*\d*\s*\]\s*)*)\s*;')


def _data_fields(target):
    return [*target.linker_relocs, *(r for r in target.relocs if r.type in (HIGH, LOW))]


def _offset_delta(relocations, expected):
    differences = defaultdict(set)
    for relocation in relocations:
        wanted = expected[relocation.offset, relocation.type].addend
        actual = relocation.addend
        kind = relocation.type
        delta = signed(wanted) - signed(actual) if kind == LOW else wanted - actual
        differences[kind].add(delta)
    if not any(delta for kind in (HIGH, LOW) for delta in differences.get(kind, ())):
        return 0
    if set(differences) != {HIGH, LOW} or any(len(group) != 1 for group in differences.values()):
        return None
    return (next(iter(differences[HIGH])) << 16) + next(iter(differences[LOW]))


def _offset_expression(declaration, alias, delta):
    kind, pointers, _, dimensions = declaration.groups()
    cast = kind + ' ' + pointers + (f'(*){dimensions}' if dimensions else '*')
    return f'(*({cast})((unsigned char *)&{alias} + ({delta})))'


def offset_retry(source, header, name, candidate, target):
    """Rebase an explicitly declared object by a consistent compiled HI16/LO16 delta."""
    from .source_symbol_recovery import overlay_bindings

    if target.arch != 'mips':
        return None
    aliases = overlay_bindings(candidate, target, masked=_data_fields(target))
    if not aliases:
        return None
    expected = {(r.offset, r.type): r for r in target.relocs}
    groups = defaultdict(list)
    for relocation in candidate.relocs:
        groups[relocation.symbol].append(relocation)
    offsets = {symbol: delta for symbol, sites in groups.items() if (delta := _offset_delta(sites, expected))}
    if not offsets:
        return None

    bound = {}
    declarations = []
    clean_header = csource._blank(header)
    for decl in seeds.declarations(header):
        match = _DATA.fullmatch(csource._blank(decl.text).strip())
        if not match or match[3] not in offsets:
            continue
        symbol = match[3]
        alias = aliases[symbol]
        if alias != symbol and re.search(r'\b' + re.escape(alias) + r'\b', clean_header):
            continue
        if alias != symbol:
            declarations.append(f'extern unsigned char {alias}[];')
        bound[symbol] = _offset_expression(match, alias, offsets[symbol])
    span = csource.find(source, name)
    if not bound or span is None:
        return None
    start, end = span
    direct = {old: new for old, new in aliases.items() if old not in bound}
    source = rename(source[:start], direct) + rename(source[start:end], direct | bound) + rename(source[end:], direct)
    return source, rename(header, direct) + '\n' + '\n'.join(dict.fromkeys(declarations)) + '\n'
