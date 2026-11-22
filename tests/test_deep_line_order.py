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
    assert after.focus(PARENT) == [3] and after.focus(None) == [] and asked == [PARENT]
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
    assert source_lines.debug_twin_cmd(cmd, 'ido-7.1') is None  # IDO's objects carry lines already; its -g* change the code
    mw = ['wibo', 'mwccps2.exe', '-c', '-O2', '{src}', '-o', '{obj}']
    assert source_lines.debug_twin_cmd(mw, 'mwcps2-2.3.3') == ['wibo', 'mwccps2.exe', '-c', '-O2', '-sym', 'on', '{src}', '-o', '{obj}']
    assert source_lines.debug_twin_cmd([*mw[:4], '-sym', 'on', *mw[4:]], 'mwcps2-2.3.3') is None
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


def test_line_object_compiles_the_twin_with_the_project_flags_plus_g_and_keeps_failures_quiet(monkeypatch):
    from decompnet import network, source_lines
    from decompnet.compiler import CompilerConfig
    seen = []

    Invocation = CompilerConfig(cmd=['gcc', '-O2', '-c', '{src}', '-o', '{obj}'], identity=('shared',))

    def fake_compile(invocation, header, source):
        seen.append((invocation.cmd, invocation.identity))
        return SimpleNamespace(ok=True, obj=b'twin')

    monkeypatch.setattr(network.Network, '_invocation', staticmethod(lambda proj, fn: ({'compiler': {'toolchain': 'gcc-2.7.2'}}, Invocation)))
    monkeypatch.setattr(network, 'compile_source', fake_compile)
    monkeypatch.setattr(source_lines, 'locations', lambda data, name, filename: [(0, 4, 1)] if data == b'twin' else [])
    monkeypatch.setattr(source_lines, 'same_code', lambda a, b, name: True)
    scored = SimpleNamespace(obj=b'scored', language='c')
    got = network.Network.line_object(None, {}, 'h', 'c', {'name': 'f'}, scored)
    assert got == b'twin'
    assert seen == [(['gcc', '-O2', '-c', '-g', '{src}', '-o', '{obj}'], None)]
    monkeypatch.setattr(network, 'compile_source', lambda *a: (_ for _ in ()).throw(RuntimeError('boom')))
    assert network.Network.line_object(None, {}, 'h', 'c', {'name': 'f'}, scored) is None
    monkeypatch.setattr(source_lines, 'same_code', lambda a, b, name: False)
    monkeypatch.setattr(network, 'compile_source', fake_compile)
    assert network.Network.line_object(None, {}, 'h', 'c', {'name': 'f'}, scored) is None  # code differs: no map


def test_ido_objects_already_carry_the_lines_the_editor_and_permuter_use():
    """Real IDO (skips without it): plain -O2 has an ECOFF line table, and -g3 would change the code (so no twin)."""
    import subprocess
    import tempfile
    from pathlib import Path

    import pytest

    from decompnet import toolchains
    if not toolchains.installed('ido-7.1'):
        pytest.skip('IDO 7.1 is not installed')
    cc = toolchains.path('ido-7.1') / 'cc'
    with tempfile.TemporaryDirectory() as work:
        Path(work, 'func.c').write_text('int g(int);\nint f(int a, int b) {\n    int c = a * 3;\n    if (b > c) {\n        c = g(b) + a;\n    }\n    return c + b * 5;\n}\n')
        out = {}
        for flag in ('', '-g3'):
            args = [str(cc), '-c', '-O2', '-mips2', '-G', '0', '-non_shared', *([flag] if flag else []), 'func.c', '-o', f'f{flag}.o']
            if subprocess.run(args, cwd=work, capture_output=True).returncode:
                pytest.skip('IDO does not run here')
            out[flag] = Path(work, f'f{flag}.o').read_bytes()
    assert {line for _, _, line in source_lines.locations(out[''], 'f')} >= {3, 4, 5, 7}
    assert not source_lines.same_code(out[''], out['-g3'], 'f')


def test_locator_keeps_a_recorded_map_and_builds_one_once_when_there_is_none(monkeypatch):
    from decomprepair import repair_localisation
    asked = []
    info = {'available': True, 'lines': [{'line': 2, 'severity': 'different', 'instructions': []}], 'scalar_registers': {2: '$v0'}}
    monkeypatch.setattr(order, 'line_info', lambda net, proj, fn, source: asked.append(source) or info)
    recorded = repair_localisation.bind(PARENT, {'available': True, 'lines': [{'line': 3}]})
    bare = repair_localisation.bind('other', {'available': False, 'lines': []})
    located = order.locator(fake_net(), {}, {}, {PARENT: recorded, 'other': bare, 'unseen': None})
    assert located(PARENT) is recorded and asked == []
    made = located('other')
    assert asked == ['other'] and repair_localisation.lines('other\n\n', made) == set()  # (bound to its own text)
    assert made['mapping']['available'] and made['mapping']['lines'] == [{'line': 2}]
    assert located('other') is made and located('unseen')['mapping']['lines'] == [{'line': 2}] and asked == ['other', 'unseen']


