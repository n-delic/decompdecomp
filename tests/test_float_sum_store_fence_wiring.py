"""decomprepair float_sum_store_fence (a float sum's swapped first add and the one-shot scope in the store run between
its two adds, a KMC bounding-sphere function 25 -> 0): reached from the drafts and the Deep Permuter's local lane."""
import importlib

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
void f(f32 *out, f32 *box, f32 a, f32 b, f32 c) {
    f32 x2, y2, z2, length2;
    x2 = a * a;
    y2 = b * b;
    z2 = c * c;
    length2 = y2 + x2;
    box[1] = a;
    box[3] = b;
    box[5] = c;
    length2 += z2;
    box[0] = a;
    box[2] = b;
    out[3] = length2 * 0.5f;
}
"""


def test_the_drafts_offer_the_family():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert 'float_sum_store_fence' in set(labels.values())
    assert 'float_sum_store_fence' in draft_repairs.AGENT_FAMILIES
    assert any('length2 = x2 + y2;' in text and 'do { } while (0);' in text
               for text, family in labels.items() if family == 'float_sum_store_fence')


def test_it_runs_in_the_bounded_local_lane(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* float_sum_store_fence */'
    monkeypatch.setattr(importlib.import_module('decomprepair.float_sum_store_fence'), 'variants',
                        lambda *args, **kwargs: [('float_sum_store_fence', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert child in tried
