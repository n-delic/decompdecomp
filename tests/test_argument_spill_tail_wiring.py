"""decomprepair 0.5.592's `argument_spill_tail` reaches drafts, the agents and the Deep Permuter's beam and local lane.

A KMC GCC 2.7.2 route function (N64, 359 instructions, every saved register in use so two parameters spill) went
985 -> 0 from its saved source through the library's chain child in the isolated judge (judge-worker, no permuter run):
the parameters' volatile slots dropped, the goto tails of a call copied into the arms, a call sum as one expression and
a one-shot copy forwarded. Alone: 965, 365, 720; the first three-step subsets 265 and 620; the first two plus the copy
80. This test uses a synthetic function, not the game's code."""
from decompnet.app import draft_repairs
from decomprepair import argument_spill_tail

SOURCE = '''s32 scan();
f32 near();
void use();
s32 check();
void note();
s32 pick(void *a, f32 *b, s32 c, s32 d)
{
  volatile s32 padFC;
  volatile s32 spFC;
  s32 sp104;
  f32 t;
  f32 u;
  s32 var_a1;
  s32 hit;
  spFC = c;
  sp104 = d;
  hit = scan(a, b);
  if (hit == 1)
  {
    do { t = near(a, b); var_a1 = spFC; } while (0);
    *(f32 *) a = t;
    use(a, var_a1);
    return 1;
  }
  if (hit == 2)
  {
    u = near(a, b);
    u = u + near(b, a);
    var_a1 = spFC;
    *(f32 *) a = u;
    goto block_25;
  }
  if (d != 0)
  {
    if (check(a) != 0)
    {
      note(a, 333);
      goto block_24;
    }
    goto block_26;
  }
  return 0;
  block_24:
  var_a1 = sp104;
  block_25:
  use(a, var_a1);
  block_26:
  return 1;
}
'''


def _is_chain(text):
    return ('volatile' not in text and 'goto' not in text and 'do {' not in text
            and 'u = near(a, b) + near(b, a);' in text and 'use(a, c);\n    return 1;' in text)


def test_the_families_are_known_to_the_agents():
    for family in argument_spill_tail.FAMILIES:
        assert family in draft_repairs.AGENT_FAMILIES


def test_drafts_and_the_beam_propose_the_chain():
    labels = {}
    out = draft_repairs.variants(SOURCE, 'pick', 'mips', labels=labels, limit=None)
    chain = [v for v in out if labels.get(v) == argument_spill_tail.FAMILY and _is_chain(v)]
    assert chain
    assert any(labels.get(v) == argument_spill_tail.SLOT_FAMILY for v in out)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from decompnet.app import deep_permuter
    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'pick', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    offered = [text for family, text in proposals if family == argument_spill_tail.FAMILY]
    assert any(_is_chain(text) for text in offered)
