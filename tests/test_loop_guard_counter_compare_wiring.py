"""decomprepair loop_guard_counter_compare (a loop's entry guard as the counter's compare, with each local merge as one
child: a 401-instruction KMC loader, frame 8 bytes short, 22 -> 0): reached from the drafts and the Deep Permuter's local lane."""
import importlib

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
extern void g(s32);
extern s32 h(s32);
void f(s32 n)
{
  u32 count;
  u32 i;
  u32 biggest;
  u32 later;
  count = n;
  i = 0;
  biggest = 0;
  if (count != 0)
  {
    do
    {
      if (biggest < h(i))
      {
        biggest = h(i);
      }
      i += 1;
    }
    while (i < count);
  }
  g(biggest);
  later = h(2);
  g(later);
  g(later + 1);
}
"""


def test_the_drafts_offer_the_guard_and_the_combined_child():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    offered = [text for text, family in labels.items() if family == 'loop_guard_counter_compare']
    assert 'loop_guard_counter_compare' in draft_repairs.AGENT_FAMILIES
    assert any('if (i < count)' in text and 'later' in text for text in offered)
    # the combined child: the guard with the second role merged into the first
    assert any('if (i < count)' in text and 'biggest = h(2)' in text for text in offered)


def test_it_runs_in_the_bounded_local_lane(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* loop_guard_counter_compare */'
    monkeypatch.setattr(importlib.import_module('decomprepair.loop_guard_counter_compare'), 'variants',
                        lambda *args: [('loop_guard_counter_compare', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert child in tried
