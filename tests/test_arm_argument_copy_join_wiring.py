"""decomprepair arm_argument_copy_join (a call's first argument copied on two paths of the switch before it, with the masked
store value in its own local, as one child: a KMC effect spawner 60 -> 0): reached from the drafts and the Deep Permuter's
local lane."""
import importlib

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
void *start(u16 kind, s32 tag, s32 extra, u16 tail);
void apply(void *obj, s32 word, u16 half, s32 tag);

void *make(u16 kind, s32 tag, s32 word, u16 half)
{
    s32 sel;
    void *rec;
    u16 flags;

    flags = kind;
    rec = start(1, 0, 0, 0);
    sel = flags & 0xF000;
    switch (sel) {
    case 0x1000:
        (*(s16 *)((char *)rec + 0x3C)) = 0;
        break;
    case 0x3000:
        (*(s16 *)((char *)rec + 0x3C)) = 1;
        break;
    case 0x2000:
        (*(s16 *)((char *)rec + 0x3C)) = 2;
        break;
    }
    (*(s16 *)((char *)(rec) + 0x3E)) = (s16) (flags & 0xFFF);
    apply(rec, word, half, tag);
    return rec;
}
"""


def test_the_drafts_offer_the_family():
    labels = {}
    draft_repairs.variants(SOURCE, 'make', 'mips', labels=labels, limit=None)
    assert 'arm_argument_copy_join' in set(labels.values())
    assert 'arm_argument_copy_join' in draft_repairs.AGENT_FAMILIES
    offered = [text for text, family in labels.items() if family == 'arm_argument_copy_join']
    assert offered and all('apply(arg_copy, word, half, tag);' in text and 'mask_value = flags & 0xFFF;' in text
                           for text in offered)
    assert any(text.count('arg_copy = rec;') == 2 and text.count('goto arg_join;') == 1 for text in offered)


def test_it_runs_in_the_bounded_local_lane(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* arm_argument_copy_join */'
    monkeypatch.setattr(importlib.import_module('decomprepair.arm_argument_copy_join'), 'variants',
                        lambda *args: [('arm_argument_copy_join', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert child in tried
