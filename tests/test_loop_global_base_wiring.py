"""decomprepair's `loop_global_base` and the symbol-based `field_struct_view_printed` reach drafts and the beam.

A KMC GCC 2.7.2 map-table setup sat at 85 (register-only rows). The match was three edits that each did nothing or made
it worse alone: its first loop reading the global the entries were stored to through a loop-scoped local (85 -> 85),
one record field read as a struct member (455 alone, 50 with the first), and the header statements reordered (50 -> 0,
an existing family). The neutral plateau pass keeps the 85 -> 85 step and expands it, so the chain needs both library
families offered by `draft_repairs.variants`, which the drafts and the beam share."""
from decompnet.app import draft_repairs

SOURCE = '''typedef struct { s16 slot; u16 flags; } Entry;
extern Entry *G;
extern u16 D_800C02D8;
inline static Entry *as_entry(u8 *arg0)
{
  return (Entry *) arg0;
}

void f(u16 count, s32 offset, Entry *table)
{
  short i;
  Entry *entries;
  Entry *entry;
  u16 size;
  entries = table;
  size = *((u16 *) (((u8 *) (&D_800C02D8)) + offset)); G = entries;
  for (i = 0; ((u16) i) < count; ++i)
  {
    if (entries == NULL)
    {
      break;
    }
    entry = (Entry *) ((((u16) i) * 4) + ((u8 *) entries));
    entry->flags = size;
  }
}
'''


def test_the_loop_step_is_a_known_family_and_proposed():
    labels = {}
    result = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert 'loop_global_base' in draft_repairs.AGENT_FAMILIES
    staged = [text for text in result if labels.get(text) == 'loop_global_base']
    assert staged and all('Entry *base = G;' in text for text in staged)
    assert any('as_entry(' in text for text in staged)


def test_the_member_read_of_a_symbol_field_is_proposed_from_the_staged_state():
    labels = {}
    staged = next(text for text in draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
                  if labels.get(text) == 'loop_global_base')
    inner = {}
    result = draft_repairs.variants(staged, 'f', 'mips', labels=inner, limit=None)
    member = [text for text in result if inner.get(text) == 'field_struct_view_printed']
    assert member and any('((Field_u16 *) (((u8 *) (&D_800C02D8)) + offset))->v' in text for text in member)
