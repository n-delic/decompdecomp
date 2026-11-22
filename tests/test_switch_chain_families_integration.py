"""The families a large m2c switch function needed, offered through the app's own proposal path."""
from decompnet.app import draft_repairs

NESTED = '''void f(s32 k, s32 a, s32 b)
{
    s32 v;
    s32 r;
    switch (k)
    {
        case 1:
            if (a == 0) {
                if (b != 0) {
                    v = first();
block_1:
                    if (v == 0) {
                        goto block_2;
                    }
                } else {
                    r = second();
block_2:
                    third(r);
                }
            }
            break;
        case 2:
            if (a != 0) {
                if (b == 0) {
                    goto block_2;
                }
                v = other();
                goto block_1;
            }
            break;
        case 3:
            fourth();
            break;
    }
}
'''

PAIRED = '''void f(s32 k, u8 *rec, void *ctx)
{
    s32 v;
    switch (k)
    {
        case 1:
            v = M2C_FIELD(rec, u8 *, 0x1E) + 0x6F;
            send(ctx, v & 0xFF);
            break;
        case 2:
            v = M2C_FIELD(rec, u8 *, 0x1E) + 0x6D;
            send(ctx, v & 0xFF);
            break;
    }
}
'''

WIDENED = '''void f(void *rec)
{
    if (((s32) M2C_FIELD(rec, u16 *, -0x10) == 0) && (M2C_FIELD(rec, u16 *, -0xC) == 0)) {
        go();
    }
}
'''

FOLDED = '''void f(u16 *start, s32 count)
{
    u16 *rec;
    u16 *field;
    rec = start;
    field = rec + 0x1E;
    do {
        use(M2C_FIELD(field, u16 *, -0x18), M2C_FIELD(field, s32 *, 0), M2C_FIELD(rec, u16 *, 0));
        rec += 0x22;
        field = rec + 0x1E;
        count -= 1;
    } while (count > 0);
}
'''

STAGED = '''void f(void)
{
    struct { f32 a, b, c; s16 x, y, z; } L;
    other(&L);
    L.x = (s16) (s32) L.a;
    L.y = (s16) (s32) L.b;
    call(&L.x);
}
'''

SPILLS = '''void f(s32 n)
{
    struct {
        f32 a, b;
        u8 pad8[8];
        volatile s32 result;
        u8 padD0[4];
        u16 *volatile buffer;
        u8 padE0[24];
    } L;
    u8 *walk;
    L.result = first(n);
    L.buffer = alloc(n);
    {
        u8 *dest = L.buffer;
        walk = L.buffer;
        fill(dest, n);
    }
    loop(walk);
    done(L.result);
}
'''

SINK = '''void f(s32 n)
{
    u8 *buffer;
    u8 *walk;
    buffer = alloc(n);
    {
        u8 *dest = buffer;
        walk = buffer;
        fill(dest, n);
    }
    loop(walk);
}
'''

ASM = '''glabel f
/* 000000 80000000 3C014270 */  lui        $at, (0x42700000 >> 16)
/* 000004 80000004 44816000 */  mtc1       $at, $fa0
/* 000008 80000008 0C000100 */  jal        step
/* 00000C 8000000C 00000000 */   nop
/* 000010 80000010 03E00008 */  jr         $ra
'''

FLOATED = '''extern void step(void);
void f(void)
{
    step();
}
'''


def offered(source, family, asm=''):
    labels = {}
    children = draft_repairs.variants(source, 'f', 'mips', asm, ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2'], labels, limit=None)
    return [text for text in children if labels.get(text) == family]


def test_every_family_is_routed_to_the_agents_and_the_permuter():
    for family in ('nested_join_hoist', 'paired_call_argument_inline', 'widened_field_read', 'float_register_argument',
                   'derived_pointer_fold', 'staged_narrow_store', 'volatile_slot_demotion', 'copy_sink_after_call'):
        assert family in draft_repairs.AGENT_FAMILIES


def test_draft_repairs_offer_the_join_hoist_and_the_inlined_arguments():
    assert any('goto block_1;' in text and text.index('block_1:') > text.index('case 2:') for text in offered(NESTED, 'nested_join_hoist'))
    assert any('send(ctx, (M2C_FIELD(rec, u8 *, 0x1E) + 0x6F) & 0xFF);' in text for text in offered(PAIRED, 'paired_call_argument_inline'))


def test_draft_repairs_offer_the_wide_read_and_the_folded_pointer():
    assert any('M2C_FIELD(rec, s32 *, -0x10) == 0' in text for text in offered(WIDENED, 'widened_field_read'))
    assert any('use(M2C_FIELD(rec, u16 *, 0x24)' in text and 'field' not in text for text in offered(FOLDED, 'derived_pointer_fold'))


def test_draft_repairs_offer_staged_stores_demoted_slots_and_the_sunk_copy():
    assert any('__repair_narrow_0 = (s16) (s32) L.a;' in text for text in offered(STAGED, 'staged_narrow_store'))
    assert any('volatile' not in text and '__repair_slot_buffer' in text for text in offered(SPILLS, 'volatile_slot_demotion'))
    assert any('    }\n    walk = buffer;\n    loop(walk);' in text for text in offered(SINK, 'copy_sink_after_call'))


def test_the_float_the_target_loads_into_f12_is_offered_as_the_argument():
    [text] = offered(FLOATED, 'float_register_argument', ASM)
    assert 'step(60.0f);' in text and 'step(f32);' in text
