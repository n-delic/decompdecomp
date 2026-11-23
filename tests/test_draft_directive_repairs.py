"""A draft whose function holds a `#define` still gets the library repairs that read those lines."""
import pytest

from decompnet.app import draft_repairs, library_registry
from decomprepair import registry

SOURCE = '''typedef int s32;
extern void g(s32);

void f(void *arg0, s32 arg1, s32 arg2)
{
  volatile struct { int unused; int filter; } stack;
#define sp14 stack.filter
  s32 i;
  sp14 = arg2;
  for (i = 0; i < arg1; i++)
  {
    if (sp14 == i)
    {
      g(i);
    }
  }
}
'''

pytestmark = pytest.mark.skipif(not hasattr(registry.Repair, 'directives'),
                                reason='the pinned decomprepair predates the registry directives flag')


def test_the_registry_lists_the_modules_that_read_directives():
    names = [repair.module for repair in library_registry.runnable('draft', 'mips', directives=True)]
    assert 'volatile_stack_struct_drop' in names
    assert 'pair_pointer_struct' not in names


def test_a_function_with_a_define_is_offered_the_plain_local():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    plain = [text for text in children if labels.get(text) == 'volatile_stack_struct_drop']
    assert len(plain) == 1
    assert 'volatile' not in plain[0] and '#define' not in plain[0] and 'int sp14;' in plain[0]
