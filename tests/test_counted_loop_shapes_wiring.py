"""decomprepair 0.5.593's `counted_loop_shapes` reaches drafts, the agents and the Deep Permuter's local lane.

A four-loop N64 table loader (KMC GCC 2.7.2, `-O2`) went 99 -> 0 in the isolated judge (judge-worker, no permuter run)
from its saved source through the family's chain child: the pointer walk as an index, strides from the counter, one
shared counter, the guarded do/whiles as `for` loops with the unused frame array dropped, the sum in the scaled
register, the integer address sum, the product shifted in its arm and the per-operand limit casts. This test uses a
synthetic function, not the game's code."""
from decompnet.app import draft_repairs
from decomprepair import counted_loop_shapes

SOURCE = """void unpack(void *ctx) {
    u32 pad[8];
    u32 limit;
    u32 i;
    u32 k;
    u16 *base;
    u16 *cursor;
    u16 word;
    s32 scaled;
    s32 stride;
    void *entry;
    void *item;
    u32 extent;
    u8 *data;
    u8 *walk;
    u32 area;
    u32 width;
    u32 height;
    base = load(ctx);
    limit = count >> 1;
    i = 0;
    if (limit != 0) {
        cursor = base;
        do {
            word = *cursor;
            i += 1;
            *cursor = scale(word) + 1;
            cursor += 1;
        } while (i < (count >> 1));
    }
    i = 0;
    if ((*((u32 *) (((char *) ctx) + 0x14))) != 0) {
        do {
            *((s32 *) (((char *) ((i * 8) + (*((u8 **) (((char *) ctx) + 0x10))))) + 4)) = 1;
            scaled = i * 8; entry = scaled + (*((u8 **) (((char *) ctx) + 0x10)));
            *((void **) (((char *) entry) + 4)) = (void *) ((*((u32 *) (((char *) entry) + 4))) + 2);
            i += 1;
        } while (i < ((u32) (*((u32 *) (((char *) ctx) + 0x14)))));
    }
    k = 0;
    if ((*((u32 *) (((char *) ctx) + 0x1C))) != 0) {
        stride = 0;
        do {
            item = stride + (*((u8 **) (((char *) ctx) + 0x18)));
            *((s32 *) (((char *) item) + 8)) = 3;
            k += 1;
            stride += 0xC;
        } while (k < ((u32) (*((u32 *) (((char *) ctx) + 0x1C)))));
    }
    k = 0;
    if ((*((u32 *) (((char *) ctx) + 0x1C))) != 0) {
        stride = 0;
        do {
            item = stride + (*((u8 **) (((char *) ctx) + 0x18)));
            data = *((u8 **) (((char *) item) + 8));
            area = ((u32) (width * height)) >> 1;
            if (!((*((u8 *) (((char *) item) + 0))) & 1)) {
                if (((int) (((u32) (width * height)) >> 1)) != 0) {
                    walk = data;
                    do {
                        *walk = (*walk >> 4) | ((*walk & 0xF) * 0x10);
                        ++walk;
                    } while (((u32) walk) < ((u32) ((((u32) (width * height)) >> 1) + data)));
                }
            }
            k += 1;
            stride += 0xC;
        } while (k < ((u32) (*((u32 *) (((char *) ctx) + 0x1C)))));
    }
    done();
}
"""




def _chain(text):
    return ('do {' not in text.split('area =')[0] and 'u32 pad[8];' not in text and 'cursor' not in text
            and 'base[i]' in text and '(u32) area + (u32) data' in text and 'area >>= 1;' in text)


def test_the_families_are_known_to_the_agents():
    assert set(counted_loop_shapes.FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)


def test_drafts_and_the_beam_propose_the_chain():
    labels = {}
    out = draft_repairs.variants(SOURCE, 'unpack', 'mips', labels=labels, limit=None)
    assert any(labels.get(v) == counted_loop_shapes.CHAIN and _chain(v) for v in out)
    assert any(labels.get(v) == counted_loop_shapes.INDEX for v in out)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from decompnet.app import deep_permuter
    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'unpack', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert any(family == counted_loop_shapes.CHAIN and _chain(text) for family, text in proposals)
