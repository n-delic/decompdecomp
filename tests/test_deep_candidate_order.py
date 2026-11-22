from contextlib import contextmanager

import pytest

from decompnet.app import deep_candidate_order, deep_compilation
from decompnet.app.permute import Permuter, TracedVariant

PARENT = 'int f(int x) {return x + 65536;}'
TARGET = bytes.fromhex('3c02000f3442423f03e0000800821021')
EXACT = TracedVariant('int f(int x) {return x + 0xf423fUL;}', ['test'])


@pytest.mark.parametrize('ranked', [False, True])
def test_candidate_order_precedes_compile_admission_and_retains_lineage(monkeypatch, ranked):
    search = Permuter(None)
    search._random_trials_remaining = lambda: 1
    project = {'arch': 'mips', 'big_endian': True}
    function = {'target': TARGET, 'relocs': '[]'}
    notes = []
    if ranked:
        search.candidate_order = deep_candidate_order.for_function(project, function, notes.append)
    original = [PARENT, 'int f(int x) {return x + 999998;}', EXACT]
    admitted = []

    @contextmanager
    def compile_chunk(search, agent, proj, fn, sources, fork):
        admitted.extend(sources)
        yield

    monkeypatch.setattr(deep_compilation, 'chunk', compile_chunk)
    result = list(search._stream_variants((batch for batch in [original]), None, project, function, None, parent=PARENT))
    assert admitted == [EXACT if ranked else PARENT]
    assert result[0 if ranked else 2] is EXACT
    assert bool(notes) == ranked
    assert original[0] == PARENT


def test_candidate_order_supports_little_endian_and_ignores_relocations_and_comments():
    project = {'arch': 'mips', 'big_endian': False}
    function = {'target': bytes.fromhex('0f00023c3f4242340800e00321108200'), 'relocs': '[]'}
    order = deep_candidate_order.for_function(project, function, lambda _: None)
    with_comment = 'int f(int x) {return x + 65536; /* 999999 */}'
    assert order(PARENT, [with_comment, EXACT]) == [EXACT, with_comment]
    assert deep_candidate_order.for_function(project, function | {'relocs': '[[4, 6, 0]]'}, None) is None
    assert deep_candidate_order.for_function({'arch': 'arm'}, function, None) is None
    assert deep_candidate_order.for_function(project, {'target': None}, None) is None


def test_literal_extraction_falls_back_for_unavailable_or_unsupported_inputs(monkeypatch):
    long = ' ' * (256 * 1024) + '65536'
    non_ascii = '/* naïve */ 65536'
    calls = []

    def reject(raw, length, result, capacity):
        calls.append(1)
        return capacity + 1

    monkeypatch.setattr(deep_candidate_order, '_kernel', lambda: reject)
    for source in ('65536', long, non_ascii):
        assert deep_candidate_order._literals(source) == {65536}
    assert len(calls) == 1
    monkeypatch.setattr(deep_candidate_order, '_kernel', lambda: None)
    assert deep_candidate_order._literals('65536') == {65536}


def test_native_literals_and_rank_order_match_python(monkeypatch):
    from random import Random

    kernel = deep_candidate_order._kernel()
    if kernel is None:
        pytest.skip('native alignment library with rank literals has not been built')
    assert deep_candidate_order._native_literals('int f(void) { return 65536; }', kernel) == {65536}
    assert deep_candidate_order._native_literals('/* unterminated 65536', kernel) is None
    tokens = [
        '01234567', '0xfffedcUL', '4294967295', '4294967296', '0X123FFFF', '000000065536', '0999999',
        '0x', '0u', '65535', '65536', '77888ULLl', '_name987654', 'int65536', 'L"65536"', '65536.1',
        '1.8708988', '65537e+100000', '// 1234567', '/* 1234567 */', '"9876543\\"54321"', "'123456'",
        ' ', '/*', '*/', '//', '\n', '"', "'", '\\', '++', '\x00',
    ]
    random = Random(81)
    cases = [' '.join(tokens[:24]), '/* unterminated 65536', '"unterminated 65536',
             '65536' + 'x' + ' ' * (256 * 1024 - 6)]
    cases += [''.join(random.choices(tokens, k=8)) for _ in range(2000)]
    for source in cases:
        assert deep_candidate_order._literals(source) == deep_candidate_order._python_literals(source), repr(source)

    project = {'arch': 'mips', 'big_endian': True}
    function = {'target': TARGET, 'relocs': '[]'}
    candidates = [PARENT, 'int f(int x) {return x + 999998;}', EXACT, 'int f(int x) {return x + 999998UL;}']
    native = deep_candidate_order.for_function(project, function, lambda _: None)(PARENT, candidates)
    monkeypatch.setattr(deep_candidate_order, '_kernel', lambda: None)
    python = deep_candidate_order.for_function(project, function, lambda _: None)(PARENT, candidates)
    assert native == python
    assert native[0] is EXACT
