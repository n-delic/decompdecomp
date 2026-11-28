"""Prove saved native-module sources against newly recovered loader bindings."""

import json
import re
from collections import Counter, defaultdict

from ...app import overlay_donor_aliases
from ...app.function_names import rename_identifiers
from ...forks import csource
from ...network import NetworkError
from .uso_match import alias, local_alias


def _macro_variant(fn, source, header, candidate, info, extra):
    """Separate placeholder calls only when every occurrence has one recorded JAL."""
    span = csource.find(source, fn['name'])
    if span is None:
        return None
    clean = csource._blank(source)
    calls = list(re.finditer(r'\b(?:(?:CALL|FCALL)\s*\(\s*[A-Za-z_]\w*\s*\)|D_80000000(?=\s*\())',
                             clean[span[0]:span[1]]))
    calls = [(span[0] + match.start(), span[0] + match.end()) for match in calls]
    relocs = []
    target = fn['target']
    for at, kind, index in info.get('relocs') or ():
        if kind == 4 and 0 <= at and at + 4 <= len(target) and int.from_bytes(target[at:at + 4], 'big') >> 26 == 3:
            relocs.append((at, index))
    if not calls or len(calls) != len(relocs):
        return None
    actual = {(reloc.offset, reloc.type): reloc.symbol for reloc in candidate.relocs}
    if any(actual.get((at, 4)) != 'D_80000000' for at, _ in relocs):
        return None
    pattern = r'(?m)^[ \t]*extern[ \t]+[^;{}#\n]+\bD_80000000[ \t]*\([^;{}#\n]*\)[ \t]*;'
    declaration = re.search(pattern, csource._blank(header))
    # Old C can call an undeclared function with the compiler's implicit int type.
    # The complete judge below must still prove any rewrite using this fallback.
    prototype = header[declaration.start():declaration.end()] if declaration else 'extern int D_80000000();'
    local = {(at, index): offset for at, index, offset in info.get('local_calls', ())}
    bindings = [local_alias(info['section'], index, local[(at, index)]) if (at, index) in local
                else alias(info['section'], index) for at, index in sorted(relocs)]
    # The port build gives these call macros their original symbolic callees.
    # Keep each original expression there while the ROM build uses its binding.
    names = []
    preprocessor = csource._blank(header + '\n' + source)
    for index, ((start, end), binding) in enumerate(zip(calls, bindings), 1):
        name = f'decomp_overlay_call_{index}'
        if re.search(r'\b' + re.escape(name) + r'\b', preprocessor):
            return None
        names.append((name, source[start:end], binding))
    for (start, end), (name, _, _) in reversed(list(zip(calls, names))):
        source = source[:start] + name + source[end:]
    declarations = [rename_identifiers(prototype, {'D_80000000': binding}) for binding in sorted(set(bindings))]
    port = '\n'.join(f'#define {name} {original}' for name, original, _ in names)
    rom = '\n'.join(f'#define {name} {binding}' for name, _, binding in names)
    return '\n'.join(declarations) + '\n#ifdef PORT\n' + port + '\n#else\n' + rom + '\n#endif\n' + source + '\n' + extra


