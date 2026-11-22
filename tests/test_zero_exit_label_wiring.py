"""decomprepair 0.5.584's `zero_exit_label` reaches drafts, the agents and the Deep Permuter's beam.

A KMC GCC 2.7.2 line-segment test (N64, 205 instructions) went 20 -> 0 from its saved source through the family's chain
child in the isolated judge (judge-worker, no permuter run): the final guarded return as one labelled zero return, the
later returns of zero-only variables jumping to it, and both guard-then-return forms inverted. Each pair of the three
stayed at 520. This test uses a synthetic function, not the game's code."""
from decompnet.app import draft_repairs
from decomprepair import zero_exit_label_repairs

SOURCE = '''s32 seg(void *p, void *q)
{
  f32 d;
  s32 bit;
  s32 low;
  s32 high;
  s32 fail;
  bit = 0;
  d = *(f32 *) p - *(f32 *) q;
  if (d > 0.0f)
  {
    if (!(d < 1.0f))
    {
      if (*(f32 *) q < d)
      {
        return 0;
      }
      goto inner;
    }
    return bit;
  }
  fail = 0;
  if ((!(d < 2.0f)) && (!(d > 3.0f)))
  {
    inner:
    low = d > 4.0f;
    high = bit;
    if ((low | high) != 0)
    {
      return 0;
    }
    if (!(low | high))
      goto done;
    return bit;
    done:;
    return 1;
  }
  return fail;
}
'''


def test_the_family_is_known_to_the_agents():
    assert zero_exit_label_repairs.FAMILY in draft_repairs.AGENT_FAMILIES


def test_drafts_and_the_beam_propose_the_chain():
    labels = {}
    out = draft_repairs.variants(SOURCE, 'seg', 'mips', labels=labels, limit=None)
    chain = [v for v in out if labels.get(v) == zero_exit_label_repairs.FAMILY
             and 'return fail;' not in v and 'goto inner;\n    goto __zero_exit;' in v]
    assert chain and '__zero_exit:\n    return 0;' in chain[0]


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from decompnet.app import deep_permuter
    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'seg', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    offered = [text for family, text in proposals if family == zero_exit_label_repairs.FAMILY]
    assert any('return fail;' not in text and 'goto inner;\n    goto __zero_exit;' in text for text in offered)
