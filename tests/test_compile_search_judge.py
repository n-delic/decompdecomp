"""Lean search evaluation retains exact linking; full diagnostics are still available for winners."""
from types import SimpleNamespace

import pytest

from decompnet import compile_sessions
from decompnet.app.engine.judging import JudgeMixin, search_evaluation
from decompnet.rom.match import LinkResult
from tests.test_candidate_diagnostics import object_file


@pytest.mark.parametrize('exact', [False, True])
def test_lean_search_uses_full_link_verdict_without_export_diagnostics(monkeypatch, exact):
    candidate = object()
    links = []; reports = []
    def link(*args):
        links.append(args)
        return LinkResult(candidate, exact, ['data differs'] if not exact else [], {})
    monkeypatch.setattr('decompnet.rom.match.link', link)
    monkeypatch.setattr('decompnet.app.candidate_diagnostics.export_layout',
                        lambda *a: reports.append(a) or {'ready':True})
    class Net(JudgeMixin):
        def is_rom(self, proj):return True
        def _rom_target(self, fn):return None
        def symbols(self, pid):return None
        def reader(self, proj, fn):return None
    net = Net();fn = {'id':1, 'name':'f'};proj = {'id':1}
    with search_evaluation():
        got, verdict, problems = net._evaluate(fn, proj, object_file())
    assert got is candidate and verdict is exact
    assert bool(problems) is not exact and len(links) == 1 and not reports
    import hashlib
    net._candidate_diagnostics = {(fn['id'], hashlib.sha256(object_file()).hexdigest()): ({}, [], ['stale rows'])}
    assert net._search_rows(fn, proj, object_file(), ['current rows']) == ['current rows']
    # Promotion/interactive paths use the ordinary diagnostics and linker again.
    assert net._evaluate(fn, proj, object_file())[1] is exact
    assert len(links) == 2 and reports


def test_target_cache_scoped_to_exact_inputs_and_symbol_table():
    class Net(JudgeMixin):
        _generation = {}
        def __init__(self):
            self.syms = object();self.calls = 0
        def is_rom(self, proj):return True
        def cfg(self, proj):return {'example':proj['config']}
        def symbols(self, pid):return self.syms
        def console(self, proj):
            def target(*args):
                self.calls += 1
                return SimpleNamespace(cpu=None)
            return SimpleNamespace(target_object=target, cpu_variant='test')
    net = Net();proj = {'id':1, 'config':'first'}
    fn = dict(id=1, name='f', target=b'target', relocs='[]', vram=0, meta='{}', unit='', size=4, rom_offset=0)
    with compile_sessions.session():
        first = net._target_obj(fn, proj)
        assert net._target_obj(fn, proj) is first and net.calls == 1
        assert net._target_obj({**fn, 'target':b'changed'}, proj) is not first
        assert net._target_obj(fn, {**proj, 'config':'changed'}) is not first
        net.syms = object()
        assert net._target_obj(fn, proj) is not first
        net._generation[1] = 1
        assert net._target_obj(fn, proj) is not first
    net._target_obj(fn, proj)
    assert net.calls == 6


@pytest.mark.parametrize('score,exact,diagnostic_calls,records', [(80,False,1,0), (10,False,2,1), (80,True,2,1)])
def test_permuter_scores_once_and_fully_diagnoses_every_saved_candidate(monkeypatch, score, exact, diagnostic_calls, records):
    from decompnet import scoring
    from decompnet.app.engine.judging import _search_evaluation
    from decompnet.app.permute import Permuter
    from decompnet.compiler import CompileResult
    from decompnet.elfobj import FunctionObj
    from tests.test_permute import AGENT, FakeNet
    net = FakeNet();fn = net.function();proj = net.project_row('game')
    target = FunctionObj('f', 'mips', True, bytes.fromhex('03e0000800000000'), [])
    modes = [];diffs = [];saved = []
    net._compile = lambda *a: CompileResult(True, b'object', '')
    def evaluate(*args):
        modes.append(_search_evaluation.get())
        return target, exact, []
    net._evaluate = evaluate
    net._target_obj = lambda *a: target
    net.keep_diff = lambda *a: None
    def record(*args, **kwargs):
        assert modes == [True, False]
        saved.append(kwargs)
    net._record = record
    def diff(*args):
        diffs.append(1)
        return scoring.DiffResult(score, 100, [], False)
    monkeypatch.setattr(scoring, 'diff', diff)
    monkeypatch.setattr('decompnet.source_lines.mismatches', lambda *a, **kw: {'available':False})
    result = Permuter(net)._score_work(AGENT, proj, fn, 'int f(){return 123;}', 40, None)
    assert len(diffs) == 1 and len(modes) == diagnostic_calls and len(saved) == records
    assert result == (0 if exact else score)
    if exact:assert saved[0]['matched']


def test_prepared_disassembly_preserves_score_and_invalidates_target_changes(monkeypatch):
    from decompnet import scoring
    from decompnet.elfobj import FunctionObj, Reloc
    target = FunctionObj('f', 'mips', True, bytes.fromhex('03e0000800000000'), [])
    candidate = FunctionObj('f', 'mips', True, bytes.fromhex('03e0000824020001'), [])
    expected = scoring.diff(target, candidate)
    original = scoring.disassemble;targets = []
    def disassemble(fn, cpu=None):
        if fn is target:targets.append(1)
        return original(fn, cpu)
    monkeypatch.setattr(scoring, 'disassemble', disassemble)
    with compile_sessions.session() as session:
        for _ in range(3):assert scoring.diff(target, candidate) == expected
        assert len(targets) == 1
        target.code = candidate.code
        assert scoring.diff(target, candidate).bytes_match
        assert len(targets) == 2
        # A mutable relocation list cannot leave an old normalized target in the cache.
        target.relocs.append(Reloc(0, 4, 'changed', 0))
        scoring.diff(target, candidate)
        assert len(targets) == 3 and len(session.instructions) == 3


def test_lean_search_retains_physical_register_cycles_without_relinking(monkeypatch):
    import struct

    from decompnet.elfobj import FunctionObj
    target = struct.pack('>III', 0x27bdffc0, 0x26f4ffff, 0x0003ac03)
    linked = struct.pack('>III', 0x27bdffc0, 0x26f5ffff, 0x0003a403)
    net = object.__new__(JudgeMixin)
    net.is_rom = lambda _: True
    net._rom_target = lambda _: SimpleNamespace(code=target)
    net.symbols = lambda _: None
    net.reader = lambda *a: None
    net._target_obj = lambda *a: FunctionObj('f', 'mips', True, target)
    calls = []
    def link(*args):
        calls.append(1)
        return LinkResult(FunctionObj('f', 'mips', True, linked), False, [], {}, False, linked)
    monkeypatch.setattr('decompnet.rom.match.link', link)
    fn = dict(id=1, name='f');proj = dict(id=1, arch='mips', big_endian=True)
    net._evaluate(fn, proj, b'object')
    expected = net._search_rows(fn, proj, b'object', ['fallback'])
    assert expected != ['fallback']
    net._candidate_diagnostics.clear()
    with search_evaluation():assert net._evaluate(fn, proj, b'object')[1] is False
    assert net._search_rows(fn, proj, b'object', ['fallback']) == expected
    assert len(calls) == 2
