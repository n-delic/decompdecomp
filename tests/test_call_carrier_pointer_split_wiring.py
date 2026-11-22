"""decomprepair call_carrier_pointer_split (stray trailing pointer arguments dropped and a reused pointer local split, as
one child: a KMC menu function 60 -> 0): reached from the drafts and the Deep Permuter's local lane."""
import importlib

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
s32 show();
s32 flush();

void f(void *obj, s32 mode) {
    void *temp;
    void *other;
    temp = *(void **)obj;
    *(s32 *)((char *)temp + 0x10) = 1;
    temp = *(void **)obj;
    *(s32 *)((char *)temp + 0x28) = mode;
    show(3, temp);
    other = *(void **)((char *)obj + 4);
    flush(3, other);
}
"""


def test_the_drafts_offer_the_family():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert 'call_carrier_pointer_split' in set(labels.values())
    assert 'call_carrier_pointer_split' in draft_repairs.AGENT_FAMILIES
    assert any('show(3);' in text and 'temp_b1 = *(void **)obj;' in text
               for text, family in labels.items() if family == 'call_carrier_pointer_split')


def test_it_runs_in_the_bounded_local_lane(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* call_carrier_pointer_split */'
    monkeypatch.setattr(importlib.import_module('decomprepair.call_carrier_pointer_split'), 'variants',
                        lambda *args: [('call_carrier_pointer_split', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert child in tried
