"""The deep pass drops an argument m2c passed from a register the target only stored or compared before the call."""
from decompnet.app import draft_repairs

SOURCE = '''void put(void);
extern u8 D_X;
extern u8 D_Y;

void f(void) {
    D_Y = D_X;
    put(D_X);
}
'''
ASM = '''glabel f
/* 0 */  lui        $a0, %hi(D_X)
/* 4 */  lbu        $a0, %lo(D_X)($a0)
/* 8 */  lui        $at, %hi(D_Y)
/* c */  sb         $a0, %lo(D_Y)($at)
/* 10 */  jal        put
/* 14 */  nop
'''


def test_a_stored_leftover_argument_is_dropped():
    # Three N64 functions were exact once m2c's leftover $a0 (a byte just stored, a switch's selector) was no longer
    # passed to a callee that takes fewer arguments (decomp-repair 0.5.30, target_call_arity).
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'f', 'mips', ASM, labels=labels, limit=None)
    dropped = [t for t in texts if '    put();' in t]
    assert dropped and labels.get(dropped[0]) == 'target_call_arity'


MULTI = '''void put(void);
void other(void);
extern u8 D_X;
extern u8 D_Y;

void f(void) {
    D_Y = D_X;
    put(D_X);
    other(D_X);
}
'''
MULTI_ASM = '''glabel f
/* 0 */  lui        $a0, %hi(D_X)
/* 4 */  lbu        $a0, %lo(D_X)($a0)
/* 8 */  lui        $at, %hi(D_Y)
/* c */  sb         $a0, %lo(D_Y)($at)
/* 10 */  jal        put
/* 14 */  nop
/* 18 */  lui        $a0, %hi(D_X)
/* 1c */  lbu        $a0, %lo(D_X)($a0)
/* 20 */  sb         $a0, %lo(D_Y)($at)
/* 24 */  jal        other
/* 28 */  nop
'''


def test_a_stray_argument_is_dropped_at_every_callee_together():
    # A leftover argument passed to several one-argument callees: each callee alone scored the same as the draft,
    # only all of them together were exact (decomp-repair 0.5.345, target_call_arity merged child).
    texts = draft_repairs.variants(MULTI, 'f', 'mips', MULTI_ASM, limit=None)
    assert any('put();' in t and 'other();' in t for t in texts)
