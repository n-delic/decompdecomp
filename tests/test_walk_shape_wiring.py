"""decomprepair 0.5.597's `walk_shape_repairs` reaches drafts, the agents and the Deep Permuter's beam and local lane.

A recency-list cache lookup (N64, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, 364 instructions) sat at 5451 with
eighty register rows. Reversed from its match (the log pointer, the capture, the shared sum and scratch, the folds, the
node handle, the store order, the parameter copies), the library's own children took it 2820 -> 870 (the chain as one
child) -> 595 -> 535 -> 30 -> 0 in the isolated judge (judge-worker, no permuter run). This test uses a synthetic list
walk, not the game's code."""
from decompnet.app import draft_repairs
from decomprepair import walk_shape_repairs

SOURCE = """void lookup(Out *out, u32 arg1, s32 arg2, s32 key)
{
  u32 rows[3][2];
  u32 hi;
  Node *node;
  Node *next;
  Ent *e;
  u32 a0;
  u32 a1;
  s32 count;
  s32 size;
  u32 start;
  u32 v;
  Node *free_head;

  start = arg1;
  size = arg2;
  hi = start + size;
  count = 0;
  node = list_head;
  while (node != NULL) {
    next = node->next;
    if (node->tag == key && node->addr < hi && start < node->addr) {
      a0 = node->addr;
      a1 = node->len;
      if (start + size >= a0 + 0x600)
        v = 0x600 - a1;
      else
        v = ((start + size - (a0 + a1)) + 0xFF) & ~0xFF;
      e = &log_table[log_count];
      e->base = a0 + a1;
      e->count = v;
      e->data = node->data;
      log_count++;
      if (start < a1) {
        a0 = size - (node->addr - start);
        rows[1][(u16) count] = a0;
        count++;
        size -= a0;
      } else if (a1 + node->len < start + size) {
        a0 = node->addr + node->len - start;
        rows[1][(u16) count] = a0;
        count++;
        size -= a0;
        start = node->addr + node->len;
      } else {
        rows[1][(u16) count] = size;
        size = 0;
        rows[2][(u16) count] = start;
        rows[0][(u16) count] = node->data + (start - a1);
        count++;
        break;
      }
    }
    node = next;
  }
  while (size != 0) {
    node = free_list;
    if (free_list != NULL) {
      free_list = node->prev;
      list_head = node;
    } else {
      node = list_tail;
      list_tail = node->prev;
    }
    size = size - 0x600 + v;
    start -= v - 0x600;
  }
  if ((count & 0xFFFF) == 1) {
    out->a = rows[0][0];
    out->d = 0;
    out->c = rows[1][0];
    return;
  }
  out->a = rows[0][1];
}
"""
NAME = 'lookup'


def test_the_families_are_known_to_the_agents():
    assert set(walk_shape_repairs.FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)


def test_drafts_and_the_beam_propose_the_chain():
    labels = {}
    out = draft_repairs.variants(SOURCE, NAME, 'mips', labels=labels, limit=None)
    chain = [v for v in out if labels.get(v) == walk_shape_repairs.FAMILY_CHAIN]
    assert chain
    assert 'log_table[log_count].base' in chain[0] and '(a0 = node->addr)' in chain[0] and 'node = t;' in chain[0]
    assert {walk_shape_repairs.FAMILY_MOVE, walk_shape_repairs.FAMILY_POINTER} <= set(labels.values())


def test_the_families_reach_the_deep_passes_local_lane():
    import pytest

    from decompnet.app import deep_permuter
    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': NAME, 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    labels = {family for family, _ in proposals}
    assert {walk_shape_repairs.FAMILY_CHAIN, walk_shape_repairs.FAMILY_MOVE} <= labels
