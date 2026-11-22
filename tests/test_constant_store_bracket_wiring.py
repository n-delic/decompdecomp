"""decomprepair 0.5.576's constant_store_bracket reaches the draft repair path and the Deep Permuter's beam.

A defaults reset in a 145-instruction N64 function (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`) stood at 210: one
literal stored to the same global twice with other stores between (both emitted, one extra `lui/sb` pair), and the
one-shot literals stored before the repeated one. Dropping the first duplicate and moving three one-shot stores
between the repeated literal's stores was exact; neither half helps alone (1350 and 1320). The shape below is the
function's, with other names and values. The beam runs `draft_repairs.variants` with no limit, so one call covers both."""
import re

from decompnet.app import draft_repairs

SOURCE = """\
extern u8 g_a, g_b, g_c, g_d, g_e, g_f, g_g, g_h, g_i, g_j, g_k, g_l, g_m;
void defaults(void)
{
  do {
    u8 hold = 0xFF;
    g_a = 0xA1; g_b = 0x61; g_c = 5; g_d = 0xEE; g_e = 0x41; g_f = 0x11; g_g = 0;
    g_h = hold; g_i = hold; g_j = 0; g_k = 0;
    g_c = 5; g_l = 0; g_m = 5;
  } while (0);
}
"""


def stores(text):
    return re.findall(r'(?<![\w])(\w+) = (\w+);', text.replace('u8 hold = 0xFF;', ''))


def test_the_chain_is_proposed_as_one_labelled_child_and_known_to_the_agents():
    labels = {}
    result = draft_repairs.variants(SOURCE, 'defaults', 'mips', labels=labels, limit=None)
    assert {'constant_store_bracket', 'duplicate_constant_store_drop'} <= set(draft_repairs.AGENT_FAMILIES)
    wanted = [('g_a', '0xA1'), ('g_b', '0x61'), ('g_g', '0'), ('g_h', 'hold'), ('g_i', 'hold'), ('g_j', '0'),
              ('g_k', '0'), ('g_c', '5'), ('g_d', '0xEE'), ('g_e', '0x41'), ('g_f', '0x11'), ('g_l', '0'), ('g_m', '5')]
    assert [text for text in result if labels.get(text) == 'constant_store_bracket' and stores(text) == wanted]
