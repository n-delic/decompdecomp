"""decomprepair is a standalone library: it must never import decompdecomp (`decompnet`), so other tools can use it."""

import ast
import pathlib
import subprocess
import sys

import decomprepair

LIBRARY = pathlib.Path(decomprepair.__file__).parent


def test_no_module_imports_decompnet():
    found = []
    for path in sorted(LIBRARY.glob('*.py')):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ''] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            found += [f'{path.name}: {n}' for n in names if n.split('.')[0] == 'decompnet']
            if isinstance(node, ast.ImportFrom) and node.level > 1:
                found.append(f'{path.name}: relative import above the package')
    assert found == []


def test_every_module_imports_without_decompnet():
    code = ('import importlib, pkgutil, sys, decomprepair\n'
            'for m in pkgutil.iter_modules(decomprepair.__path__): importlib.import_module("decomprepair." + m.name)\n'
            'print(sorted(k for k in sys.modules if k.split(".")[0] == "decompnet"))')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == '[]'


def test_app_names_are_the_library_modules():
    """The app keeps its old names for the shared readers; both names must be one module (patches reach both)."""
    from decompnet.arch import mips
    from decompnet.forks import csource
    from decompnet.rom import symbols
    from decomprepair import csource as c
    from decomprepair import mips as m
    from decomprepair import symbols as s
    assert (csource, mips, symbols) == (c, m, s)