def test_locator_falls_back_to_the_recorded_map_when_the_twin_gives_nothing_or_fails(monkeypatch):
    from decomprepair import repair_localisation
    recorded = repair_localisation.bind('a', {'available': False, 'lines': []})
    monkeypatch.setattr(order, 'line_info', lambda *a: None)
    assert order.locator(fake_net(), {}, {}, {'a': recorded})('a') is recorded

    def broken(*a):
        raise ValueError('no compiler')
    monkeypatch.setattr(order, 'line_info', broken)
    assert order.locator(fake_net(), {}, {}, {'a': recorded})('a') is recorded
    assert order.locator(SimpleNamespace(), {}, {}, {'a': recorded})('a') is recorded  # no ROM judge: untouched


def test_thin_keeps_every_marked_candidate_and_a_share_of_the_rest_only_for_a_wide_batch():
    marked = [PARENT.replace('b * 2', f'b * {n}') for n in range(3, 6)]
    others = [PARENT.replace('a + 1', f'a + {n}') for n in range(2, 11)]
    focus = {3: order.DIFFERENT}
    kept = order.thin(PARENT, others + marked, focus)
    assert kept[:3] == marked and len(kept) == 3 + max(1, len(others) // order.EXPLORE) and set(kept[3:]) <= set(others)
    assert order.thin(PARENT, others[:5] + marked[:2], focus)[:2] == marked[:2] and len(order.thin(PARENT, others[:5] + marked[:2], focus)) == 7
    assert order.thin(PARENT, others, focus) == others  # none marked: left in order
    assert order.thin(PARENT, others + marked, {}) == others + marked


LAYOUT = 'int f(int a) {\n  int c = a * 3; /* scale */\n  if (a > c)\n  {\n    c = g(a) + 1;\n  }\n  return c;\n}\n'
REPRINT = 'int f(int a)\n{\n    int c = a * 3;\n    if (a > c) {\n        c = g(a) + 1;\n    }\n    return c;\n}\n'


def test_touched_lines_ignore_the_layout_the_permuter_reprints_with():
    assert order.touched_lines(LAYOUT, REPRINT) == set()
    assert order.touched_lines(LAYOUT, REPRINT.replace('a * 3', 'a << 1')) == {2}
    assert order.touched_lines(LAYOUT, REPRINT.replace('g(a) + 1', 'g(a) + 2')) == {5}
    # so the thinning no longer counts a re-indented candidate as touching every line
    reprinted = [REPRINT.replace('+ 1', f'+ {n}') for n in range(2, 12)] + [REPRINT] * 2
    assert len(order.thin(LAYOUT, reprinted, {5: order.DIFFERENT})) == 11  # 10 edit line 5, one in 4 of the 2 that edit nothing


def test_two_edits_on_different_lines_merge_and_overlapping_or_wholesale_ones_do_not():
    left, right = REPRINT.replace('a * 3', 'a << 1'), REPRINT.replace('g(a) + 1', 'g(a) + 2')
    merged = order.merge(LAYOUT, left, right)
    assert 'a << 1' in merged and 'g ( a ) + 2' in merged and merged.count('\n') >= 5
    assert order.merge(LAYOUT, left, REPRINT.replace('a * 3', 'a * 5')) is None  # the same statement
    assert order.merge(LAYOUT, left, LAYOUT) is None and order.merge(LAYOUT, left, left.replace('a << 1', 'a << 1')) is None
    tried = set()
    assert order.crossovers(LAYOUT, [left, right, LAYOUT], 3, tried) == [merged]
    assert order.crossovers(LAYOUT, [left, right], 3, tried) == []  # (each pair once)


def test_widen_follows_the_variables_on_a_marked_line_back_to_where_they_are_set():
    source = 'int f(int a, int b) {\n  int c;\n  int d;\n  c = a * 3;\n  d = b + 1;\n  return c + g(d);\n}\n'
    assert order.widen(source, {6: order.DIFFERENT}, 1) == {6: 2, 4: 1, 5: 1}
    assert order.widen(source, {5: order.DIFFERENT}, 2) == {5: 2}  # b is a parameter: nothing sets it
    assert order.widen(source, {}, 1) == {} and order.widen(source, {99: 1}, 1) == {99: 1}


def test_shape_says_what_the_marked_statements_are_made_of():
    source = 'void f(S *s, int a) {\n  s->x = g(a) * 3;\n  if (a > 2) {\n    s->y[a] = a << 2;\n  }\n}\n'
    assert order.shape(source, [2]) == 'call+mul+field'
    assert order.shape(source, [3]) == 'cond'
    assert order.shape(source, [99]) == '' and order.shape('void f(void) {\n  x = y;\n}\n', [2]) == 'plain'


def test_outcomes_tell_whether_marked_lines_pay_and_which_rewrites_change_nothing():
    for name in ('_seen', '_parent_output', '_dead', '_alive', 'stats'):
        getattr(order, name).clear()
    marked, other = REPRINT.replace('a * 3', 'a << 1'), REPRINT.replace('g(a) + 1', 'g(a) + 2')
    focus = {2: order.DIFFERENT}
    key = order._key(LAYOUT)
    order._parent_output[key] = 'same'
    from decompnet.app.permute import TracedVariant
    tagged = [TracedVariant(marked + f'/*{n}*/', ['perm_x']) for n in range(order.DEAD_AFTER)]
    order.register(LAYOUT, [*tagged, other], focus)
    assert order.stats['written_marked'] == order.DEAD_AFTER and order.stats['written_other'] == 1
    order.outcome(other, True, 'changed')
    for candidate in tagged:
        assert not order.dead(LAYOUT, candidate)
        order.outcome(candidate, False, 'same')  # compiled to the parent's own code, and no better
    assert order.stats['compiled_marked'] == order.DEAD_AFTER and order.stats['compiled_other'] == 1
    assert order.dead(LAYOUT, TracedVariant(marked, ['perm_x'])) and not order.dead(LAYOUT, TracedVariant(marked, ['perm_y']))
    assert order.stats['no_effect'] == order.DEAD_AFTER
    assert order.summary() == '' or 'improved' in order.summary()
    order.stats['compiled_marked'] = 40
    assert 'on marked lines improved' in order.summary() and 'elsewhere' in order.summary()
    for name in ('_seen', '_parent_output', '_dead', '_alive', 'stats'):
        getattr(order, name).clear()


def test_the_order_widens_a_stalled_parent_and_leaves_dead_rewrites_out(monkeypatch):
    for name in ('_seen', '_parent_output', '_dead', '_alive', 'stats'):
        getattr(order, name).clear()
    source = 'int f(int a) {\n  int c;\n  c = a * 3;\n  return g(c);\n}\n'
    notes = []
    after = order.for_function(fake_net(), {}, {}, notes.append, finder=lambda *a: ({4: order.DIFFERENT}, 'out'))
    assert after.focus(source) == [4] and after.shape(source) == 'call'
    assert after.focus(source) == [4] and after.focus(source) == [3, 4]  # (the third batch from it with no improvement)
    assert any('following the variables' in note for note in notes)
    assert after(source, [source]) == [source]


def test_prefixed_batches_yield_the_merges_first_and_close_what_they_wrap():
    class Stream:
        closed = False

        def __iter__(self):
            return iter(['c', 'd'])

        def close(self):
            self.closed = True
    stream = Stream()
    prefixed = order.Prefixed(['a', 'b'], stream)
    assert list(prefixed) == ['a', 'b', 'c', 'd']
    prefixed.close()
    assert stream.closed


def test_a_sweep_takes_near_misses_first_and_keeps_the_shuffled_order_among_equals():
    from decompnet.app.deep_sweep import closeness_order
    details = {1: {'best_score': 900, 'n_insns': 40}, 2: {'best_score': 4, 'n_insns': 200}, 3: {'best_score': None, 'n_insns': 0},
               4: {'best_score': 6, 'n_insns': 200}, 5: {'best_score': 60, 'n_insns': 100}}
    rows = [{'id': n} for n in (1, 2, 3, 4, 5)]
    assert [r['id'] for r in closeness_order(rows, details)] == [2, 3, 4, 5, 1]
    assert [r['id'] for r in closeness_order(rows[::-1], details)] == [4, 3, 2, 5, 1]
    assert closeness_order([], details) == [] and [r['id'] for r in closeness_order([{'id': 9}], {})] == [9]


def test_the_policy_scales_boosts_by_a_shape_key_and_counts_a_trial_once(monkeypatch):
    from decompnet.app import adaptive_repairs
    seen = []
    monkeypatch.setattr(adaptive_repairs, 'profile', lambda *a: 'p')
    monkeypatch.setattr(adaptive_repairs, 'factors', lambda net, key, focus: seen.append(focus) or {'perm_x': 2.0})
    boosted = adaptive_repairs.with_shape(None, {}, {}, {'perm_x': 1.5, 'perm_y': 1.0}, 'register', 'call+field')
    assert seen == ['register@call+field'] and boosted == {'perm_x': 3.0, 'perm_y': 1.0}
    calls = []
    monkeypatch.setattr(adaptive_repairs, 'prepare', lambda net: None)
    monkeypatch.setattr(adaptive_repairs.trial_bookkeeping, 'count', lambda *a, **k: calls.append(a[1].split()[2]))
    adaptive_repairs.record(SimpleNamespace(store=None), 'p', 'register@call', 'perm_x', 5, 3, 10, coverage=False)
    adaptive_repairs.record(SimpleNamespace(store=None), 'p', 'register', 'perm_x', 5, 3, 10)
    assert calls == ['repair_policy', 'repair_policy', 'repair_policy_coverage']
