"""Portable, bounded source snapshots for a donor translation unit."""

import json
import re
from pathlib import Path

from ..compiler import DONOR_UNIT_HEADER, MAX_DONOR_UNIT_BYTES, SourceRejected, check_source, preprocess_donor_unit
from ..forks import csource
from ..forks.project import ForkProject
from ..forks.source_paths import readable
from ..network import NetworkError
from . import donor_sources


class PreparedSources(dict):
    """Keep replacement snapshots from evicting the resident source cache."""

    def __init__(self):
        super().__init__()
        self.replacements = {}


def _cache_snapshot(cache, key, prepare):
    if key not in cache:
        saved = prepare()
        if len(cache) >= 4:
            cache.pop(next(iter(cache)))
        cache[key] = saved
    return cache[key]


def _preprocess(path, root, invocation, *, original_parent=None):
    roots, _ = donor_sources.build_hints(root)
    reader = donor_sources.DonorReader({'root': str(root), 'include_roots': '\n'.join(map(str, roots))})
    text = reader.flatten(path, original_parent=original_parent)
    if original_parent is not None:
        # Plain checkout source may still contain assembly placeholders.
        text = re.sub(r'(?m)^[ \t]*(?:#\s*pragma\s+)?(?:GLOBAL_ASM|INCLUDE_ASM|INCLUDE_RODATA)\([^\n]*$', '', text)
    text = preprocess_donor_unit(invocation, text)
    return (re.sub(r'(?m)^[ \t]*#[^\n]*', '', text) if text is not None else None), reader


def _owned_span(text, entry):
    span = csource.find(text, entry)
    if span:
        return span
    # A macro can spell a declaration's return type. Hide it from the source
    # parser only. The donor's real preprocessor decides what it means.
    prefix = re.search(r'(?m)^[ \t]*([A-Za-z_]\w*\([^\n;{}]*?\))[ \t]+' + re.escape(entry) + r'[ \t]*\(',
                       csource._blank(text))
    if not prefix:
        return None
    start, end = prefix.span(1)
    return csource.find(text[:start] + 'int'.ljust(end - start) + text[end:], entry)


def has_owned_definition(root, unit, entry):
    path = readable(root, unit)
    if path is None:
        return False
    try:
        text = donor_sources.DonorReader({'root': str(root)}).file(path)
    except (NetworkError, OSError):
        return False
    span = _owned_span(text, entry)
    return bool(span and not csource.is_stub(text[slice(*span)]))


def candidate(other, donor, names, invocation, cache):
    """Snapshot the compiler's selected translation unit with its entry in the editable source."""
    if 'unit' not in donor.keys():
        return None
    root = Path(other['root']).resolve()
    unit = ForkProject(root).unit(donor['unit'])
    original = readable(root, unit)
    if original is None:
        return None
    generated = unit.base.with_suffix('').resolve() if unit.base else original
    if not generated.is_relative_to(root) or not generated.is_file():
        generated = original
    entry = (donor['meta'] and json.loads(donor['meta']).get('symbol')) or donor['name']
    key = (generated, original, tuple(invocation.cmd), invocation.source_extension)

    def prepare():
        text, reader = _preprocess(generated, root, invocation, original_parent=original.parent)
        return reader.file(original), text, {}

    original_text, text, spans = _cache_snapshot(cache, key, prepare)
    if text is None:
        return None
    if entry not in spans:
        original_span = _owned_span(original_text, entry)
        spans[entry] = (csource.find(text, entry)
                        if original_span and not csource.is_stub(original_text[slice(*original_span)]) else None)
    span = spans[entry]
    # Keep the selected definition in the editable source and the preceding text in its private header.
    if span is None or csource.is_stub(text[span[0]:span[1]]):
        return None
    start = text.rfind('\n', 0, span[0]) + 1
    if start == 0 or text[start:span[0]].strip():
        return None
    from .source_symbol_recovery import rename
    aliases = {**names, entry: names.get(entry, names.get(donor['name'], donor['name']))}
    prefix, source = rename(text[:start], aliases), rename(text[start:], aliases)
    # The prefix is source too. Apply the same directive checks without a blanket trust exception.
    if any(re.search(r'(?im)^\s*#\s*(include|import|embed)\b', part) for part in (prefix, source)):
        return None
    try:
        check_source(prefix + source, max_bytes=MAX_DONOR_UNIT_BYTES)
    except SourceRejected:
        return None
    return source, DONOR_UNIT_HEADER + prefix


def is_unit(header, proj):
    return proj['arch'] == 'mips' and header.startswith(DONOR_UNIT_HEADER)


def replacement(other, donor, target_name, invocation, path, cache=None):
    """Prepare literal replacement C without executing the donor's build or object patches."""
    root = Path(other['root']).resolve()
    if not path.resolve().is_relative_to(root):
        return None
    key = (path, tuple(invocation.cmd), invocation.source_extension)
    def prepare():
        return _preprocess(path, root, invocation)[0], {}

    text, spans = _cache_snapshot(cache if cache is not None else {}, key, prepare)
    if text is None:
        return None
    entry = (donor['meta'] and json.loads(donor['meta']).get('symbol')) or donor['name']
    if entry not in spans:
        spans[entry] = (entry, csource.find(text, entry))
        if spans[entry][1] is None and path.suffix == '.cpp':
            clean = csource._blank(text)
            methods = [(match[1], match[2]) for match in re.finditer(
                r'\b([A-Za-z_]\w*)::([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{', clean)]
            if len(methods) == 1:
                owner, method = methods[0]
                spans[entry] = (method, csource.find(text, f'{owner}::{method}'))
    source_name, span = spans[entry]
    if not span or csource.is_stub(text[slice(*span)]):
        return None
    from .source_symbol_recovery import rename

    start = text.rfind('\n', 0, span[0]) + 1
    aliases = {source_name: target_name}
    return rename(text[start:], aliases), DONOR_UNIT_HEADER + rename(text[:start], aliases)
