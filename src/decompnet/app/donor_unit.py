"""Portable, bounded source snapshots for a donor translation unit."""

import json
import re
from pathlib import Path

from ..compiler import DONOR_UNIT_HEADER, MAX_DONOR_UNIT_BYTES, SourceRejected, check_source, preprocess_donor_unit
from ..forks import csource
from ..forks.project import ForkProject
from ..forks.source_paths import readable
from . import donor_sources


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
    key = (generated, tuple(invocation.cmd))
    if key not in cache:
        roots, _ = donor_sources.build_hints(root)
        reader = donor_sources.DonorReader({'root': str(root), 'include_roots': '\n'.join(map(str, roots))})
        text = reader.flatten(generated, original_parent=original.parent)
        # Plain checkout source may still contain its assembly placeholders. Preserve their line
        # boundaries; never compile them into a saved C unit.
        text = re.sub(r'(?m)^[ \t]*(?:#\s*pragma\s+)?(?:GLOBAL_ASM|INCLUDE_ASM|INCLUDE_RODATA)\([^\n]*$', '', text)
        text = preprocess_donor_unit(invocation, text)
        if len(cache) >= 4:
            cache.pop(next(iter(cache)))
        cache[key] = (reader.file(original), re.sub(r'(?m)^[ \t]*#[^\n]*', '', text) if text is not None else None)
    original_text, text = cache[key]
    if text is None:
        return None
    original_span = csource.find(original_text, entry)
    if original_span is None or csource.is_stub(original_text[slice(*original_span)]):
        return None
    # Keep the selected definition in the editable source and the preceding text in its private header.
    span = csource.find(text, entry)
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
