"""decomprepair 0.5.599: the four steps of an 842-instruction N64 lit-actor drawer's chain (KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 2, isolated judge replay from the saved best, no permuter run: u8 globals 10770,
array sink 10577, word-cursor records in command order 7497, one call-result copy per arm removed 6502, a heavy command
value staged before the cursor read 5097, a quotient in a halfword with signed operands 4488) reach the draft repairs and
the deep pass's local lane."""
import pytest

from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned char u8;\ntypedef unsigned short u16;\ntypedef unsigned u32;\n'
CURSOR = HEADER + '''typedef struct { s32 value; } Field_0;
extern s32 * volatile sCursor;
extern unsigned char gR, gG, gB;
void cursor(u8 *arg0, u8 *table)
{
  s32 *t;
  t = sCursor;
  sCursor = t + 2;
  ((Field_0 *) t)->value = 0xFA000000;
  sCursor = t + 4;
  *((s32 *) (((char *) t) + 0xC)) = (gR << 24) | gG;
  *((s32 *) (((char *) t) + 4)) = (((arg0[0x7F] * table[0x64]) >> 8) << 24) | arg0[0x82];
  *((s32 *) (((char *) t) + 8)) = 0xFB000000;
}
'''
COPIES = HEADER + '''void copies(void *arg0, void *arg1, s32 mode)
{
  s32 var_s4;
  s32 temp_v0_2;
  s32 temp_v0_3;
  if (mode == 1)
  {
    temp_v0_2 = find(arg0, 1);
    var_s4 = temp_v0_2;
    use(arg0, var_s4, arg1);
  }
  else
  {
    if (mode == 2)
    {
      temp_v0_3 = find(arg0, 2);
      var_s4 = temp_v0_3;
      use(arg0, var_s4, arg1);
    }
  }
}
'''
QUOTIENT = HEADER + '''void quotient(u8 *arg0, s32 *out)
{
  s32 lum;
  s32 var_a1;
  int var_a2;
  unsigned int var_a3;
  var_a1 = arg0[1];
  var_a2 = arg0[2];
  var_a3 = arg0[3];
  lum = ((var_a3 + (var_a1 + var_a2)) / 3) & 0xFFFF;
  out[0] = lum * 5;
}
'''
EXPECTED = {'cursor': {'display_cursor_record_ordered', 'display_value_stage'}, 'copies': {'shared_copy_local_direct'},
            'quotient': {'narrow_quotient_local'}}
SOURCES = {'cursor': CURSOR, 'copies': COPIES, 'quotient': QUOTIENT}


def test_the_families_are_known_to_the_agents():
    assert set().union(*EXPECTED.values()) <= set(draft_repairs.AGENT_FAMILIES)


@pytest.mark.parametrize('name', sorted(EXPECTED))
def test_the_families_are_offered_through_the_draft_repairs_with_the_shape_of_the_others(name):
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCES[name], name, 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    assert EXPECTED[name] <= {labels[t] for t in texts if t in labels}
    assert all(isinstance(t, str) for t in texts)


@pytest.mark.parametrize('name', sorted(EXPECTED))
def test_the_families_reach_the_deep_passes_local_lane(name):
    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': name, 'asm': '', 'best_source': SOURCES[name]}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCES[name], proj, fn, [])
    finally:
        monkeypatch.undo()
    assert EXPECTED[name] <= {family for family, _ in proposals}
