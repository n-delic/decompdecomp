"""decomprepair chain_weight_scope (a flag chain in one-shot scopes with its later reuse split off) and
goto_loop_sibling_shape (a goto row loop as do/while with its commuted statement written like its siblings): a
167-instruction KMC function 35 -> 25 -> 0. Both declare themselves in the library registry; the drafts and the Deep
Permuter's local lane reach them with no list to extend."""
import importlib

import pytest

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

CHAIN = """\
typedef struct { s16 limit; s16 shape; s32 base; } Rec;
extern Rec *gRec;
extern s16 gTable[];
void consume(s32, s32, s32);
void f(u32 frame, s32 extra)
{
  s32 flag;
  s32 shape;
  s32 count;
  Rec *rec;
  rec = gRec;
  flag = frame < ((u32) rec->limit);
  shape = rec->shape;
  count = gTable[shape & 0x7fff];
  frame &= -flag;
  shape &= 0x8000;
  consume(count, frame, shape);
  flag = rec->base + extra;
  consume(flag, flag, flag);
}
"""

ROWS = """\
typedef struct { f32 scale[3]; f32 offset[3]; } Frame;
void g(f32 *coords, u16 *packed, Frame *frame, s32 n)
{
  s32 i;
  i = 0;
  if (n > 0)
  {
    __repair_rows:
    {
      f32 z;
      coords[0] = (packed[0] * frame->scale[0]) + frame->offset[0];
      coords[1] = (packed[1] * frame->scale[1]) + frame->offset[1];
      z = frame->offset[2] + (frame->scale[2] * packed[2]);
      i++;
      packed += 3;
      coords[2] = z;
      coords += 3;
    }

    if (i < n)
    {
      goto __repair_rows;
    }
  }
}
"""


def test_the_drafts_offer_the_flag_chain_scopes_with_the_split():
    labels = {}
    draft_repairs.variants(CHAIN, 'f', 'mips', labels=labels, limit=None)
    assert 'chain_weight_scope' in set(labels.values())
    assert 'chain_weight_scope' in draft_repairs.AGENT_FAMILIES
    offered = [text for text, family in labels.items() if family == 'chain_weight_scope']
    assert any('__repair_phase_flag' in text and text.count('do {') == 2 for text in offered)


def test_the_drafts_offer_the_row_loop_as_one_child():
    labels = {}
    draft_repairs.variants(ROWS, 'g', 'mips', labels=labels, limit=None)
    assert 'goto_loop_sibling_shape' in set(labels.values())
    assert 'goto_loop_sibling_shape' in draft_repairs.AGENT_FAMILIES
    assert any('} while (i < n);' in text and 'z = (packed[2] * frame->scale[2]) + frame->offset[2];' in text
               for text, family in labels.items() if family == 'goto_loop_sibling_shape')


@pytest.mark.parametrize('name', ('chain_weight_scope', 'goto_loop_sibling_shape'))
def test_they_run_in_the_bounded_local_lane(monkeypatch, name):
    from tests.test_permute import AGENT

    net = setup(monkeypatch, declared=(name,))
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + f'\n/* {name} */'
    monkeypatch.setattr(importlib.import_module(f'decomprepair.{name}'), 'variants',
                        lambda *args, **kwargs: [(name, child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert child in tried
