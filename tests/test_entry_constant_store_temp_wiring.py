"""decomprepair 0.5.631: entry_constant_local (a loop's compared byte constant as a local set among the entry
assignments) and store_temp_inline (a one-line temp written into the store that reads it). Reached from the drafts and
the Deep Permuter's local lane."""
from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
s32 walk(u8 *table, s32 count)
{
    s32 flag;
    s32 index;
    s32 offset;
    s32 total;
    void *t;
    flag = 1;
    index = 0;
    offset = 0;
    total = 0;
    do {
        if (table[offset] != 0xFF) {
            total += 1;
        }
        if (table[offset + 1] != 0xFF) {
            flag = 0;
        }
        t = (index * 8) + (*(u8 **)((char *)(table) + 4));
        (*(s32 *)((char *)(((offset * 4) + table)) + 0xF80)) = (s32) (((*(u16 *)((char *)(t) + 0)) << 0x10) | (*(u8 *)((char *)(t) + 2)));
        offset += 16;
        index += 1;
    } while (index < count);
    return flag + total;
}
"""


def test_the_drafts_offer_both_families():
    labels = {}
    draft_repairs.variants(SOURCE, 'walk', 'mips', labels=labels, limit=None)
    assert {'entry_constant_local', 'store_temp_inline'} <= set(labels.values())
    assert {'entry_constant_local', 'store_temp_inline'} <= set(draft_repairs.AGENT_FAMILIES)
    constant = next(text for text, family in labels.items() if family == 'entry_constant_local')
    assert '!= const_0xff' in constant
    inline = next(text for text, family in labels.items() if family == 'store_temp_inline')
    assert 't = (index * 8)' not in inline


import pytest


@pytest.mark.parametrize('module, family', [('entry_constant_local', 'entry_constant_local'), ('store_temp_inline', 'store_temp_inline')])
def test_each_runs_in_the_bounded_local_lane(monkeypatch, module, family):
    import importlib

    from tests.test_permute import AGENT

    net = setup(monkeypatch, declared=('entry_constant_local', 'store_temp_inline'))
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + f'\n/* {family} */'
    for other in ('entry_constant_local', 'store_temp_inline'):
        monkeypatch.setattr(importlib.import_module('decomprepair.' + other), 'variants',
                            (lambda *args, **kwargs: [(family, child)]) if other == module else (lambda *args, **kwargs: []))
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert tried == [child]
