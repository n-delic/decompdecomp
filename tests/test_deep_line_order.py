from types import SimpleNamespace

from decompnet import source_lines
from decompnet.app import deep_line_order as order

PARENT = 'int f(int a) {\n  int b = a + 1;\n  int c = b * 2;\n  return c;\n}'


def test_touched_lines_cover_edits_deletions_and_insertions():
    assert order.touched_lines(PARENT, PARENT.replace('b * 2', 'b << 1')) == {3}
    assert order.touched_lines(PARENT, PARENT.replace('  int c = b * 2;\n', '')) == {3}
    assert order.touched_lines(PARENT, PARENT.replace('  return c;', '  c += 0;\n  return c;')) == {3, 4}


def test_rank_puts_edits_of_marked_lines_first_and_keeps_the_rest_in_order():
    elsewhere, marked, other = PARENT.replace('a + 1', 'a + 2'), PARENT.replace('b * 2', 'b << 1'), PARENT.replace('return c', 'return b')
    assert order.rank(PARENT, [elsewhere, other, marked], {3: order.DIFFERENT}) == [marked, elsewhere, other]
    # a different instruction outweighs a differing operand
    both = PARENT.replace('b * 2', 'b << 1').replace('a + 1', 'a + 2')
    assert order.rank(PARENT, [elsewhere, marked, both], {2: order.OPERAND, 3: order.DIFFERENT})[0] is both
    assert order.rank(PARENT, [elsewhere, marked], {}) == [elsewhere, marked]


def fake_net():
    return SimpleNamespace(_evaluate=1, _target_obj=1, line_object=1, _compile=1)


def test_order_asks_once_per_parent_logs_once_and_chains_the_next_order():
    asked, notes = [], []
    marked, other = PARENT.replace('b * 2', 'b << 1'), PARENT.replace('a + 1', 'a + 2')

    def finder(net, proj, fn, source):
        asked.append(source)
        return {3: order.DIFFERENT}

    after = order.for_function(fake_net(), {}, {}, notes.append, then=lambda parent, candidates: sorted(candidates), finder=finder)
    assert after(PARENT, [other, marked]) == [marked, other]
    assert after(PARENT, [other, marked]) == [marked, other]
    assert asked == [PARENT] and len(notes) == 1 and 'line 3' in notes[0]
    assert after(None, [other, marked]) == sorted([other, marked])


def test_a_failing_or_empty_map_leaves_the_order_alone():
    notes = []

    def broken(*_):
        raise ValueError('no compiler')

    after = order.for_function(fake_net(), {}, {}, notes.append, finder=broken)
    assert after(PARENT, ['b', 'a']) == ['b', 'a'] and 'skipped' in notes[0]
    assert order.for_function(SimpleNamespace(), {}, {}, notes.append) is None  # a project that can't judge this way


def test_debug_twin_only_for_gcc_that_has_no_debug_flag_yet():
    cmd = ['gcc', '-O2', '-c', '{src}', '-o', '{obj}']
    assert source_lines.debug_twin_cmd(cmd, 'gcc-2.7.2') == ['gcc', '-O2', '-c', '-g', '{src}', '-o', '{obj}']
    assert source_lines.debug_twin_cmd([*cmd[:2], '-g', *cmd[2:]], 'gcc-2.7.2') is None
    assert source_lines.debug_twin_cmd(cmd, 'ido-7.1') is None
    assert source_lines.debug_twin_cmd(['gcc', '-c'], 'gcc-2.7.2') is None


def test_neutral_object_trusts_a_twin_only_when_its_code_is_identical(monkeypatch):
    spans = {b'plain': [], b'same': [(0, 4, 1)], b'other': [(0, 4, 1)]}
    monkeypatch.setattr(source_lines, 'locations', lambda data, name, filename: spans[data])
    monkeypatch.setattr(source_lines, 'same_code', lambda a, b, name: b == b'same')
    assert source_lines.neutral_object(b'plain', 'f', 'func.c', lambda: b'same') == b'same'
    assert source_lines.neutral_object(b'plain', 'f', 'func.c', lambda: b'other') is None
    assert source_lines.neutral_object(b'plain', 'f', 'func.c', lambda: None) is None
    spans[b'plain'] = [(0, 4, 1)]
    assert source_lines.neutral_object(b'plain', 'f', 'func.c', lambda: 1 / 0) == b'plain'
