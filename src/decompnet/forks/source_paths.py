"""Read source for imports whose objdiff metadata lists objects without source paths.

This does not make an inferred file writable or alter the imported build configuration.
"""
from pathlib import Path

SUFFIXES = ('.c', '.cpp', '.cc', '.cxx', '.cp')


def readable(root, unit):
    root = Path(root).resolve()
    if unit.source:
        paths = [root / unit.source]
    else:
        # Only the unit's exact relative name is evidence. Basename searches can
        # confuse overlay files; ambiguous language alternatives must be explicit.
        name = Path(unit.name)
        if name.is_absolute() or '..' in name.parts:
            return None
        paths = [root / (str(name) + suffix) for suffix in SUFFIXES]
    found = [path for path in paths if path.resolve().is_relative_to(root) and path.is_file()]
    return found[0] if len(found) == 1 else None
