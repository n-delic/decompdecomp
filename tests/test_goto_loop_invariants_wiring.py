"""decomprepair's `goto_loop_invariants` reaches the drafts, the agents' families and the deep permuter's local lane.

m2c's `again: ... if (c) goto again;` with locals cached from a parameter becomes a real loop with the values written at
their uses (an 89-instruction N64 voice function, 45 -> 0 with the app's judge). Without the wiring none of its
variants is ever proposed."""
import inspect

from decompnet.app import deep_permuter, draft_repairs

SOURCE = '''void lock(void);
s32 emit(s32, s32, u8, s32);
extern s32 gNext, gOwner;
s32 walk(s32 handle, s32 word) {
    s32 index, value = word;
    u32 low;
    if (handle != -1) {
        low = value & 0x7f;
    again:
        index = handle & 0xff;
        value = index * 0x1a0;
        if (*(s32 *)((char *)&gOwner + value) != handle) return -1;
        emit(1, index, 0, low & 0xff);
        emit(2, index, 0, low & 0xff);
        handle = *(s32 *)((char *)&gNext + index * 0x1a0);
        if (handle != -1) goto again;
    }
    return 0;
}
'''


def test_the_drafts_propose_the_loop_form():
    labels = {}
    result = draft_repairs.variants(SOURCE, 'walk', 'mips', labels=labels, limit=None)
    assert 'goto_loop_invariants' in draft_repairs.AGENT_FAMILIES
    loops = [text for text in result if labels.get(text) == 'goto_loop_invariants']
    assert loops and '} while (handle != -1);' in loops[0] and 'goto again' not in loops[0]


def test_the_deep_permuters_local_lane_calls_the_family():
    assert "'goto_loop_invariants.variants'" in inspect.getsource(deep_permuter)
