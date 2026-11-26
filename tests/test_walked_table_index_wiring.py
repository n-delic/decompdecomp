"""decomprepair walked_table_index (a byte-cursor table walk, its shared offset local and a void tail written back as
the indexed loop, as one child: a KMC 108-instruction function 100 -> 0): reached from the drafts and the Deep
Permuter's local lane."""
import importlib

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
s32 notify();
s32 fallback();
extern s32 T_A;
extern char T_B[];
extern void *g_obj;

void walk(void) {
    char *var_s2;
    s32 temp_s0;
    s32 var_a2;
    u32 temp_a0;
    u32 var_s1;

    var_a2 = 0;
    if (g_obj != NULL) {
        var_s1 = 0;
        var_s2 = &T_A;
        do {
            temp_a0 = *(u32 *)((char *)g_obj + 0x30);
            temp_s0 = var_s1 * 4;
            if (((temp_a0 >> 4) == *(s32 *)var_s2) && ((temp_a0 >> 0x14) == *(s32 *)(T_B + temp_s0))) {
                notify(1);
                var_a2 = 1;
            }
            var_s2 += 4;
            var_s1 += 1;
        } while (var_s1 < 2U);
    }
    temp_s0 = var_a2;
    if ((u8)temp_s0 == 0) {
        fallback();
    }
}
"""


def test_the_drafts_offer_the_family():
    labels = {}
    draft_repairs.variants(SOURCE, 'walk', 'mips', labels=labels, limit=None)
    assert 'walked_table_index' in set(labels.values())
    assert 'walked_table_index' in draft_repairs.AGENT_FAMILIES
    assert any('&T_A + var_s1' in text and 'return temp_s0;' in text
               for text, family in labels.items() if family == 'walked_table_index')


def test_it_runs_in_the_bounded_local_lane(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch, declared=('walked_table_index_repairs',))
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* walked_table_index */'
    monkeypatch.setattr(importlib.import_module('decomprepair.walked_table_index_repairs'), 'variants',
                        lambda *args, **kwargs: [('walked_table_index', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert child in tried
