"""decomprepair 0.5.587's table-search families reach the draft repairs and the deep pass's local lane.

A 126-instruction N64 text measurer (KMC GCC 2.7.2, saved best 105) matched after three edits no family offered: m2c's
`for (;;)` search through a per-character kerning table (a pair pointer read through `volatile`, identical-arm `if`s,
dead copies, a local table pointer and a widened copy of the byte) as the plain counted `for` that re-reads the table
field (105 -> 53; each piece alone 1278-1735), the leftover four-word `volatile` scratch shortened (53 -> 30), and the
value and the totals it feeds wrapped in one one-shot scope (30 -> 0). Run from the saved best with the judge-worker
(no permuter run) the three children chained to 0. The shape below is the function's, with other names."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned int u32;\ntypedef unsigned char u8;\ntypedef signed char s8;\n'
SOURCE = HEADER + '''typedef struct Rec { u8 *pairs; u8 count; } Rec;
typedef struct Obj { u8 pad0[8]; u8 *glyphs; u8 *table; s8 extra; } Obj;
u32 measure(u8 *text, s32 n, Obj *obj)
{
  u32 width;
  u32 total;
  u32 key;
  s32 idx;
  u32 k;
  u8 *p;
  u8 ch;
  s32 off;
  u8 *table;
  Rec *rec;
  s32 adv;
  volatile u32 pad[4];
  width = 0;
  total = 0;
  ch = *text;
  key = ch;
  idx = ch - 0x21;
  table = obj->table;
  if (table != 0)
  {
    if (n || idx)
    {
      off = idx * 8;
    }
    else
    {
      off = idx * 8;
    }
    if (((Rec *) (table - (-(2 * (idx * 4)))))->count != 0)
    {
      k = 0;
      for (;;)
      {
        if (ch)
        {
          rec = (Rec *) ((idx * 8) + table);
          p = (u8 *) ((*((u8 * volatile *) (&rec->pairs))) + (k * 2));
          k++;
        }
        else
        {
          rec = (Rec *) ((idx * 8) + table);
          p = (u8 *) ((*((u8 * volatile *) (&rec->pairs))) + (k * 2));
          k++;
        }
        if (p[0] == key)
        {
          width -= (s8) p[1];
        }
        else
          if (k < rec->count)
        {
          continue;
        }
        break;
      }
    }
  }
  adv = obj->glyphs[(idx * 5) + 2] + obj->extra; width += adv; total += adv;
  return width + total;
}
'''
FAMILIES = {'record_search_for_loop', 'unused_frame_padding_shrink', 'accumulation_run_scope'}


def _offered(source):
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + source, 'measure', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    return texts, labels


def test_the_families_are_known_to_the_agents():
    assert FAMILIES <= set(draft_repairs.AGENT_FAMILIES)


def test_the_chain_is_offered_through_the_draft_repairs_one_step_at_a_time():
    texts, labels = _offered(SOURCE)
    child = next(t for t in texts if labels.get(t) == 'record_search_for_loop' and 'if (obj->table != 0)' in t and '== ch)' in t)
    assert 'for (;;)' not in child and 'volatile *' not in child
    texts, labels = _offered(child.replace(seeds.M2C_MACROS, '', 1))
    shrunk = [t for t in texts if labels.get(t) == 'unused_frame_padding_shrink']
    assert shrunk and any('pad[1]' in t for t in shrunk)
    texts, labels = _offered(next(t for t in shrunk if 'pad[1]' in t).replace(seeds.M2C_MACROS, '', 1))
    wrapped = [t for t in texts if labels.get(t) == 'accumulation_run_scope']
    assert any('do { adv = obj->glyphs[(idx * 5) + 2] + obj->extra; width += adv; total += adv; } while (0);' in t for t in wrapped)


def test_the_families_reach_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'measure', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert {'record_search_for_loop', 'unused_frame_padding_shrink'} <= {family for family, _ in proposals}
