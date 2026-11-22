"""decomprepair 0.5.581: the families a 374-instruction N64 effect spawner's match needed reach the draft repairs and the
deep pass (KMC GCC 2.7.2, saved best 1397; judge-worker replay from the saved best, no permuter run: element_update_shape
1027, arm_label_share 788, phase_storage_split_narrow_share 22, the field-store swap of allocation_lever 2,
declaration_scope_sink 0)."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned char u8;\ntypedef signed char s8;\ntypedef unsigned short u16;\n'
SOURCE = HEADER + '''typedef struct { u8 v; } Field_u8;
typedef struct { s32 v; } Field_s32;
extern s32 table[][7];
extern s32 stock[];
static inline u8 column_of(void *rec)
{
  return ((Field_u8 *) (((char *) rec) + 0x34))->v;
}

void *spawn(s32 *arg0, void *rec, s32 n, s32 flip, u8 flag)
{
  u16 len;
  s32 x;
  s32 *slot;
  union
  {
    s32 *pointer;
    s32 value;
  } store;
  store.pointer = arg0;
  if (store.pointer == NULL)
  {
    store.pointer = stock;
  }
  fill(store.pointer);
  store.value = 0xA0;
  x = 0;
  if (((s8) flag) == 0)
  {
    x = 1;
  }
  if (((s8) flag) != 0)
  {
    x = 2;
  }
  if (n != 1)
  {
    x = 0x74;
    if (n == 2)
    {
      if (flip != 1)
      {
        ((Field_u8 *) (((char *) rec) + 0x35))->v = 0;
      }
      else
      {
        goto first;
      }
    }
    else
    {
      goto second;
    }
  }
  else
  {
    first:
    x = 0x3A;

    second:
    ((Field_u8 *) (((char *) rec) + 0x35))->v = 0;

  }
  if (rec != NULL)
  {
    len = measure(rec);
    ((Field_s32 *) (((char *) rec) + 0x3C))->v = 0;
    ((Field_s32 *) (((char *) rec) + 0x28))->v = 0;
    slot = &table[((Field_u8 *) (((char *) rec) + 0x33))->v][column_of(rec)];
    *slot += 1;
    use(rec, len, x, store.value);
  }
  return rec;
}
'''
FAMILIES = {'arm_label_share', 'element_pointer_compound', 'index_helper_unwrap', 'element_update_shape', 'narrow_test_share',
            'phase_storage_split', 'phase_storage_split_narrow_share', 'declaration_scope_sink', 'allocation_lever'}


def test_the_families_are_known_to_the_agents():
    assert (FAMILIES - {'allocation_lever'}) <= set(draft_repairs.AGENT_FAMILIES)


def test_the_families_are_offered_through_the_draft_repairs_with_the_shape_of_the_others():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'spawn', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    offered = {labels[t] for t in texts if t in labels}
    assert FAMILIES <= offered, sorted(FAMILIES - offered)
    assert all(isinstance(t, str) for t in texts)
    swapped = [t for t in texts if labels.get(t) == 'allocation_lever' and t.index('+ 0x28') < t.index('+ 0x3C')]
    assert swapped


def test_the_families_reach_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'spawn', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert FAMILIES <= {family for family, _ in proposals}
