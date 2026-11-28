---
title: leader banner draft chain to zero
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

An N64 text banner of 242 instructions (it picks a message by mode and player scores and draws it), SN GCC 2.8.1,
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The m2c draft, kept by autopilot, scored 5416.

By hand: plain C with a `char text[0x100]`, `strcpy` of each message, `sprintf` for the numbered ones, and the draw call
made in every branch (cross-jumping merges the tails) scored 620. Declaring the draw function's fourth parameter `f32`
and passing `1.3f` instead of its bits gave 0: with an integer the scheduler loads the third argument before the `lui`
of the fourth, with the float after.

From the saved draft, the library chain that reaches 0 (decomp-repair PR 2255), measured with `prove_chain` and the
app's standalone judge: `symbol_stack_block_copy` 1276 (1676 without the `unkspNN` tails), `float_argument_bits` 656,
`goto_selector_call_split` 236, `zero_running_max` 6, `stack_buffer_frame_fit` 0.

- The string copies are one block move each; a tail m2c stores to `unksp2E` belongs to it.
- `score > 0U` where the target has `sltu $v0, $a2, $v1` was `score > best` with `best` still 0, and the two zero
  initialisations are adjacent (the second is a register copy of the first).
- The argument area of a frame must not be read from stores into an address-taken buffer.

Failed: `goto_paths_written_out` on the 420 state duplicated the player-count tests and scored 855.

This is evidence from one function; the new rules have synthetic unit tests in the library.
