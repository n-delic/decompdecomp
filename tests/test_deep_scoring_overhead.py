"""Lean comparisons preserve scores; cached instructions cannot leak mutable state."""
import random

import pytest

from decompnet import scoring
from decompnet.app.integer_repair_support import view
from decompnet.elfobj import FunctionObj, Reloc


@pytest.mark.parametrize('arch,big', [('mips',True),('mips',False),('x86',False),('arm',False)])
def test_lean_score_matches_full_rows(arch,big):
    randomizer=random.Random(442)
    for _ in range(30):
        target=FunctionObj('f',arch,big,randomizer.randbytes(64))
        candidate=FunctionObj('f',arch,big,randomizer.randbytes(64))
        full=scoring.diff(target,candidate)
        lean=scoring.score(target,candidate)
        assert (lean.score,lean.max_score,lean.bytes_match)==(full.score,full.max_score,full.bytes_match)
        assert not lean.rows


def test_instruction_cache_isolated_and_invalidated_by_relocations():
    fn=FunctionObj('f','mips',True,bytes.fromhex('0c000000'),[Reloc(0,4,'first',0)])
    first=scoring.disassemble(fn)
    first[0].text="corrupted"
    first.clear()
    assert scoring.disassemble(fn)[0].text=='jal first'
    fn.relocs=[Reloc(0,4,'second',0)]
    assert scoring.disassemble(fn)[0].text=='jal second'


def test_cached_c_view_returns_independent_mutable_spans():
    source='int f(int x){return x+1;}'
    first=view(source,'f');expected=first.end
    first.end=0
    assert view(source,'f').end==expected


def test_lookahead_shrinks_on_unused_work_and_grows_on_consumed_work(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from decompnet.app import deep_lookahead
    widths=[]
    @contextmanager
    def chunk(search,agent,proj,fn,sources,fork):
        widths.append(len(sources))
        search._ahead={source:SimpleNamespace(counted=False) for source in sources}
        yield
        search._ahead={}
    monkeypatch.setattr(deep_lookahead.deep_compilation,'chunk',chunk)
    search=SimpleNamespace(compile_pool=SimpleNamespace(count=8),net=SimpleNamespace(compiled_ahead=True),
        limit=100,tested=0,check=lambda:None,_prime_trials=lambda *args:None)
    with deep_lookahead.Window(search,None,None,None,False) as window:
        window.ensure(['a','b','c','d'])
        window.ensure(['new','c','d'])
        for ticket in search._ahead.values():ticket.counted=True
        window.ensure(['next','c','d'])
    assert widths==[2,1,2]
