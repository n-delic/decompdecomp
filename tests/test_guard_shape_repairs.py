from decomprepair import guard_shape_repairs as shape

EARLY = '''void f(void *a, f32 b) {
    void *t;

    t = *(void **)((s8 *)a + 0x18);
    if (t == NULL) {
        *(s32 *)((s8 *)a + 0x24) = 0x1E8;
        return;
    }
    *(s32 *)((s8 *)a + 0x24) = (s32)(b * *(f32 *)((s8 *)t + 8));
}
'''

LOOP = '''void *g(void *a, s32 k) {
    void *p;

    p = *(void **)((u8 *)a + 0x64);
    if (p == NULL) {
        return NULL;
    }
    do {
        if (*((u8 *)p + 0x32) == k) {
            return p;
        }
        p = *(void **)p;
    } while (p != NULL);
    return NULL;
}
'''


def test_early_return_becomes_else_branch():
    # An agent rewrote m2c's early return as the if/else the original source had.
    (family, out), = shape.variants(EARLY, 'f')
    assert family == 'guard_shape'
    assert '''    if (t != NULL) {
        *(s32 *)((s8 *)a + 0x24) = (s32)(b * *(f32 *)((s8 *)t + 8));
    } else {
        *(s32 *)((s8 *)a + 0x24) = 0x1E8;
    }
}''' in out
    assert 'return' not in out


def test_entry_guard_and_do_while_become_one_while_loop():
    (family, out), = shape.variants(LOOP, 'g')
    assert 'while (p != NULL) {' in out and 'do {' not in out and out.count('return NULL;') == 1


def test_changes_meaning_are_left_alone():
    # a value returned by the guard differs from the one after the loop
    assert not shape.variants(LOOP.replace('return NULL;\n    }\n    do', 'return a;\n    }\n    do'), 'g')
    # the loop test is not the guard's inverse
    assert not shape.variants(LOOP.replace('while (p != NULL)', 'while (k != 0)'), 'g')
    # a second return inside the early arm, a goto into the rest, or a value-returning function
    assert not shape.variants(EARLY.replace('0x1E8;', '0x1E8; if (b) return;'), 'f')
    assert not shape.variants(EARLY.replace('(s32)(b', 'goto x; x: (s32)(b'), 'f')
    assert not shape.variants(EARLY.replace('return;', 'return 1;'), 'f')
    # an if/else already there
    assert not shape.variants(EARLY.replace('    *(s32 *)((s8 *)a + 0x24) = (s32)', '    else *(s32 *)((s8 *)a + 0x24) = (s32)'), 'f')


def test_ternary_in_the_rest_is_not_mistaken_for_a_label():
    assert shape.variants(EARLY.replace('(s32)(b', 'b ? 1 : (s32)(b'), 'f')