def _data_variant(fn, source, header, candidate, target):
    """Separate marked data sites when each has one recorded low relocation."""
    span = csource.find(source, fn['name'])
    if span is None:
        return None
    clean = csource._blank(source)
    pattern = r'\bXDATA\s*\(\s*([A-Za-z_]\w*)\s*,\s*[A-Za-z_]\w*\s*,\s*(?:0[xX][0-9a-fA-F]+|[0-9]+)\s*\)'
    sites = list(re.finditer(pattern, clean[span[0]:span[1]]))
    definitions = csource._blank(header + '\n' + source)
    for site in sites:
        token = site.group(1)
        if token == 'D_00000000':
            continue
        pattern = r'(?m)^\s*#\s*define\s+' + re.escape(token) + r'\s+([^\n]*)$'
        values = re.findall(pattern, definitions)
        if not values or any(value.strip() != 'D_00000000' for value in values):
            return None
        if re.search(r'(?m)^\s*#\s*undef\s+' + re.escape(token) + r'\b', definitions):
            return None
    sites = [(span[0] + m.start(), span[0] + m.end(), span[0] + m.start(1), span[0] + m.end(1)) for m in sites]
    low = sorted((r for r in candidate.relocs if r.type == 6 and r.symbol == 'D_00000000'),
                 key=lambda r: r.offset)
    if not sites or len(sites) != len(low):
        return None
    expected = {(r.offset, r.type): r for r in target.relocs}
    wanted = [expected.get((r.offset, r.type)) for r in low]
    if (any(r.addend for r in low) or any(r is None or r.addend or not re.fullmatch(r'[A-Za-z_]\w*', r.symbol)
                                          for r in wanted)):
        return None
    pattern = r'(?m)^[ \t]*extern[ \t]+[^;{}#\n]+\bD_00000000[ \t]*;'
    declaration = re.search(pattern, csource._blank(header))
    if declaration is None:
        return None
    prototype = header[declaration.start():declaration.end()]
    bindings = [r.symbol for r in wanted]
    wrappers = []
    for index, ((start, end, token_start, token_end), binding) in enumerate(zip(sites, bindings), 1):
        name = f'decomp_overlay_data_{index}'
        if re.search(r'\b' + re.escape(name) + r'\b', definitions):
            return None
        original = source[start:end]
        rewritten = source[start:token_start] + binding + source[token_end:end]
        wrappers.append((name, original, rewritten))
    for (start, end, _, _), (name, _, _) in reversed(list(zip(sites, wrappers))):
        source = source[:start] + name + source[end:]
    declarations = [rename_identifiers(prototype, {'D_00000000': binding}) for binding in sorted(set(bindings))]
    port = '\n'.join(f'#define {name} {original}' for name, original, _ in wrappers)
    rom = '\n'.join(f'#define {name} {rewritten}' for name, _, rewritten in wrappers)
    return '\n'.join(declarations) + '\n#ifdef PORT\n' + port + '\n#else\n' + rom + '\n#endif\n' + source


def _direct_variant(fn, source, header, candidate, target, extra, *, prefer_decl=True, nested_first=False):
    """Separate direct placeholder calls only when every call has one relocation."""
    span = csource.find(source, fn['name'])
    if span is None:
        return None
    clean = csource._blank(source)
    pattern = r'\b(?:func_00000000|func_80000000|gl_func_00000000)(?=\s*\()'
    calls = list(re.finditer(pattern, clean[span[0]:span[1]]))
    calls = [(span[0] + m.start(), span[0] + m.end(), m.group()) for m in calls]
    if not calls:
        return None
    counts = Counter(symbol for _, _, symbol in calls)
    grouped = defaultdict(list)
    for reloc in candidate.relocs:
        if reloc.type == 4 and reloc.symbol in counts:
            grouped[reloc.symbol].append(reloc)
    if any(len(grouped[symbol]) != count for symbol, count in counts.items()):
        return None
    expected = {(r.offset, r.type): r for r in target.relocs}
    combined = header + '\n' + source
    clean_combined = csource._blank(combined)
    bindings, declarations, source_declarations = {}, [], {}
    closing = {}
    if nested_first:
        for start, end, _ in calls:
            opening = clean.find('(', end, span[1])
            depth = 0
            for at in range(opening, span[1]):
                if clean[at] == '(':
                    depth += 1
                elif clean[at] == ')':
                    depth -= 1
                    if depth == 0:
                        closing[start] = at
                        break
            if start not in closing:
                return None
    for symbol in counts:
        ordered = sorted(grouped[symbol], key=lambda r: r.offset)
        wanted = [expected.get((r.offset, r.type)) for r in ordered]
        if any(r is None or not re.fullmatch(r'[A-Za-z_]\w*', r.symbol) for r in wanted):
            return None
        pattern = (r'(?m)^[ \t]*(?!(?:return|if|while|for|switch)\b)'
                   r'[A-Za-z_]\w*(?:[ \t*]+[A-Za-z_]\w*)*[ \t*]+' + re.escape(symbol)
                   + r'[ \t]*\([^;{}#\n]*\)[ \t]*;')
        declaration = re.search(pattern, clean_combined) if prefer_decl else None
        prototype = combined[declaration.start():declaration.end()] if declaration else f'extern int {symbol}();'
        occurrences = [call for call in calls if call[2] == symbol]
        if nested_first:
            occurrences.sort(key=lambda call: closing[call[0]])
        bindings.update((call[0], r.symbol) for call, r in zip(occurrences, wanted))
        copies = [rename_identifiers(prototype, {symbol: r.symbol}) for r in wanted]
        if declaration and declaration.start() >= len(header) + 1:
            source_declarations[symbol] = (pattern, copies)
        else:
            declarations.extend(copies)
    replacements = []
    for index, (start, end, symbol) in enumerate(calls, 1):
        name = f'decomp_overlay_direct_{index}'
        if re.search(r'\b' + re.escape(name) + r'\b', clean_combined):
            return None
        replacements.append((start, end, name, symbol, bindings[start]))
    for start, end, name, _, _ in reversed(replacements):
        source = source[:start] + name + source[end:]
    for pattern, copies in source_declarations.values():
        declaration = re.search(pattern, csource._blank(source))
        if declaration is None:
            return None
        source = source[:declaration.end()] + '\n' + '\n'.join(sorted(set(copies))) + source[declaration.end():]
    port = '\n'.join(f'#define {name} {symbol}' for _, _, name, symbol, _ in replacements)
    rom = '\n'.join(f'#define {name} {binding}' for _, _, name, _, binding in replacements)
    return '\n'.join(sorted(set(declarations))) + '\n#ifdef PORT\n' + port + '\n#else\n' + rom + '\n#endif\n' + source + '\n' + extra


