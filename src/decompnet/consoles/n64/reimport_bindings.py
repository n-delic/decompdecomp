"""Prove saved native-module sources against newly recovered loader bindings."""

import json
import re

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
    calls = list(re.finditer(r'\bCALL\s*\(\s*[A-Za-z_]\w*\s*\)', clean[span[0]:span[1]]))
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
    if declaration is None:
        return None
    prototype = header[declaration.start():declaration.end()]
    local = {(at, index): offset for at, index, offset in info.get('local_calls', ())}
    bindings = [local_alias(info['section'], index, local[(at, index)]) if (at, index) in local
                else alias(info['section'], index) for at, index in sorted(relocs)]
    for (start, end), binding in reversed(list(zip(calls, bindings))):
        source = source[:start] + binding + source[end:]
    declarations = [rename_identifiers(prototype, {'D_80000000': binding}) for binding in sorted(set(bindings))]
    return '\n'.join(declarations) + '\n' + source + '\n' + extra


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
        variants = [source + '\n' + extra] if extra else []
        macro = _macro_variant(fn, source, header, candidate, info, extra)
        if macro:
            variants.append(macro)
        for variant in variants:
            built = net._compile(project, header, variant, fn)
            if built.ok and net._evaluate(fn, project, built.obj)[1] is True:
                return variant
    except (NetworkError, OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None
    return None
