"""The three repairs a hand match of a queued breadth-first walk (N64, gcc 2.7.2) found, offered through draft_repairs."""
from decompnet.app import draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
SOURCE = '''
s32 call();
void walk(s32 arg0, void *arg1, s32 arg2) {
    void *sp10[0x20];
    void **var_s2;
    s32 var_s4;
    u32 var_s3;
    u32 var_s6;
    u32 temp_s7;
    void *var_s0;
    var_s4 = arg2;
    var_s6 = 0;
    var_s2 = sp10;
    var_s0 = arg1;
    do {
        *var_s2++ = var_s0;
        var_s6++;
        var_s0 = *(void **) var_s0;
    } while (var_s0);
    temp_s7 = var_s6;
    var_s3 = 0;
    if (var_s3 < temp_s7) {
        do {
            call(sp10[var_s3], var_s4 | 0x4000);
            var_s3++;
        } while (var_s3 < temp_s7);
    }
}
'''


def _labelled(source):
    labels = {}
    out = draft_repairs.variants(source, 'walk', 'mips', '', FLAGS, labels, limit=None)
    return {labels.get(text): text for text in out}


def test_queue_cursor_guarded_loop_and_parameter_copy_are_offered():
    found = _labelled(SOURCE)
    assert 'sp10[var_s6] = var_s0;' in found['queue_cursor_index'] and 'var_s2' not in found['queue_cursor_index']
    assert 'for (; var_s3 < temp_s7; var_s3++)' in found['guarded_do_for']
    assert 'arg2 | 0x4000' in found['parameter_copy_drop'] and 'var_s4' not in found['parameter_copy_drop']
