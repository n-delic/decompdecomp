"""decomprepair 0.5.580: m2c's `goto` into the next `if`'s then arm, written as the else arm it was, reaches the draft repairs and the deep pass."""
from decompnet import seeds
from decompnet.app import draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned int u32;\n'
SOURCE = HEADER + '''s32 *alloc();
void fill();
void use();
void done();
void end();
void f(s32 *arg0) {
    s32 *held;
    s32 *buf;

    held = *(s32 **)((char *)arg0 + 0x34);
    if (held != NULL) {
        goto block_3;
    }
    buf = alloc(8, 1);
    if (buf != NULL) {
        held = buf;
        fill(buf);
block_3:
        use(held);
        goto block_9;
    } else {
block_9:
        done();
    }
    end();
}
'''


def test_the_family_is_known_to_the_agents_and_the_deep_pass():
    assert 'goto_nested_entry_else' in draft_repairs.AGENT_FAMILIES


def test_the_family_is_offered_through_draft_repairs_with_the_shape_of_the_others():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'f', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    moved = [t for t in texts if labels.get(t) == 'goto_nested_entry_else']
    assert moved, sorted(set(labels.values()))
    assert 'goto block_3' not in moved[0] and 'else' in moved[0]
    assert all(isinstance(t, str) for t in texts)


def test_the_family_is_in_the_local_lane_pool_and_the_deep_pass_children_offer_it():
    from decompnet.app import local_experiments
    assert 'goto_nested_entry_else' in local_experiments.FAMILIES
    labels = {}  # deep_permuter.children asks draft_repairs.variants exactly like this
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'f', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    assert 'goto_nested_entry_else' in {labels.get(t) for t in texts}
