"""decomprepair 0.5.334's loop_goto_form reaches the repairs the deep pass and autopilot try."""
from decompnet.app import draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned int u32;\n'
SOURCE = '''void g(s32 *);
void f(s32 *cur, s32 n) {
    do {
        if (n > 4) {
            break;
        }
        cur[0] = 0xDA380003;
        g(cur);
        cur += 2;
    } while (--n != 0);
}
'''


def test_family_is_known_to_the_agents():
    assert 'loop_goto_form' in draft_repairs.AGENT_FAMILIES


def test_a_do_while_is_offered_as_a_goto_loop():
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'f', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    goto = [t for t in texts if labels.get(t) == 'loop_goto_form']
    assert goto and 'goto loop_goto_1;' in goto[0] and 'do {' not in goto[0], sorted(set(labels.values()))


def test_shared_loop_update_is_offered():
    source = """void f(s32 *node) {
        s32 *next;
        do {
            next = node + 1;
            if (*node) {
                node = next;
                continue;
            }
            *node = 3;
            node = next;
        } while (*node);
    }"""
    assert 'loop_shared_update' in draft_repairs.AGENT_FAMILIES
    labels = {}
    texts = draft_repairs.variants(source, 'f', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    shared = [text for text in texts if labels.get(text) == 'loop_shared_update']
    assert len(shared) == 1
    assert 'goto loop_update_1;' in shared[0]
    assert 'loop_update_1: node = next;' in shared[0]
