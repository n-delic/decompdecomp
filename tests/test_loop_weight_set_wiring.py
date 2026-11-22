"""decomprepair 0.5.585's `loop_weight_set` reaches drafts, the agents and the Deep Permuter's local lane.

An 84-instruction KMC GCC 2.7.2 N64 initialiser (two goto loops over one record, saved registers in the wrong order)
went 30 -> 0 from its saved source through the family's first call in the isolated judge (judge-worker, no permuter
run): the union split, the reused offset split, every start and step of the second loop wrapped once and the induction
pointer's step three deep. loop_weight_scope offers one statement per child, and each of those stayed on the plateau.
This test uses a synthetic function, not the game's code."""
from decompnet.app import draft_repairs
from decomprepair import loop_weight_set_repairs

SOURCE = '''void init_tables(void *arg0)
{
  union
  {
    u8 *pointer;
    s32 value;
  } phase;
  s32 first_offset;
  u32 counter;
  u32 arg;
  void *record;
  void *cursor;
  void *walker;
  counter = 0;
  record = *((void **) (((char *) arg0) + 0x6C));
  phase.pointer = &flag;
  first_offset = 0;
  walker = record;
  first_loop:
  {
    if ((*phase.pointer) != 0)
    {
      *((u16 *) (((char *) walker) + 0x1BC)) = *((u16 *) (((u8 *) table_a) + first_offset));
    }
    walker += 2;
    do
    {
      counter += 1;
    }
    while (0);
    first_offset += 4;
  }
  if (counter < 0x20U)
  {
    goto first_loop;
  }
  counter = 0;
  phase.value = 0;
  first_offset = 0;
  cursor = record;
  second_loop:
  {
    *((u8 *) (((char *) cursor) + 0x1FC)) = (u8) table_b[phase.value];
    phase.value += 0xE8;
    first_offset += 8;
    cursor += 8;
    arg = counter;
    counter += 1;
    call(arg);
  }
  if (counter < 0x3BU)
  {
    goto second_loop;
  }
}
'''


def _chain(text):
    return ('do { do { do { cursor += 8; } while (0); } while (0); } while (0);' in text
            and 'do { phase_value += 0xE8; } while (0);' in text and 'do { arg = counter; counter += 1; } while (0);' in text)


def test_the_family_is_known_to_the_agents():
    assert loop_weight_set_repairs.FAMILY in draft_repairs.AGENT_FAMILIES


def test_drafts_and_the_beam_propose_the_set():
    labels = {}
    out = draft_repairs.variants(SOURCE, 'init_tables', 'mips', labels=labels, limit=None)
    assert any(labels.get(v) == loop_weight_set_repairs.FAMILY and _chain(v) for v in out)
    assert all(isinstance(v, str) for v in out)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from decompnet.app import deep_permuter
    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'init_tables', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert any(family == loop_weight_set_repairs.FAMILY and _chain(text) for family, text in proposals)