def _split_direct_data(fn, source, header, candidate, *, group_casts=False, relaxed=False):
    """Give individual placeholder data uses separate relocations for a private probe."""
    span = csource.find(source, fn['name'])
    if span is None:
        return None
    clean = csource._blank(source)
    body = clean[span[0]:span[1]]
    # XDATA can stringify its first argument in another build; leave those sites intact.
    if re.search(r'\bXDATA\s*\(', body):
        return None
    sites = list(re.finditer(r'\bD_00000000\b', body))
    sites = [(span[0] + m.start(), span[0] + m.end()) for m in sites]
    low = [r for r in candidate.relocs if r.type == 6 and r.symbol == 'D_00000000']
    if (not sites or not low or (group_casts or relaxed) and len(sites) > 32
            or not group_casts and not relaxed and len(sites) != len(low)):
        return None
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+([A-Za-z_]\w*)\s*\(', csource._blank(header + '\n' + source)))
    for start, _ in sites:
        depth = 0
        for at in range(start - 1, span[0] - 1, -1):
            if clean[at] == ')':
                depth += 1
            elif clean[at] == '(':
                if depth:
                    depth -= 1
                    continue
                caller = re.search(r'([A-Za-z_]\w*)\s*$', clean[span[0]:at])
                if caller:
                    name = caller.group(1)
                    if name in macros or (name.isupper() and not re.fullmatch(r'D_[0-9A-Fa-f]{8}', name)):
                        return None
    pattern = r'(?m)^[ \t]*extern[ \t]+[^;{}#\n]+\bD_00000000[ \t]*;'
    declaration = re.search(pattern, csource._blank(header))
    if declaration is None:
        return None
    prototype = header[declaration.start():declaration.end()]
    names = [f'decomp_overlay_data_ref_{index}' for index in range(1, len(sites) + 1)]
    if group_casts:
        # Repeated casts of the same placeholder can compile to fewer loads
        # than source occurrences. Keep each cast type together for the probe;
        # the complete byte and relocation judge decides whether it is valid.
        pattern = r'\(\s*((?:struct\s+)?[A-Za-z_]\w*)\s*\*\s*\)\s*\(\s*&\s*(D_00000000)\s*\)'
        groups = {}
        positions = {start: index for index, (start, _) in enumerate(sites)}
        for match in re.finditer(pattern, body):
            index = positions.get(span[0] + match.start(2))
            if index is not None:
                kind = ' '.join(match.group(1).split())
                names[index] = groups.setdefault(kind, names[index])
        if len(set(names)) == len(names):
            return None
    clean_all = csource._blank(header + '\n' + source)
    if any(re.search(r'\b' + re.escape(name) + r'\b', clean_all) for name in names):
        return None
    for (start, end), name in reversed(list(zip(sites, names))):
        source = source[:start] + name + source[end:]
    port = '\n'.join(f'#define {name} D_00000000' for name in dict.fromkeys(names))
    rom = '\n'.join(rename_identifiers(prototype, {'D_00000000': name}) for name in dict.fromkeys(names))
    return '#ifdef PORT\n' + port + '\n#else\n' + rom + '\n#endif\n' + source


def _split_local_pointer(fn, source, header):
    """Probe an indexed use of a local pointer initialized from the placeholder."""
    span = csource.find(source, fn['name'])
    if span is None:
        return None
    clean = csource._blank(source)
    body = clean[span[0]:span[1]]
    assignments = list(re.finditer(r'\b([A-Za-z_]\w*)\s*=\s*&\s*D_00000000\s*;', body))
    if len(assignments) != 1:
        return None
    variable = assignments[0].group(1)
    uses = list(re.finditer(r'\(\s*\(\s*int\s*\*\s*\)\s*(' + re.escape(variable)
                            + r')\s*\)\s*\[', body))
    if len(uses) != 1:
        return None
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+([A-Za-z_]\w*)\s*\(', csource._blank(header + '\n' + source)))
    start = span[0] + uses[0].start(1)
    depth = 0
    for at in range(start - 1, span[0] - 1, -1):
        if clean[at] == ')':
            depth += 1
        elif clean[at] == '(':
            if depth:
                depth -= 1
                continue
            caller = re.search(r'([A-Za-z_]\w*)\s*$', clean[span[0]:at])
            if caller and (caller.group(1) in macros or caller.group(1).isupper()):
                return None
    name, ref = 'decomp_overlay_local_site', 'decomp_overlay_local_ref'
    if re.search(r'\b(?:' + name + '|' + ref + r')\b', csource._blank(header + '\n' + source)):
        return None
    end = span[0] + uses[0].end(1)
    source = source[:start] + name + source[end:]
    return (f'#ifdef PORT\n#define {name} {variable}\n#else\n'
            f'extern int {ref};\n#define {name} (&{ref})\n#endif\n' + source)


def _grouped_data(fn, source, header, candidate):
    """Separate distinct marked aliases before probing their loader relocations."""
    span = csource.find(source, fn['name'])
    if span is None:
        return None
    clean = csource._blank(source)
    pattern = r'\bXDATA\s*\(\s*([A-Za-z_]\w*)\s*,\s*[A-Za-z_]\w*\s*,\s*(?:0[xX][0-9a-fA-F]+|[0-9]+)\s*\)'
    matches = list(re.finditer(pattern, clean[span[0]:span[1]]))
    tokens = sorted({m.group(1) for m in matches})
    low = [r for r in candidate.relocs if r.type == 6 and r.symbol == 'D_00000000']
    if len(tokens) < 2 or len(low) < len(tokens):
        return None
    definitions = csource._blank(header + '\n' + source)
    for token in tokens:
        if token == 'D_00000000':
            continue
        pattern = r'(?m)^\s*#\s*define\s+' + re.escape(token) + r'\s+([^\n]*)$'
        values = re.findall(pattern, definitions)
        if not values or any(value.strip() != 'D_00000000' for value in values):
            return None
        if re.search(r'(?m)^\s*#\s*undef\s+' + re.escape(token) + r'\b', definitions):
            return None
    declaration = re.search(r'(?m)^[ \t]*extern[ \t]+[^;{}#\n]+\bD_00000000[ \t]*;', csource._blank(header))
    if declaration is None:
        return None
    prototype = header[declaration.start():declaration.end()]
    names = {token: f'decomp_overlay_group_ref_{index}' for index, token in enumerate(tokens, 1)}
    wrappers = []
    for index, match in enumerate(matches, 1):
        name = f'decomp_overlay_group_site_{index}'
        if re.search(r'\b' + re.escape(name) + r'\b', definitions):
            return None
        start, end = span[0] + match.start(), span[0] + match.end()
        a, b = span[0] + match.start(1), span[0] + match.end(1)
        wrappers.append((start, end, name, source[start:end], source[start:a] + names[match.group(1)] + source[b:end]))
    if any(re.search(r'\b' + re.escape(name) + r'\b', definitions) for name in names.values()):
        return None
    for start, end, name, _, _ in reversed(wrappers):
        source = source[:start] + name + source[end:]
    declarations = [rename_identifiers(prototype, {'D_00000000': name}) for name in names.values()]
    port = '\n'.join(f'#define {name} {original}' for _, _, name, original, _ in wrappers)
    rom = '\n'.join(f'#define {name} {changed}' for _, _, name, _, changed in wrappers)
    return '\n'.join(declarations) + '\n#ifdef PORT\n' + port + '\n#else\n' + rom + '\n#endif\n' + source


def _probe_grouped(net, project, fn, source, header, candidate, target, info):
    grouped = _grouped_data(fn, source, header, candidate)
    if grouped:
        built = net._compile(project, header, grouped, fn)
        if not built.ok:
            return None
        separated, _, _ = net._evaluate(fn, project, built.obj)
        bound = overlay_donor_aliases.explicit_symbol_bindings(
            grouped, header, separated, target, net.console(project).separate_overlay_reference)
        if not bound or not bound[1].startswith(header):
            return None
        extra = '\n#ifndef PORT\n' + bound[1][len(header):] + '\n#endif\n'
        variants = [grouped + '\n' + extra]
        macro = _macro_variant(fn, grouped, header, separated, info, extra)
        if macro:
            variants.append(macro)
        for prefer_decl in (True, False):
            for nested_first in (False, True):
                direct = _direct_variant(fn, grouped, header, separated, target, extra,
                                         prefer_decl=prefer_decl, nested_first=nested_first)
                if direct and direct not in variants:
                    variants.append(direct)
        for variant in variants:
            built = net._compile(project, header, variant, fn)
            if built.ok and net._evaluate(fn, project, built.obj)[1] is True:
                return variant
    return None

def _group_object_data(fn, source, header, candidate):
    """Separate named object aliases of one placeholder for a private probe."""
    span = csource.find(source, fn['name'])
    if span is None:
        return None
    clean = csource._blank(source)
    body = clean[span[0]:span[1]]
    if re.search(r'(?m)^\s*#', body):
        return None
    definitions = csource._blank(header + '\n' + source)
    aliases = set(re.findall(r'(?m)^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+D_00000000[ \t]*$', definitions))
    tokens = aliases | {'D_00000000'}
    sites = [(span[0] + m.start(), span[0] + m.end(), m.group())
             for m in re.finditer(r'\b[A-Za-z_]\w*\b', body) if m.group() in tokens]
    used = sorted({token for _, _, token in sites})
    low = [r for r in candidate.relocs if r.type == 6 and r.symbol == 'D_00000000']
    if len(used) < 2 or len(low) < len(used):
        return None
    macros = set(re.findall(r'(?m)^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)\(', definitions))
    for start, _, token in sites:
        if token != 'D_00000000' and re.search(r'(?m)^[ \t]*#[ \t]*undef[ \t]+' + re.escape(token) + r'\b', definitions):
            return None
        if re.search(r'(?:\.|->)\s*$', clean[span[0]:start]):
            return None
        depth = 0
        for at in range(start - 1, span[0] - 1, -1):
            if clean[at] == ')':
                depth += 1
            elif clean[at] == '(':
                if depth:
                    depth -= 1
                    continue
                caller = re.search(r'([A-Za-z_]\w*)\s*$', clean[span[0]:at])
                if caller:
                    name = caller.group(1)
                    if name in macros or (name.isupper() and not re.fullmatch(r'D_[0-9A-Fa-f]{8}', name)):
                        return None
    declaration = re.search(r'(?m)^[ \t]*extern[ \t]+[^;{}#\n]+\bD_00000000[ \t]*;', csource._blank(header))
    if declaration is None:
        return None
    prototype = header[declaration.start():declaration.end()]
    names = {token: f'decomp_overlay_object_ref_{index}' for index, token in enumerate(used, 1)}
    if any(re.search(r'\b' + re.escape(name) + r'\b', definitions) for name in names.values()):
        return None
    for start, end, token in reversed(sites):
        source = source[:start] + names[token] + source[end:]
    port = '\n'.join(f'#define {name} {token}' for token, name in names.items())
    rom = '\n'.join(rename_identifiers(prototype, {'D_00000000': name}) for name in names.values())
    return '#ifdef PORT\n' + port + '\n#else\n' + rom + '\n#endif\n' + source


def _probe_objects(net, project, fn, source, header, candidate, target, info):
    grouped = _group_object_data(fn, source, header, candidate)
    if not grouped:
        return None
    built = net._compile(project, header, grouped, fn)
    if not built.ok:
        return None
    separated, _, _ = net._evaluate(fn, project, built.obj)
    bound = overlay_donor_aliases.explicit_symbol_bindings(
        grouped, header, separated, target, net.console(project).separate_overlay_reference)
    if not bound or not bound[1].startswith(header):
        return None
    extra = '\n#ifndef PORT\n' + bound[1][len(header):] + '\n#endif\n'
    variants = [grouped + '\n' + extra]
    macro = _macro_variant(fn, grouped, header, separated, info, extra)
    if macro:
        variants.append(macro)
    for prefer_decl in (True, False):
        for nested_first in (False, True):
            direct = _direct_variant(fn, grouped, header, separated, target, extra,
                                     prefer_decl=prefer_decl, nested_first=nested_first)
            if direct and direct not in variants:
                variants.append(direct)
    for variant in variants:
        built = net._compile(project, header, variant, fn)
        if built.ok and net._evaluate(fn, project, built.obj)[1] is True:
            return variant
    return None


def _float_prototype_variants(fn, source, header, target):
    """Try fixed float parameters for otherwise undeclared loader calls."""
    span = csource.find(source, fn['name'])
    if span is None:
        return []
    clean = csource._blank(source)
    body = clean[span[0]:span[1]]
    pattern = r'\buso_[0-9A-Fa-f]+_sym_[0-9]+(?=\s*\()'
    calls = list(re.finditer(pattern, body))
    counts = Counter(match.group() for match in calls)
    targets = {r.symbol for r in target.relocs if r.type == 4}
    declarations = csource._blank(header + '\n' + source)
    variants = []
    float_literal = r'[+-]?(?:(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+)[fF]'
    for call in calls:
        symbol = call.group()
        if counts[symbol] != 1 or symbol not in targets:
            continue
        declared = r'(?m)^[ \t]*[A-Za-z_]\w*(?:[ \t*]+[A-Za-z_]\w*)*[ \t*]+' + re.escape(symbol) + r'\s*\('
        if re.search(declared, declarations):
            continue
        opening = body.find('(', call.end())
        depth, start, args = 0, opening + 1, []
        for at in range(opening, len(body)):
            char = body[at]
            if char in '([{':
                depth += 1
            elif char in ')]}':
                depth -= 1
                if depth == 0:
                    args.append(body[start:at].strip())
                    break
            elif char == ',' and depth == 1:
                args.append(body[start:at].strip())
                start = at + 1
        else:
            continue
        if not args or len(args) > 12 or any(not arg for arg in args):
            continue
        types = ['float' if re.fullmatch(float_literal, arg) else 'int' for arg in args]
        if 'float' not in types:
            continue
        prototype = f'extern int {symbol}({", ".join(types)});'
        variants.append('#ifndef PORT\n' + prototype + '\n#endif\n' + source)
    return variants


def retry(net, project, fn, source):
    """Return a rewritten saved source only after its complete module-aware judge passes."""
    try:
        info = json.loads(fn['meta'] or '{}').get('uso') or {}
        if not info.get('native') or not isinstance(info.get('section'), int):
            return None
        unit = fn['unit'] or ''
        context = net._contexts(fn['project_id'], {unit}).get(unit)
        header = context['header'] if context else ''
        built = net._compile(project, header, source, fn)
        if not built.ok:
            return None
        candidate, exact, _ = net._evaluate(fn, project, built.obj)
        if candidate is None or exact:
            return None
        target = net._target_obj(fn, project)
        bound = overlay_donor_aliases.explicit_symbol_bindings(
            source, header, candidate, target, net.console(project).separate_overlay_reference)
        extra = bound[1][len(header):] if bound and bound[1].startswith(header) else ''
        if extra:
            # These records prove ROM loader bindings; ports use their own symbols.
            extra = '\n#ifndef PORT\n' + extra + '\n#endif\n'
        variants = [source + '\n' + extra] if extra else []
        macro = _macro_variant(fn, source, header, candidate, info, extra)
        if macro:
            variants.append(macro)
        for prefer_decl in (True, False):
            for nested_first in (False, True):
                direct = _direct_variant(fn, source, header, candidate, target, extra,
                                         prefer_decl=prefer_decl, nested_first=nested_first)
                if direct and direct not in variants:
                    variants.append(direct)
        data = _data_variant(fn, source, header, candidate, target)
        if data:
            variants.append(data + '\n' + extra)
            combined = _macro_variant(fn, data, header, candidate, info, extra)
            if combined:
                variants.append(combined)
        for variant in variants:
            built = net._compile(project, header, variant, fn)
            if built.ok and net._evaluate(fn, project, built.obj)[1] is True:
                return variant
        for variant in _float_prototype_variants(fn, source, header, target):
            built = net._compile(project, header, variant, fn)
            if built.ok and net._evaluate(fn, project, built.obj)[1] is True:
                return variant
        grouped = _probe_grouped(net, project, fn, source, header, candidate, target, info)
        if grouped:
            return grouped
        objects = _probe_objects(net, project, fn, source, header, candidate, target, info)
        if objects:
            return objects
        splits = [_split_direct_data(fn, source, header, candidate, group_casts=True),
                  _split_direct_data(fn, source, header, candidate)]
        local = _split_local_pointer(fn, source, header)
        if local:
            built = net._compile(project, header, local, fn)
            if built.ok:
                local_candidate, _, _ = net._evaluate(fn, project, built.obj)
                if local_candidate:
                    splits.append(_split_direct_data(fn, local, header, local_candidate, relaxed=True))
        for split in splits:
            if not split:
                continue
            built = net._compile(project, header, split, fn)
            if not built.ok:
                continue
            separated, _, _ = net._evaluate(fn, project, built.obj)
            bound = overlay_donor_aliases.explicit_symbol_bindings(
                split, header, separated, target, net.console(project).separate_overlay_reference)
            if not bound or not bound[1].startswith(header):
                continue
            extra = '\n#ifndef PORT\n' + bound[1][len(header):] + '\n#endif\n'
            variants = [split + '\n' + extra]
            macro = _macro_variant(fn, split, header, separated, info, extra)
            if macro:
                variants.append(macro)
            for prefer_decl in (True, False):
                for nested_first in (False, True):
                    direct = _direct_variant(fn, split, header, separated, target, extra,
                                             prefer_decl=prefer_decl, nested_first=nested_first)
                    if direct and direct not in variants:
                        variants.append(direct)
            for variant in variants:
                built = net._compile(project, header, variant, fn)
                if built.ok and net._evaluate(fn, project, built.obj)[1] is True:
                    return variant
    except (NetworkError, OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None
    return None
