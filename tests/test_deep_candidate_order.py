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
