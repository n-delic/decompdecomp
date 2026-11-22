"""decomprepair 0.5.589's `flag_word_bitfield` reaches drafts, the agents and the Deep Permuter's local lane.

A 34-instruction N64 object placer (KMC GCC 2.7.2) went 15 -> 0 in the isolated judge (judge-worker, no permuter run)
from its saved source through the family's chain child: the masked flag word and the byte stored over it as bit-fields,
the volatile pointer dropped, and the constant scale store moved ahead of the coordinate stores (the bit-field alone
775, with the drop 265). This test uses a synthetic object, not the game's code."""
from decompnet.app import draft_repairs
from decomprepair import flag_word_bitfield_repairs

SOURCE = '''typedef struct {
    u8 pad[0x1e];
    s16 scale;
    u16 x, y;
    u32 flags;
} Obj;
extern Obj *gObj;
extern u16 gX, gY;
extern volatile u16 gC;
void place(void) {
    volatile Obj *obj;
    u16 x, y; u8 color;
    u32 flags;
    obj = gObj;
    x = gX;
    y = gY;
    flags = obj->flags;
    obj->x = x;
    color = gC;
    obj->scale = 0x1000;
    obj->y = y;
    obj->flags = (flags & ~0xfc) | 0x1c;
    *(volatile u8 *)&obj->flags = color;
}
'''


def _chain(text):
    return ('flags_set = 7;' in text and 'volatile Obj' not in text
            and text.index('obj->scale = 0x1000;') < text.index('obj->x = x;'))


def test_the_family_is_known_to_the_agents():
    assert flag_word_bitfield_repairs.FAMILY in draft_repairs.AGENT_FAMILIES


def test_drafts_and_the_beam_propose_the_chain():
    labels = {}
    out = draft_repairs.variants(SOURCE, 'place', 'mips', labels=labels, limit=None)
    assert any(labels.get(v) == flag_word_bitfield_repairs.FAMILY and _chain(v) for v in out)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from decompnet.app import deep_permuter
    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'place', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert any(family == flag_word_bitfield_repairs.FAMILY and _chain(text) for family, text in proposals)
