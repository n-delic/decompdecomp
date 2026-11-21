from difflib import SequenceMatcher
from itertools import product
from random import Random

import pytest

from decompnet import compile_sessions, instruction_alignment, scoring
from decompnet.elfobj import FunctionObj


def reference(left, right):
    return tuple(SequenceMatcher(a=left, b=right, autojunk=False).get_opcodes())


def test_alignment_cache_reuses_shape_only_inside_a_search(monkeypatch):
    calculate = instruction_alignment._calculate
    calls = []

    def count(left, right):
        calls.append((left, right))
        return calculate(left, right)

    monkeypatch.setattr(instruction_alignment, '_calculate', count)
    monkeypatch.setattr(instruction_alignment, '_kernel', lambda: None)
    left = ['add', 'nop']
    right = ['nop', 'sub']
    with compile_sessions.session() as session:
        expected = reference(left, right)
        assert instruction_alignment.opcodes(left, right) == expected
        assert instruction_alignment.opcodes(left.copy(), right.copy()) == expected
        assert len(calls) == 1
        assert instruction_alignment.opcodes(right, left) == reference(right, left)
        for i in range(130):
            instruction_alignment.opcodes([str(i)], [])
        assert len(session.alignments) == 128
        assert instruction_alignment.opcodes(left, right) == expected
        assert len(calls) == 133
    assert instruction_alignment.opcodes(left, right) == expected
    assert len(calls) == 134


def test_rejected_or_oversized_native_input_uses_python(monkeypatch):
    calls = []

    def reject(*args):
        calls.append(1)
        return 0

    monkeypatch.setattr(instruction_alignment, '_kernel', lambda: reject)
    assert instruction_alignment.opcodes(['add'], ['ret']) == reference(['add'], ['ret'])
    assert calls == [1]
    left = ['ret'] * 8193
    assert instruction_alignment.opcodes(left, ['ret']) == reference(left, ['ret'])
    assert calls == [1]


@pytest.mark.parametrize('failure', [OSError, AttributeError])
def test_unloadable_library_uses_python(monkeypatch, failure):
    def fail(*args):
        raise failure

    with monkeypatch.context() as changes:
        changes.delenv('DECOMP_NATIVE_ALIGNMENT', raising=False)
        changes.setattr(instruction_alignment.ctypes, 'CDLL', fail)
        instruction_alignment._kernel.cache_clear()
        try:
            assert instruction_alignment._kernel() is None
            assert instruction_alignment.opcodes(['add'], ['ret']) == reference(['add'], ['ret'])
        finally:
            instruction_alignment._kernel.cache_clear()


def test_native_matches_python_blocks_and_opcodes():
    kernel = instruction_alignment._kernel()
    if kernel is None:
        pytest.skip('native instruction alignment library has not been built')
    sequences = [tokens for size in range(7) for tokens in product(range(2), repeat=size)]
    rng = Random(7924)
    cases = [
        (tuple(rng.choices(range(12), k=rng.randrange(96))),
         tuple(rng.choices(range(12), k=rng.randrange(96))))
        for _ in range(512)
    ]
    cases.extend([((1, 0) * 354, (0, 1) * 354), ((1,) * 708, (1,) * 707)])
    for left, right in (*product(sequences, repeat=2), *cases):
        matcher = SequenceMatcher(a=left, b=right, autojunk=False)
        blocks = instruction_alignment._native_blocks(left, right, kernel)
        assert blocks == matcher.get_matching_blocks()
        assert tuple(instruction_alignment._from_blocks(blocks)) == tuple(matcher.get_opcodes())


def test_score_and_diff_keep_operand_changes_and_byte_verdicts(monkeypatch):
    kernel = instruction_alignment._kernel()
    if kernel is None:
        pytest.skip('native instruction alignment library has not been built')

    def function(machine_code):
        return FunctionObj('f', 'x86', False, bytes.fromhex(machine_code))

    target = function('b801000000c3')
    candidates = [target, function('b802000000c3'), function('b901000000c3'), function('90b801000000c3')]

    def judge(mode):
        with monkeypatch.context() as changes, compile_sessions.session():
            changes.setattr(instruction_alignment, '_kernel', lambda: mode)
            return [(scoring.diff(target, candidate), scoring.score(target, candidate)) for candidate in candidates]

    native = judge(kernel)
    assert native == judge(None)
    assert [full.bytes_match for full, lean in native] == [True, False, False, False]
    assert [full.score for full, lean in native] == [lean.score for full, lean in native]
