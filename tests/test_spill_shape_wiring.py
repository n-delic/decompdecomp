"""decomprepair 0.5.562's spill-shape families reach the draft repair path: a draft that carries a local struct of volatile
fields (the workaround that stood a 363-instruction KMC function at 1025) is offered the plain-locals form, the
duplicate-arm collapse and the other steps of the chain that reached byte-identical, each labelled with its family."""
from decompnet.app import draft_repairs

NAME = 'step'
FAMILIES = ('volatile_read_drop', 'volatile_state_unwrap', 'identical_arm_collapse')

DRAFT = """\
void step(void **list, s32 mode, f32 *shift, f32 *angles, s32 mask)
{
  u8 vals[4];
  volatile u8 mirror[2];
  s32 narrow;
  u32 *cursor;
  u32 *next;
  struct
  {
    u32 pad0;
    volatile s32 mode;
    f32 * volatile angles;
    volatile u8 done;
    volatile s32 flags;
  } state;
  state.mode = mode;
  state.angles = angles;
  state.done = 0;
  narrow = *(volatile s32 *)&mask;
  if (list[0] != NULL)
  {
    cursor = list[0];
    next = cursor + 1;
    state.flags = (s8) narrow;
    do
    {
      vals[0] = next[0] * 0.015625f;
      vals[1] = next[1] * 0.015625f;
      if (state.flags)
      {
        use(state.mode, state.angles);
      }
      else
        if (mask || vals[0])
      {
        other(state.mode);
      }
      else
      {
        other(state.mode);
      }
      if (cursor[0] == 3)
      {
        state.done = 1;
      }
      next += 2;
      cursor += 2;
    }
    while (!state.done);
  }
}
"""




def test_spill_shape_families_are_proposed_labelled_and_known():
    labels = {}
    result = draft_repairs.variants(DRAFT, NAME, 'mips', labels=labels, limit=None)
    assert set(FAMILIES) <= set(labels.values())
    unwrapped = next(t for t in result if labels.get(t) == 'volatile_state_unwrap')
    assert 'struct' not in unwrapped and 'state_done' in unwrapped
    for family in FAMILIES + ('flag_exit_mask_test', 'loop_constant_stage', 'narrow_copy_merge'):
        assert family in draft_repairs.AGENT_FAMILIES
