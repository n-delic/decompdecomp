"""decomprepair phase_donor (a later phase's clamp variable, record pointer and product written through the earlier
phase's dead locals, a 181-instruction N64 function 40 -> 0): reached from the drafts and the Deep Permuter's local lane."""
import importlib

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
void f(void *arg0, s32 arg2, s32 arg3, s32 limit)
{
  s32 var_a0;
  s32 var_v1;
  s32 var_a1;
  register union
  {
    void *pointer;
    s32 value;
  } phase;
  register void *second;
  void *base;
  phase.pointer = *((void **) (((char *) arg0) + 0x68));
  base = (void *) (0x14 + ((s32) phase.pointer));
  var_v1 = arg2 * 2;
  var_v1 = var_v1 + ((s32) base);
  var_a0 = (limit - *((s16 *) (((char *) var_v1) + 0x94))) * arg3;
  var_a0 >>= 7;
  if (var_a0 < (-0x40))
  {
    var_a0 = -0x40;
  }
  *((s16 *) (((char *) var_v1) + 0x94)) += var_a0;
  var_a1 = limit - *((s16 *) (((char *) base) + 0x98));
  phase.value = ((s32) (var_a1 * arg3)) >> 7;
  if (phase.value < (-0x40))
  {
    phase.value = -0x40;
  }
  second = (void *) ((arg2 * 2) + base);
  *((s16 *) (((char *) second) + 0x98)) += phase.value;
}
"""


def test_the_drafts_offer_the_family():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert 'phase_donor' in set(labels.values())
    assert 'phase_donor' in draft_repairs.AGENT_FAMILIES
    assert any('var_a0 = var_a1 * arg3; phase_value = var_a0 >> 7;' in text and 'var_v1 = (s32) ((arg2 * 2) + base);' in text
               for text, family in labels.items() if family == 'phase_donor')


def test_it_runs_in_the_bounded_local_lane(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* phase_donor */'
    monkeypatch.setattr(importlib.import_module('decomprepair.phase_donor_repairs'), 'variants',
                        lambda *args: [('phase_donor', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert child in tried
