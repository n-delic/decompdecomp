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


CALL_RESULT = '''int f(u8 *a,u8 *b){register u8 x,y;
 x=read(*a);y=read(*b);if((x&0xFF)>(y&0xFF))return 1;
 x=read(*a);y=read(*b);if((x&0xFF)<(y&0xFF))return 0;return 2;}'''
STACK = 'int other(void) { return 7; }\nvoid f(Obj *p)\n{\n int flags; /* flags stays a comment */\n p->flags=1;\n say("flags");\n}\n'


def test_propose_reaches_what_each_family_proposes_on_its_own():
    from decomprepair import call_result_repairs, stack_repairs
    from decomprepair.propose import propose
    for source, module in [(CALL_RESULT, call_result_repairs), (STACK, stack_repairs)]:
        alone = [v[1] if isinstance(v, tuple) else v for v in module.variants(source, 'f')]
        mine = [p.source for p in propose(source, 'f').proposals if p.module == module.__name__.split('.')[-1]]
        assert alone and set(alone) == set(mine)
    labels = {p.family for p in propose(STACK, 'f').proposals}
    assert 'unused_scalar_local' in labels  # a family's own label, not just its module's name


def test_propose_gives_each_source_once_never_the_input_and_times_every_family():
    from decomprepair.propose import modules, propose
    report = propose(CALL_RESULT, 'f')
    texts = [p.source for p in report.proposals]
    assert texts and len(texts) == len(set(texts)) and CALL_RESULT not in texts
    assert [t.module for t in report.timings] == modules() and len(modules()) > 100
    assert all(t.ms >= 0 for t in report.timings) and report.ms == sum(t.ms for t in report.timings)


def test_a_family_that_needs_an_input_you_left_out_says_so_and_runs_when_given():
    from decomprepair.propose import propose
    skipped = {t.module: t.skipped for t in propose(STACK, 'f').timings}
    assert skipped['storage_scope_repairs'].startswith('needs ') and skipped['field_load_repairs'].startswith('needs ')
    assert {t.module: t.skipped for t in propose(STACK, 'f', arch='mips').timings}['field_load_repairs'] == 'needs asm'
    given = {t.module: t.skipped for t in propose(STACK, 'f', arch='mips', asm='', flags=('-O2',)).timings}
    assert given['storage_scope_repairs'] == '' and given['field_load_repairs'] == ''


def test_a_family_that_raises_is_reported_and_the_others_still_propose(monkeypatch):
    from decomprepair import stack_repairs
    from decomprepair.propose import propose

    def broken(source, name):
        raise ValueError('boom')
    monkeypatch.setattr(stack_repairs, 'variants', broken)
    report = propose(CALL_RESULT, 'f')
    assert {t.module: t.error for t in report.timings}['stack_repairs'] == 'ValueError: boom'
    assert any(p.module == 'call_result_repairs' for p in report.proposals)


def test_the_command_line_writes_each_proposal_and_reports_json(tmp_path):
    source = tmp_path / 'f.c'
    source.write_text(STACK)
    run = lambda *a: subprocess.run([sys.executable, '-m', 'decomprepair', str(source), '--function', 'f', *a],  # noqa: E731
                                    capture_output=True, text=True, check=True).stdout
    assert 'proposals from' in run('--out', str(tmp_path / 'out'))
    written = sorted((tmp_path / 'out').glob('*.c'))
    assert written and all(p.read_text() != STACK for p in written)
    report = __import__('json').loads(run('--json', '--only', 'stack_repairs'))
    assert report['proposals'] and {p['module'] for p in report['proposals']} == {'stack_repairs'}
    assert [t['module'] for t in report['timings']] == ['stack_repairs']
