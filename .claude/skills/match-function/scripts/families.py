#!/usr/bin/env python3
"""A searchable one-line-per-family list of the decomp-repair library: family, module, what it is for.

Step 5 of the skill says to check what already exists before writing a rewrite, and ten minutes of grep over the
library's docstrings has found the fix more than once. This does that grep. It reads the *installed* library's source
(never imports it), so it is always the version this environment runs:

    families.py [QUERY ...] [--all] [--library DIR]

Every QUERY word must appear (case-insensitive) in the family name, module name or purpose. A line is
`family  module  purpose`; a module with no FAMILY/FAMILIES constant but a `variants` function is listed by its module
name (it labels its children itself). --all also lists the modules that are neither (shared helpers). The purpose is the
module docstring's first sentence. To try a family on a source: prove_chain.py --families NAME (and --list).
"""
import argparse
import ast
import importlib.util
import sys
from pathlib import Path

PURPOSE_CHARS = 150


def library_dir(explicit=None):
    if explicit:
        return Path(explicit)
    spec = importlib.util.find_spec("decomprepair")
    if spec is None or not spec.submodule_search_locations:
        sys.exit("decomprepair isn't installed in this python: run with a setup.py session's venv python")
    return Path(next(iter(spec.submodule_search_locations)))


def constant(node, known):
    """The strings a module-level expression names (a literal, a tuple of them, a constant, a sum, an index), or None
    when it needs the library's own code to say (another module's FAMILIES)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, (ast.Tuple, ast.List)):
        out = []
        for item in node.elts:
            part = constant(item, known)
            if part is None:
                return None
            out += part
        return tuple(out)
    if isinstance(node, ast.Name):
        return known.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = constant(node.left, known), constant(node.right, known)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
        whole = constant(node.value, known)
        try:
            return (whole[node.slice.value],) if whole else None
        except IndexError:
            return None
    return None


def purpose(doc):
    """The docstring's first sentence, on one line."""
    text = " ".join((doc or "").split())
    end = text.find(". ")
    text = text[:end + 1] if 0 < end < PURPOSE_CHARS else text
    return text if len(text) <= PURPOSE_CHARS else text[:PURPOSE_CHARS - 1].rstrip() + "…"


def describe(path):
    """(families, has_variants, purpose) of one library module, from its source."""
    tree = ast.parse(path.read_text())
    known, families = {}, []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = constant(node.value, known)
            if value is None:
                continue
            known[node.targets[0].id] = value
            if node.targets[0].id in ("FAMILY", "FAMILIES"):
                families += value
    has_variants = any(isinstance(n, ast.FunctionDef) and n.name in ("variants", "stages", "probes") for n in tree.body)
    return list(dict.fromkeys(families)), has_variants, purpose(ast.get_docstring(tree))


def catalogue(folder, include_helpers=False):
    rows = []
    for path in sorted(Path(folder).glob("*.py")):
        if path.name.startswith("__"):
            continue
        try:
            families, has_variants, text = describe(path)
        except (SyntaxError, UnicodeDecodeError):
            continue
        module = path.stem
        if families:
            rows += [(family, module, text) for family in families]
        elif has_variants or include_helpers:
            rows.append((module, module, text))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="*")
    ap.add_argument("--all", action="store_true", help="also modules with no family and no variants function")
    ap.add_argument("--library")
    a = ap.parse_args(argv)
    words = [w.lower() for w in a.query]
    shown = [row for row in catalogue(library_dir(a.library), a.all)
             if all(w in " ".join(row).lower() for w in words)]
    for family, module, text in sorted(shown):
        print(f"{family}  {module}  {text}")
    print(f"{len(shown)} families", file=sys.stderr)


if __name__ == "__main__":
    main()
