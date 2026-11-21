import pytest

from decompnet.elfobj import FunctionObj, Reloc
from decompnet.rom.link import LinkResult
from decompnet.rom.link_cache import SearchLinks


def test_replay_validates_all_rom_reads_and_copies_results():
    cache = SearchLinks()
    scope, target, symbols = object(), object(), object()
    inputs = {1: b'a', 2: None}
    calls = []

    def read(addr, length):
        return inputs[addr]

    def build(reader):
        calls.append(1)
        values = [reader(addr, 1) for addr in (1, 2)]
        candidate = FunctionObj('f', 'mips', True, b'code', [Reloc(0, 5, 'g', 0)])
        return LinkResult(candidate, values == [b'a', None], placements={'.data': {0: 1}}, linked_code=b'code')

    first = cache.link(scope, target, symbols, b'obj', read, build)
    first.candidate.relocs.clear()
    first.placements['.data'].clear()
    second = cache.link(scope, target, symbols, b'obj', read, build)
    assert second.exact and second.candidate.relocs and second.placements == {'.data': {0: 1}}
    second.problems.append('edited')
    assert not cache.link(scope, target, symbols, b'obj', read, build).problems
    assert len(calls) == 1

    inputs[2] = b'b'
    assert not cache.link(scope, target, symbols, b'obj', read, build).exact
    assert len(calls) == 2

    def unavailable(*args):
        raise ValueError('overlay missing')

    with pytest.raises(ValueError, match='overlay missing'):
        cache.link(scope, target, symbols, b'obj', unavailable, build)


def test_cache_does_not_span_objects_targets_symbols_or_search_inputs():
    cache = SearchLinks()
    scope, target, symbols = object(), object(), object()
    calls = []

    def build(reader):
        calls.append(1)
        return LinkResult(None, False)

    def lookup(*key):
        return cache.link(*key, lambda *_: None, build)

    lookup(scope, target, symbols, b'obj')
    lookup(scope, target, symbols, b'obj2')
    lookup(scope, target, symbols, b'obj')
    assert len(calls) == 2
    lookup(scope, object(), symbols, b'obj')
    lookup(scope, target, object(), b'obj')
    lookup(object(), target, symbols, b'obj')
    assert len(calls) == 5


def test_judge_reuses_links_only_in_a_search_session():
    from decompnet.app.engine.judging import JudgeMixin, search_evaluation
    from decompnet.compile_sessions import session

    target, symbols = object(), object()
    calls = []

    class Console:
        def link_function(self, obj, name, target, symbols, read):
            calls.append(obj)
            return LinkResult(FunctionObj(name, 'mips', True, b'code'), True)

    console = Console()

    class Judge(JudgeMixin):
        revision = 1

        def is_rom(self, project):
            return True

        def _diagnostic_scope(self, fn, proj):
            return self.revision

        def _rom_target(self, fn):
            return target

        def symbols(self, project):
            return symbols

        def console(self, proj):
            return console

        def reader(self, proj, fn):
            return lambda *_: None

        def _link_diagnostics(self, fn, proj, obj, result, target):
            return result.candidate, result.exact, result.problems

    judge = Judge()
    fn, proj = {'id': 2, 'name': 'f'}, {'id': 1, 'root': '/demo'}
    with session():
        with search_evaluation():
            assert judge._evaluate(fn, proj, b'object')[1]
            assert judge._evaluate(fn, proj, b'object')[1]
            assert len(calls) == 1
            judge.revision += 1
            assert judge._evaluate(fn, proj, b'object')[1]
            assert len(calls) == 2
        assert judge._evaluate(fn, proj, b'object')[1]
        assert len(calls) == 3
    with session(), search_evaluation():
        assert judge._evaluate(fn, proj, b'object')[1]
    assert len(calls) == 4
