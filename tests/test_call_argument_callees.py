"""The callees' matched C reaches the library's declared repairs (decomprepair registry's optional `callees`)."""
from decompnet.app import draft_repairs, library_registry
from decomprepair import registry

# A field loaded into $a0, tested and passed to a one-argument callee; the draft calls the callee with nothing.
SOURCE = '''M2C_UNK release(void);
extern s32 D_800B83B4;

s32 f(void *arg0) {
    s32 temp_a0;

    if (M2C_FIELD(arg0, s32 *, 0x68) == -1) {
        temp_a0 = M2C_FIELD(arg0, s32 *, 0xC4);
        if (temp_a0 != 0) {
            release();
            M2C_FIELD(arg0, s32 *, 0xC4) = 0;
        }
        D_800B83B4 += 1;
    }
    return 1;
}
'''
ASM = '''glabel f
/* 0 80001000 00808021 */  addu       $s0, $a0, $zero
/* 4 80001004 8E030068 */  lw         $v1, 0x68($s0)
/* 8 80001008 1462000C */  bne        $v1, $v0, .L80001038
/* C 8000100C 2402FFFF */  addiu      $v0, $zero, -0x1
/* 10 80001010 8E0400C4 */  lw         $a0, 0xC4($s0)
/* 14 80001014 10800005 */  beqz       $a0, .L8000102C
/* 18 80001018 3C03800C */  lui        $v1, %hi(D_800B83B4)
/* 1C 8000101C 0C0094A0 */  jal        release
/* 20 80001020 00000000 */  nop
'''
CALLEES = 'void release(void *arg0) {\n    M2C_FIELD(arg0, s32 *, 4) = 0;\n}\n'
FAMILY = 'missing_call_argument_local'


def _children(**options):
    labels = {}
    out = draft_repairs.variants(SOURCE, 'f', 'mips', ASM, labels=labels, limit=None, **options)
    return [text for text in out if labels.get(text) == FAMILY]


def test_the_draft_pass_gives_the_callees_matched_parameters():
    with_callees = _children(callees=CALLEES)
    assert with_callees and any('M2C_UNK release(void *);' in text and 'release(temp_a0);' in text
                                for text in with_callees)
    alone = _children()
    assert alone and all('M2C_UNK release(s32);' in text for text in alone)  # the local's own type without evidence


def test_the_local_lane_carries_the_callees():
    context = library_registry.draft_context(SOURCE, 'f', '', 'mips', ASM, (), callees=CALLEES)
    [call] = [call for call in library_registry.local_calls(context) if call[1] == (FAMILY,)]
    _, args, kwargs = call
    assert kwargs['callees'] == CALLEES
    assert 'release(void *)' in registry.run(*args, **kwargs)[0][1]
    assert library_registry.draft_context(SOURCE, 'f', '', 'mips', ASM, ())['callees'] == ''
