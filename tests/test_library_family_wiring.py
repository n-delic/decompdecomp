"""Every decomprepair repair module reaches a lane, so the Deep Permuter can propose it.

The Deep Permuter's beam runs `draft_repairs.variants` with no limit, so a family wired there reaches drafts and the
beam alike. Library modules (register levers, indexed records) had
`variants` but no caller anywhere in the app: no agent, draft or permuter ever saw them. This test lists the library's
modules with a `variants` function and fails for any the app never names (directly or through another library
module that calls it)."""
import pathlib
import re

import decomprepair
from decompnet.app import draft_repairs

SRC = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'decompnet'


def test_every_library_variants_module_is_reached():
    library = pathlib.Path(decomprepair.__file__).parent
    modules = {path.stem: path.read_text() for path in library.glob('*.py')}
    app = '\n'.join(path.read_text() for path in SRC.rglob('*.py'))
    unreached = []
    for name, text in modules.items():
        if not re.search(r'(?m)^def variants\(', text):
            continue
        word = re.compile(r'\b' + name + r'\b')
        if word.search(app) or any(word.search(other) for key, other in modules.items() if key != name):
            continue
        unreached.append(name)
    assert unreached == []


def test_the_newly_wired_families_are_proposed_and_known_to_agents():
    source = """\
void f(int *p, int a, int b)
{
  int x;
  int y;
  x = a + b;
  y = b * 2;
  if (a) { p[0] = x; } else { p[0] = x; }
  p[1] = y;
}
"""
    labels = {}
    draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    assert {'allocation_lever', 'identical_arm_collapse'} <= set(labels.values())
    assert {'allocation_lever', 'indexed_record_base', 'identical_arm_collapse'} <= set(draft_repairs.AGENT_FAMILIES)


def test_crossing_scope_unwrap_is_proposed_and_known_to_agents():
    """A scope that a goto jumps into came off by hand and took a register swap from 20 to 0; drafts and the beam offer it."""
    source = """\
s32 pick(void *actor, Rec *recs, s32 count)
{
  u32 index;
  u32 threshold;
  u32 limit;
  u8 *entry;
  u16 id;
  threshold = rnd(count);
  do
  {
    limit = count - 1;
    do
    {
      entry = (u8 *) recs;
      scan:
      if (entry[4] < threshold)
      {
        index++;
        goto advance;
      }
      id = *((u16 *) (entry + 2));
    }
    while (0);
    if (id == 0)
    {
      return 0;
    }
    use(actor, id);
    advance:
    entry += 4;
    if (index < limit)
    {
      goto scan;
    }
  }
  while (0);
  return 0;
}
"""
    labels = {}
    draft_repairs.variants(source, 'pick', 'mips', labels=labels, limit=None)
    assert 'crossing_scope_unwrap' in set(labels.values())
    assert 'crossing_scope_unwrap' in draft_repairs.AGENT_FAMILIES


def test_range_assignment_switch_and_scalar_reload_volatile_are_proposed_and_known_to_agents():
    """A KMC gcc 2.7.2 menu function went 30 -> 0 by collapsing a duplicated store, writing m2c's range-test tree as the
    switch and declaring the stored-then-read flag volatile; drafts and the beam offer the last two."""
    source = """\
extern s32 *gState;
extern u16 gAction;
void f(void)
{
  s32 state;
  s32 var;
  state = *gState;
  if (state != 0x1A)
  {
    if (0x1B > ((s32) state))
    {
      var = 3;
      if (state != 0x19)
      {
        var = 1;
      }
    }
    else
    {
      var = 1;
      if (state == 0x1B)
      {
        var = 3;
      }
    }
  }
  else
  {
    var = 2;
  }
  gAction = var;
  g(gAction);
}
"""
    labels = {}
    out = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    assert {'range_assignment_switch', 'scalar_reload_volatile'} <= set(labels.values())
    assert any('switch (state)' in text and 'case 0x19:' in text for text in out)
    assert any('extern volatile u16 gAction;' in text for text in out)
    assert {'range_assignment_switch', 'scalar_reload_volatile'} <= set(draft_repairs.AGENT_FAMILIES)


def test_arm_switch_volatile_chain_is_proposed_and_known_to_agents():
    """The collapse, the grouped range switch and the volatile reload only pay off together (30 -> 0, a gcc 2.7.2 menu function)."""
    source = """\
extern s32 *gState;
extern u16 gAction;
extern s32 gGuard;
void f(void)
{
  s32 state;
  s32 var;
  state = *gState;
  if (state != 0x1A)
  {
    if (0x1B > ((s32) state))
    {
      var = 3;
      if (state != 0x19)
      {
        var = 1;
      }
    }
    else
    {
      var = 1;
      if (state == 0x1B)
      {
        var = 3;
      }
    }
  }
  else
  {
    var = 2;
  }
  if (gGuard)
  {
    gAction = var;
  }
  else
  {
    gAction = var;
  }
  g(gAction);
}
"""
    labels = {}
    out = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    chained = [text for text in out if labels.get(text) == 'arm_switch_volatile_chain']
    assert chained and all('extern volatile u16 gAction;' in text and 'switch (state)' in text for text in chained)
    assert 'arm_switch_volatile_chain' in draft_repairs.AGENT_FAMILIES
