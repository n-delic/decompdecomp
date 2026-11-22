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


def test_loop_counter_value_split_is_proposed_and_known_to_agents():
    """A table initialiser's first loop counter, reused after the loop for the loaded count, went 20 -> 0 by splitting it,
    weighting the count's statements and sinking the next counter's init; drafts and the beam offer the chain."""
    source = """\
extern u8 n; extern char t[]; extern u8 a[];
void f(void) {
 u32 p,q,i;
 for (i = 0; i < n; i++) {
   a[i] = i - 1;
 }
 i=n;
 g=0;
 h=i-1;
 i &= 0xFF; p=0;
 if(i!=0){do{*(u16 *)(t+p)=0;p+=4;}while((s32)p<(u32)(i*4));}
 q=0; do {a[q]=0xFF;q+=1;}while(q<0x100U);
}
"""
    labels = {}
    out = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    assert 'loop_counter_value_split' in set(labels.values())
    assert any('i_loop' in text and 'do{do{i=n;' in text and 'if(i!=0){\n      p=0;' in text for text in out)
    assert 'loop_counter_value_split' in draft_repairs.AGENT_FAMILIES


def test_label_entry_initializer_is_proposed_and_known_to_agents():
    """A table-copy function's address was initialised before a label a goto enters past it (a skipped load); set at the
    label (30 -> 70) with the byte index widened first (70 -> 0) is one child, offered by drafts and the beam."""
    source = """\
extern u8 tbl;
void f(s32 a, void *p, void *q) {
    u8 v;
    u8 w;
    w = *((u8 *) q + 0x63);
    v = *((u8 *) p + 0x63);
    if (((a + 0x80) & 0xFF) < 2U) {
        goto entry;
    }
    if ((u8)(a + 0x7C) < 2) {
        u8 *base = &tbl;
        s32 dst;
        entry: dst = v * 0x86;
        base[dst + (a & 0xFE)] = base[(w & 0xFF) * 0x86 + (a & 0xFE)];
        return;
    }
}
"""
    labels = {}
    out = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    assert 'label_entry_initializer' in set(labels.values())
    assert any('entry: v_wide = v;\n        base = &tbl;' in text for text in out)
    assert 'label_entry_initializer' in draft_repairs.AGENT_FAMILIES


_WALK = """\
s32 walk(u32 id)
{
    s32 ret = -1;
    s32 head;
    s32 slot;
    s32 next;
    Node *node;
    Node *found;
    u32 key;
    head = -1;
    if (id != -1U)
    {
        found = NULL;
        node = first;
        if (node != NULL)
        {
            for (;;)
            {
                key = node->key;
                if (key == id)
                {
                    found = node;
                }
                else
                {
                    found = NULL;
                    if (id >= key)
                    {
                        node = node->link;
                        if (node != NULL)
                        {
                            continue;
                        }
                    }
                }
                break;
            }
        }
        if (found != NULL) head = found->head;
    }
    while (head != -1)
    {
        slot = (head & 0xFF) * 0x1A0;
        next = *(s32 *)(table + slot);
        if (*(s32 *)(check + slot) == head)
        {
            use(table + slot);
            head = *(u8 *)(table + slot + 0x63);
            last[head * 4] = 1;
            ret = 0;
        }
        head = next;
    }
    return ret;
}
"""


def test_the_voice_chain_walk_steps_are_proposed_and_known_to_agents():
    """A voice-chain walk went 1705 -> 1330 -> 905 -> 0 by sinking an assignment past its guard, writing the default as the
    else arm the guard jumps into, and making the dead parameter the loop's carrier with the body's reload split off; an
    address spelled `idx * 4` kept hoisting its symbol until it was `idx << 2` (decomprepair 0.5.645)."""
    labels = {}
    draft_repairs.variants(_WALK, 'walk', 'mips', labels=labels, limit=None)
    assert {'assignment_guard_sink', 'default_arm_goto', 'index_shift_spelling'} <= set(labels.values())
    chained = {}
    guarded = next(text for text, label in labels.items() if label == 'default_arm_goto')
    draft_repairs.variants(guarded, 'walk', 'mips', labels=chained, limit=None)
    assert {'param_loop_carrier', 'redefined_web_split'} <= set(chained.values())
    assert {'param_loop_carrier', 'redefined_web_split', 'assignment_guard_sink', 'default_arm_goto',
            'index_shift_spelling'} <= set(draft_repairs.AGENT_FAMILIES)
