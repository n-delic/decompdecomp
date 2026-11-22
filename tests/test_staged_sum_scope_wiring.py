"""decomprepair 0.5.626: staged_sum_scope (`guarded_body_scope`: a guarded copy block in a one-shot scope;
`staged_sum_scope`: a quotient, m2c's split sum and its range test in one scope) reaches the draft repairs and the deep
pass's local lane.

A 217-instruction N64 voice allocator (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) sat at 85 with 17 register rows:
from its saved best the guarded wrapper scores 25 and the staged scope (with the earlier load-and-add wrapped) 0 in the
isolated judge, exact in the app (docs/learnings/function/voice-allocation-staged-sum-scope.md); no permuter run."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned int u32;\ntypedef unsigned char u8;\ntypedef signed char s8;\ntypedef short s16;\n'
SOURCE = HEADER + '''s32 voice(u32 arg0, s32 n, s32 k)
{
  u8 vol;
  s32 sum;
  s32 base;
  s32 clamped;
  s32 cur;
  s32 next;
  void *p;
  p = table;
  base = n + (*((s8 *) (((char *) p) + 4)));
  vol = (k * (*((u8 *) (((char *) p) + 5))))/127;
  sum = (*((s16 *) (((char *) p) + 6))); sum = ((arg0 >> 8) & 0xFF) + sum;
  if (sum < 0x100)
  {
    clamped = sum;
  }
  else
  {
    clamped = 0xFF;
  }
  next = play(clamped, vol, base);
  if (next != -1)
  {
    *((s32 *) (gTable + ((cur & 0xFF) * 0x1A0))) = next;
    cur = next;
  }
  return cur;
}
'''
FAMILIES = ('guarded_body_scope', 'staged_sum_scope')


def test_the_families_are_known_to_the_agents():
    assert set(FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)


def test_the_families_are_offered_through_the_draft_repairs():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'voice', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    found = {labels.get(t) for t in texts}
    assert set(FAMILIES) <= found
    staged = [' '.join(t.split()) for t in texts if labels.get(t) == 'staged_sum_scope']
    assert any('sum += (*((s16 *) (((char *) p) + 6))); in_range = sum < 0x100; } while (0); if (in_range)' in t
               for t in staged)


def test_the_families_reach_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'voice', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert set(FAMILIES) <= {family for family, _ in proposals}
