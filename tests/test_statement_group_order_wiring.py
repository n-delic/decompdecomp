"""decomprepair 0.5.572's statement_group_order and counter_derived_offset_unwrapped reach drafts and the Deep
Permuter's beam (both run draft_repairs.variants).

A spilled-parameter N64 function (363 instructions, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`) scored 60 with its
three pre-loop assignments in any of five orders and 0 in the sixth; an 86-instruction scene-stack search scored 115,
20 with the offset edit, 190 with the one-shot unwrap alone and 0 with both. The shapes below are theirs, renamed."""
import itertools

from decompnet.app import draft_repairs

RUN = ["    scale = 0.015625f;\n", "    flags = (s8) arg4;\n", "    next = cur + 1;\n"]


def walk(order):
    return ("void walk(s32 *cur, s32 arg4)\n{\n  f32 scale;\n  s8 flags;\n  s32 *next;\n"
            + ''.join(RUN[i] for i in order)
            + "  do\n  {\n    *next = (s32) (flags * scale);\n    next++;\n  } while (*cur);\n}\n")


def test_every_wrong_order_has_the_target_order_as_one_labelled_child():
    target = walk((0, 1, 2))
    for order in itertools.permutations(range(3)):
        if order != (0, 1, 2):
            labels = {}
            found = draft_repairs.variants(walk(order), 'walk', 'mips', labels=labels, limit=None)
            assert target in found and labels[target] in {'statement_group_order', 'preloop_assignment_swap',
                                                          'allocation_lever'}
    assert 'statement_group_order' in draft_repairs.AGENT_FAMILIES


SEARCH = '''s32 f(u16 id, u8 flag)
{
  s32 i;
  s32 r;
  char *p;
  if (D_80100000)
  {
    {
      s32 offset = 0;
      for (i = 0; i < D_80100004; i++, offset += 4)
      {
        p = *((char **) (((char *) D_80100008) + offset));
        if ((*((u16 *) (p + 4))) == id)
        {
          do { if (flag) { r = g(p, 1); } else { r = g(p, 0); } return r; } while (0);
        }
      }
    }
  }
  return -1;
}
'''


def test_the_offset_edit_and_the_unwrap_come_as_one_child():
    labels = {}
    found = draft_repairs.variants(SEARCH, 'f', 'mips', labels=labels, limit=None)
    joint = [text for text in found if labels.get(text) == 'counter_derived_offset_unwrapped']
    assert joint and '(i << 2)' in joint[0] and 'while (0)' not in joint[0]
    assert 'counter_derived_offset_unwrapped' in draft_repairs.AGENT_FAMILIES
