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
