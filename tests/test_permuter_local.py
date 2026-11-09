from decompnet.app.permuter_local import variants


def test_adjacent_constant_stores_preserve_exact_lines_and_other_functions():
    text = 'void other(void){return;}\nvoid f(Item *p) {\n  p->y = 0;\n  p->x = 0;\n}\n'
    assert variants(text, 'f') == [('adjacent_constant_stores', text.replace('  p->y = 0;\n  p->x = 0;', '  p->x = 0;\n  p->y = 0;'))]


def test_does_not_cross_calls_blocks_values_or_volatile_code():
    for middle in ('  call();\n', '}\n{\n'):
        assert not variants('void f(Item *p) {\n  p->y = 0;\n' + middle + '  p->x = 0;\n}', 'f')
    assert not variants('void f(Item *p) {\n  p->y = 1;\n  p->x = 0;\n}', 'f')
    assert not variants('void f(volatile Item *p) {\n  p->y = 0;\n  p->x = 0;\n}', 'f')


def test_shift_add_is_local_and_leaves_comments_and_strings_opaque():
    text = 'int f(int sum) {\n  // sum + (sum << 5)\n  return sum + (sum << 5);\n}'
    result = variants(text, 'f')
    assert len(result) == 1 and result[0][0] == 'shift_add_order'
    assert '// sum + (sum << 5)' in result[0][1]
    assert 'return (sum << 5) + sum;' in result[0][1]


def test_probe_count_bounded_and_unchanged_trials_cached():
    import time

    from decompnet.app.permute import State
    from tests.test_permute import AGENT, FakeNet, ScriptedPermuter
    source = 'void f(Item *p) {\n' + ''.join(f'  p->v{i} = 0;\n' for i in range(20)) + '}\n'
    assert len(variants(source, 'f')) == 8
    net = FakeNet()
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=? WHERE id=1', (source,))
    p = ScriptedPermuter(net, {})
    proj, fn, state = net.project_row('game'), net.function(), State()
    for _ in range(2):
        assert p._local_order(AGENT, proj, fn, state, None, time.monotonic() + 10, 40, source) == (40, source)
    assert len(p.judged) == 8 and state.repairs == 8
    state.stop.set()
    p._local_order(AGENT, proj, fn, state, None, time.monotonic() + 10, 40, source)
    assert len(p.judged) == 8
