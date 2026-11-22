"""The comparison-splitting repair reaches a saved source with a masked key helper."""
from decompnet.app import draft_repairs

SOURCE = '''extern char table[];
extern u8 flat[];
inline static int equal_byte(s32 key, u8 value) { return (key & 0xFF) == value; }
s32 f(u8 *obj) {
  s32 id = *((s32 *) (obj + 0x60));
  s32 copy;
  u8 a; u8 b; u8 value;
  if (id != -1) {
    a = obj[0x4A];
    if (a != 0xFF) {
      b = obj[0x4B]; copy = id;
      if (b == 0xFF) { value = flat[copy & 0xFF]; }
      else { char *base = table; value = *((base + (b * 16)) + a); }
      if (equal_byte(copy, value)) { return 1; }
    }
  }
  return 0;
}
'''


def test_masked_copy_is_offered_as_one_repair():
    labels = {}
    proposals = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert any(labels.get(text) == 'compare_into_arms' and 'u8 copy;' in text
               and text.count('if (!equal_byte(copy, value))') == 2 for text in proposals)


def test_masked_copy_offers_reversed_second_arm_comparison():
    labels = {}
    proposals = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert any(labels.get(text) == 'compare_into_arms'
               and '!equal_byte(copy, value)' in text and '!equal_byte(value, copy)' in text
               for text in proposals)
