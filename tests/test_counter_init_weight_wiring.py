"""decomprepair 0.5.353's three families reach the repairs the deep pass and autopilot try.

From a KMC 2.7.2 record-search and edge-linking function (`-G0 -mips3 -mgp32 -mfp32 -O2`), each scored with the
judge-worker on the function's own before-state: `loaded_search_fold` 155 to 135, and from the natural-loop source the
scratch drop plus a counter start weighted ahead of its guard to exact (no permuter run)."""
from decompnet.app import draft_repairs

FLAGS = ('-G0', '-mips3', '-mgp32', '-mfp32', '-O2')

SEARCH = '''s32 f(s32 map)
{
    s32 offset = 0;
    s32 key = (s16) map;
    s32 sentinel = -1;
    s16 *entry = (s16 *) table;
    s32 id;
    map = 0;
    id = *entry;
    do
    {
      entry += 4;
      if (key == id)
      {
        offset = *((s32 *) (0x8011EB64 + map));
        goto found;
      }
      id = *entry;
      map += 8;
    }
    while (id != sentinel);
    found:
    return offset;
}
'''

PADDED = '''void f(s16 n, SearchEdge *edges, SearchRow *rows)
{
  u8 buffer[16];
  volatile unsigned short pad;
  s32 j;
  {
    s32 k;
    if (n > 0)
    {
      SearchRow *table = rows;
      k = 0;
      do
      {
        s16 a = edges[k].a;
        for (j = 0; j < 16; j++)
        {
          if (table[a].links[j] == 0)
          {
            break;
          }
        }
        k++;
      }
      while (k < n);
    }
  }
}
'''


def _variants(source):
    return draft_repairs.variants(source, 'f', 'mips', '', FLAGS, limit=400)


def test_the_families_are_wired_into_the_repairs():
    for family in ('loaded_search_fold', 'unused_frame_padding_drop', 'loop_weight_scope'):
        assert family in draft_repairs.AGENT_FAMILIES


def test_each_family_reaches_the_variants_the_deep_pass_scores():
    assert any('if (*entry == key)' in v and 'while (*entry != sentinel);' in v for v in _variants(SEARCH))
    assert any('volatile' not in v and 'buffer[16]' in v for v in _variants(PADDED))
    ahead = [v for v in _variants(PADDED) if 'do {do { k = 0; } while (0);} while (0);' in v]
    assert any(v.index('k = 0;') < v.index('if (n > 0)') for v in ahead)
