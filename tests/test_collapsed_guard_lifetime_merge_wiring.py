"""decomprepair collapsed_guard_lifetime_merge (an identical-arm guard on an unassigned local collapsed and two integer
locals merged, as one child: a KMC audio function 60 -> 0): reached from the drafts and the Deep Permuter's local lane."""
import importlib

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
s32 note(s32 value);

s32 clamp(s16 *p, u32 word)
{
  s32 flag;
  s32 signed_v;
  s16 level;
  level = p[0] + (word >> 4);
  p[0] = level;
  signed_v = p[0];
  if (signed_v >= 0)
  {
    level = signed_v;
  }
  else
  {
    level = 0;
  }
  p[1] = level;
  if (word != 0)
  {
    flag = 1;
  }
  else
    if (flag)
  {
    flag = 0;
  }
  else
  {
    flag = 0;
  }
  if (flag & 0xFF)
  {
    note(word);
  }
  return level;
}
"""


def test_the_drafts_offer_the_family():
    labels = {}
    draft_repairs.variants(SOURCE, 'clamp', 'mips', labels=labels, limit=None)
    assert 'collapsed_guard_lifetime_merge' in set(labels.values())
    assert 'collapsed_guard_lifetime_merge' in draft_repairs.AGENT_FAMILIES
    assert any('if (flag)' not in text and 'flag' not in text.split('{', 1)[1]
               for text, family in labels.items() if family == 'collapsed_guard_lifetime_merge')


def test_it_runs_in_the_bounded_local_lane(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch, declared=('collapsed_guard_lifetime_merge',))
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* collapsed_guard_lifetime_merge */'
    monkeypatch.setattr(importlib.import_module('decomprepair.collapsed_guard_lifetime_merge'), 'variants',
                        lambda *args, **kwargs: [('collapsed_guard_lifetime_merge', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert child in tried
