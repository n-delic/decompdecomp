"""volatile_read_inline reaches the draft repairs (and so the Deep Permuter's beam, which runs them unlimited)."""
from decompnet.app import draft_repairs

SOURCE = """\
void *spawn(s32, s32, s32);
s32 setFrame(void *, u16, s32);
extern s16 D_800C2942;

void *f(s32 arg0)
{
    u16 frame;
    s32 animation;
    void *obj;
    void *state;

    obj = spawn(0x30, arg0, 0);
    if (obj != NULL) {
        state = (*(void **)((char *)(obj) + 0x6C));
        (*(u16 *)((char *)(state) + 0x2C)) = 0U;
        frame = *(volatile u16 *)((char *)state + 0x2C);
        (*(s8 *)((char *)(state) + 0x28)) = 0;
        animation = D_800C2942;
        *(f32 *)((char *)state + 0x8) = 1.0f;
        setFrame(obj, frame, animation);
    }
    return obj;
}
"""


def test_the_family_is_proposed_and_known_to_agents():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    plain = [text for text, family in labels.items() if family == 'volatile_read_inline']
    assert any('setFrame(obj, *(u16 *)((char *)(state) + 0x2C), animation);' in text for text in plain)
    assert 'volatile_read_inline' in draft_repairs.AGENT_FAMILIES
