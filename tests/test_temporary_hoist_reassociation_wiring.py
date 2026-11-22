"""temporary_hoist_repairs and reassociation_repairs (decomprepair 0.5.632): a sub-expression held in a temporary and an
associative chain grouped another way, the two steps most often missing in a whole-path audit of matched functions.
Reached from the drafts (and so the beam) and the Deep Permuter's local lane."""
import pytest

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
u8 *func(u8 *s, u16 i, u32 d, u32 x)
{
  u8 *p;
  p = s + (i << 4) + 0x300;
  *p = (u8) (((x + (d + 0xFFFC)) & 0xFFFF) >> 8);
  return p;
}
"""


def test_the_drafts_offer_the_temporary_and_the_regrouping():
    labels = {}
    out = draft_repairs.variants(SOURCE, 'func', 'mips', labels=labels, limit=None)
    assert {'temporary_hoist', 'reassociate'} <= set(labels.values())
    assert {'temporary_hoist', 'temporary_hoist_block', 'temporary_hoist_pair', 'reassociate'} <= set(
        draft_repairs.AGENT_FAMILIES)
    held = [text for text, family in labels.items() if family == 'temporary_hoist']
    assert any('temp = d + 0xFFFC;' in text and '(x + temp)' in text for text in held)
    regrouped = [text for text, family in labels.items() if family == 'reassociate']
    assert any('p = s + ((i << 4) + 0x300);' in text for text in regrouped)
    assert all(text in out for text in held + regrouped)


@pytest.mark.parametrize('module_name, family', [('temporary_hoist_repairs', 'temporary_hoist'),
                                                  ('reassociation_repairs', 'reassociate')])
def test_each_runs_in_the_bounded_local_lane(monkeypatch, module_name, family):
    import importlib

    from tests.test_permute import AGENT

    module = importlib.import_module('decomprepair.' + module_name)
    for other in {'temporary_hoist_repairs', 'reassociation_repairs'} - {module_name}:
        monkeypatch.setattr(importlib.import_module('decomprepair.' + other), 'variants', lambda *args: [])
    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + f'\n/* {family} */'
    monkeypatch.setattr(module, 'variants', lambda *args: [(family, child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert tried == [child]
