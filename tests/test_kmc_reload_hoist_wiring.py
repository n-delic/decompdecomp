"""decomprepair 0.5.565's five loop and reload repairs reach the draft repair path.

A 180-instruction KMC N64 function (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`) stood at 1110 and went to 600 in
five steps, each of which also improved it on its own: the registration's one scaled index with a counter copy
(1041), the loop bound set twice (900), the inner loop reading a copy of the outer cursor (700) and a field's
pointer loaded into the local that already holds the first load (1090); the base address assigned at the entry makes
the draft's read-before-write valid C at the same score. The shapes below are the function's, with other names."""
from decompnet.app import draft_repairs

NAME = 'walk'

SOURCE = """\
typedef struct
{
  s8 flag;
  u8 pad[7];
} Slot;
extern Slot D_slots[];
extern u8 D_keys[];
extern u8 D_count;
extern void *D_list;
void walk(void *obj, void *list, s32 count, s32 flag)
{
  void **base;
  void **last;
  void *cursor;
  void *entry;
  void *held;
  void *item;
  s32 off;
  s32 i;
  s32 j;
  s32 one;
  s32 total;
  if (D_count < 8)
  {
    do
    {
      D_slots[D_count].flag = flag; off = D_count * 8;
      *((void **) (D_keys + off)) = list;
    }
    while (0);
    D_count += 1;
  }
  if (count != 0)
  {
    cursor = list;
    last = base - 1;
    base = &D_list;
    do
    {
      total = *((s32 *) (((char *) cursor) + 6));
      if (one > 0)
      {
        j = 0;
        do
        {
          item = *base;
          held = *((void **) (((char *) item) + 0x6C));
          *((s32 *) (((char *) held) + 0x2C)) = *((s32 *) (((char *) cursor) + 0));
          *((s32 *) (((char *) (*((void **) (((char *) last[j]) + 0x6C)))) + 0x24)) = total;
          j += 1;
        }
        while (j < one);
      }
      cursor += 8;
    }
    while ((((u32) cursor) - ((u32) list)) < ((2 * (2 * ((u32) count))) * 2));
  }
}
"""

FAMILIES = ('counter_append_scope', 'loop_limit_reassign', 'outer_cursor_entry_copy', 'loaded_pointer_reuse', 'entry_base_init')


def test_every_family_is_proposed_labelled_and_known_to_the_agents():
    labels = {}
    result = draft_repairs.variants(SOURCE, NAME, 'mips', labels=labels, limit=None)
    assert set(FAMILIES) <= set(labels.values())
    assert set(FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)
    by = {family: next(t for t in result if labels.get(t) == family) for family in FAMILIES}
    assert '__repair_append_count = D_count;' in by['counter_append_scope']
    assert 'while (__repair_loop_limit = ((2 * (2 * ((u32) count))) * 2) + (u32) list, (u32) cursor < __repair_loop_limit);' in by['loop_limit_reassign']
    assert '__repair_entry_cursor = cursor;' in by['outer_cursor_entry_copy']
    assert by['loaded_pointer_reuse'].count('held = *((void **)') == 2
    assert by['entry_base_init'].index('base = &D_list;') < by['entry_base_init'].index('if (D_count < 8)')
