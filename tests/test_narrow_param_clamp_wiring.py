"""decomprepair 0.5.583's narrow_param_clamp_repairs reach the draft repairs and the deep pass's local lane.

A 231-instruction N64 key/velocity dispatch (KMC GCC 2.7.2, saved best 500) matched after four edits no family offered:
`((unsigned short) x) >> 16` (the constant 0) as `x >> 16`, the id local as `u16`, the key parameter (read only through
byte masks, and passed to a static helper that masks it) as `u8`, and the clamp's range test computed before its sign
test. From the saved best the chain child scored 0 in the isolated judge (judge-worker, no permuter run); alone the
steps scored 555, 1040/675, 240. The shape below is the function's, with other names and values."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned int u32;\ntypedef unsigned char u8;\ntypedef unsigned short u16;\n'
SOURCE = HEADER + '''extern s32 find(s32, s32);
extern s32 emit(u32, s32, s32, s32, s32);
extern s32 table[];
static inline s32 lookup(s32 note, u8 bank)
{
  if (bank != 255)
  {
    return find(note & 0x7f, bank);
  }
  return -1;
}

s32 play(u32 packed, s32 note, s32 volume, u8 bank, s32 trim)
{
  u32 id = ((unsigned short) packed) >> 16;
  s32 level;
  s32 mix;
  level = trim + 1;
  switch (id & 0xc000)
  {
    case 0:
      return lookup(note, bank);
    case 0x4000:
      mix = table[note & 0xff] - 0x40;
      mix += volume & 0xff;
      if (mix < 0)
      {
        volume = 0;
      }
      else
        if (mix < 0x80)
      {
        volume = mix;
      }
      else
      {
        volume = 0x7f;
      }
      return emit(id, note & 0xff, note & 0x80, volume, level);
  }
  return -1;
}
'''
FAMILIES = {'halfword_shift_cast', 'halfword_local_width', 'mask_parameter_narrow', 'clamp_range_flag',
            'narrow_clamp_chain'}


def test_the_families_are_known_to_the_agents():
    assert FAMILIES <= set(draft_repairs.AGENT_FAMILIES)


def test_the_chain_is_offered_through_the_draft_repairs_with_the_shape_of_the_others():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'play', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    offered = {labels[t] for t in texts if t in labels}
    # the halfword local only exists once the cast is gone: it is offered on its own for a source without the cast
    assert FAMILIES - {'halfword_local_width'} <= offered, sorted(FAMILIES - offered)
    plain = SOURCE.replace('((unsigned short) packed)', 'packed')
    more = {}
    draft_repairs.variants(seeds.M2C_MACROS + plain, 'play', 'mips', '', FLAGS, more, limit=None, header=HEADER)
    assert 'halfword_local_width' in more.values()
    assert all(isinstance(t, str) for t in texts)
    chain = [t for t in texts if labels.get(t) == 'narrow_clamp_chain']
    assert chain and all('u16 id = packed >> 16;' in t and 'lookup(u8 note,' in t and 'mixInRange = mix < 0x80;' in t
                         for t in chain)


def test_the_families_reach_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'play', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert FAMILIES - {'halfword_local_width'} <= {family for family, _ in proposals}
