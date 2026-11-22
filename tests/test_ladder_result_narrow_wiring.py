"""decomprepair ladder_result_narrow (a bit-test ladder's result as the else-if chain, in a u8, in a function returning
s32: an 89-instruction KMC audio-setup function 90 -> 0): reached from the drafts and the Deep Permuter's local lane."""
import importlib

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
s32 start(s32 *, s32, s32, s32);
unsigned long long f(s32 *arg0, u16 arg1, u16 arg2, u32 arg3)
{
  s32 var_v0;
  start(arg0, 0, 0, 0);
  var_v0 = 2;
  if (!(arg3 & 0x10000))
  {
    var_v0 = 3;
    if (!(arg3 & 0x20000))
    {
      var_v0 = 0;
 do { if (!(arg3 & 0x40000)) { var_v0 = 1; } } while (0);
    }
  }
  start(arg0, arg1 & 0xFFFF, arg2 & 0xFFFF, var_v0);
  return 0;
}
"""


def test_the_drafts_offer_the_family_with_the_three_edits_as_one_child():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert 'ladder_result_narrow' in set(labels.values())
    assert 'ladder_result_narrow' in draft_repairs.AGENT_FAMILIES
    assert any('s32 f(' in text and 'u8 var_v0;' in text and 'else var_v0 = 1;' in text and 'while (0)' not in text
               for text, family in labels.items() if family == 'ladder_result_narrow')


def test_it_runs_in_the_bounded_local_lane(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* ladder_result_narrow */'
    monkeypatch.setattr(importlib.import_module('decomprepair.ladder_result_narrow'), 'variants',
                        lambda *args: [('ladder_result_narrow', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert child in tried
