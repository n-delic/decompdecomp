"""decomprepair 0.5.603: argument_weight_scope (a parameter's first call in a one-shot scope, the table pointer local
written out) reaches the draft repairs and the deep pass's local lane.

A 110-instruction N64 sound-pan function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) sat at 35 with `$s0` and `$s2`
exchanged between the magnitude and the option table's address: from its saved best the call wrapped with the pointer
written out scores 0 in the isolated judge (the pointer alone 85, the wrapper alone 35); judge-worker replay, no
permuter run."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned char u8;\ntypedef float f32;\n'
SOURCE = HEADER + '''void pan(s32 arg0, void *arg1, s32 arg2)
{
    u8 *__repair_byte_table;
    s32 sp10, mag;
    if (arg0 != -1) {
        locate(arg1, arg2, &sp10);
        mag = angle(sp10, *(s32 *)((u8 *)arg1 + 0x38));
        do { __repair_byte_table = gOptionChoice; } while (0);
        if (__repair_byte_table[0] != 0) {
            set_pan(arg0, mag);
            if (__repair_byte_table[0] >= 2U) {
                set_vol(arg0, mag);
            }
        }
    }
}
'''
FAMILY = 'argument_weight_scope'


def test_the_family_is_known_to_the_agents():
    assert FAMILY in draft_repairs.AGENT_FAMILIES


def test_the_family_is_offered_through_the_draft_repairs_with_the_pointer_written_out():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'pan', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    rewritten = [t for t in texts if labels.get(t) == FAMILY]
    assert rewritten
    assert any('do { locate(arg1, arg2, &sp10); } while (0);' in t and 'gOptionChoice[0] != 0' in t
               and '__repair_byte_table' not in t for t in rewritten)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'pan', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert FAMILY in {family for family, _ in proposals}


LIST_SOURCE = HEADER + '''void walk(s32 arg0)
{
    void *obj;
    void *kid;
    for (obj = gHead; obj != NULL; obj = *(void **)((s8 *)obj + 0x1C)) {
        for (kid = *(void **)((s8 *)obj + 8); kid != NULL; kid = *(void **)((s8 *)kid + 8)) {
            prepare(obj, arg0);
            color(arg0, 0, 0);
            submit(kid, arg0);
        }
        for (kid = *(void **)obj; kid != NULL; kid = *(void **)kid) {
            color(arg0, 1, 0);
            submit_child(kid, obj, arg0);
        }
    }
}
'''


def test_the_draft_repairs_offer_the_parameter_calls_wrapped_together():
    """decomp-repair 0.5.661: the last k calls naming a parameter, each wrapped several deep, are one child (a render-list
    walk went 110 -> 0 from its saved best; no single wrapper moved it)."""
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + LIST_SOURCE, 'walk', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    grouped = [t for t in texts if labels.get(t) == FAMILY
               and 'do { do { do { submit_child(kid, obj, arg0); } while (0); } while (0); } while (0);' in t
               and 'do { do { do { submit(kid, arg0); } while (0); } while (0); } while (0);' in t]
    assert grouped
