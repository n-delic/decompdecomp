"""dead_local_constant_compare (decomprepair 0.5.628): a compare's literal routed through a local the next statement
reassigns lets the global allocator hold it (`$v1`) where the lone constant took `$v0`. Reached from the drafts and the
Deep Permuter's local lane."""
from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
extern s32 D_80107EE0;
extern s32 D_80107EE8;
u32 func(s32 arg0)
{
  s32 index = arg0 & 0xff;
  s32 scaled = (index * 5) << 3;
  u32 result;
  index = *((u8 *) (((s8 *) (&D_80107EE8)) + scaled));
  if (index == 4)
  {
    return 0;
  }
  result = *((u32 *) (((s8 *) (&D_80107EE0)) + scaled));
  return result >> 31;
}
"""


def test_the_drafts_offer_the_dead_local_compare():
    labels = {}
    draft_repairs.variants(SOURCE, 'func', 'mips', labels=labels, limit=None)
    assert 'dead_local_constant_compare' in set(labels.values())
    assert 'dead_local_constant_compare' in draft_repairs.AGENT_FAMILIES
    text = next(text for text, family in labels.items() if family == 'dead_local_constant_compare')
    assert 'result = 4;' in text and 'index == result' in text


def test_it_runs_in_the_bounded_local_lane(monkeypatch):
    from decomprepair import dead_local_constant_compare
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* literal through a dead local */'
    monkeypatch.setattr(dead_local_constant_compare, 'variants',
                        lambda *args: [('dead_local_constant_compare', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert tried == [child]
