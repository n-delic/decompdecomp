"""decomprepair row_major_table (a row-major table's pointer walks and its parallel byte table's word reads as
`D[row][column]`: a 374-instruction KMC function 645 -> 0): reached from the drafts and the Deep Permuter's local lane."""
import importlib

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
extern u8 gStart[];
extern s32 gCount;
void bump(void *);
void f(void *arg0)
{
  s32 *p;
  s32 *q;
  s32 rowsum;
  s32 col;
  s32 row;
  u8 *base;
  p = (&gCount) + (*((u8 *) (((char *) arg0) + 0x33))) * 7;
  p += *((u8 *) (((char *) arg0) + 0x34));
  *p += 1;
  col = *((u8 *) (((char *) arg0) + 0x34));
  row = (*((u8 *) (((char *) arg0) + 0x33)));
  rowsum = row * 7;
  q = &gCount;
  q += rowsum;
  q += col;
  base = gStart;
  if ((*q) == *(s32 *)(col * 4 + (rowsum * 4 + base)))
  {
    bump(arg0);
  }
}
"""


def test_the_drafts_offer_the_family_with_both_tables_as_one_child():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert 'row_major_table' in set(labels.values())
    assert 'row_major_table' in draft_repairs.AGENT_FAMILIES
    assert any('extern s32 gCount[][7];' in text and 'extern s32 gStart[][7];' in text
               and 'if (gCount[row][col] == gStart[row][col])' in text
               for text, family in labels.items() if family == 'row_major_table')


def test_it_runs_in_the_bounded_local_lane(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* row_major_table */'
    monkeypatch.setattr(importlib.import_module('decomprepair.row_major_table'), 'variants',
                        lambda *args, **kwargs: [('row_major_table', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert child in tried
