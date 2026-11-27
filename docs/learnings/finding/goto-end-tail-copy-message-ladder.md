---
title: A message ladder's shared calls written in every arm (goto into the function's last run)
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-27
---

A 160-instruction N64 message function: thirteen values of one global, each formatting a line (`sprintf`) and drawing it
with its own x position. m2c wrote sibling `if`s that set `var_a1` (and a format pointer) and `goto` two labels inside
the last `if`, where the two calls sit. Saved best 11440 after 39 attempts.

The source change, measured with the app's judge:

- the run behind each label written in place of every `goto`, with `return;` (`goto_end_tail_copy`): 11440 -> 60;
- the callee's fourth parameter `f32` and the call's `0x3FA66666` written `1.3f` (`float_argument_prototype`): alone no
  change (11440), after the copy 60 -> 0. The last two rows were `addiu $a2` ordered against the `lui`/`ori` of `$a3`:
  GCC schedules a float constant in an argument register differently from an integer one with the same bits.

The same two edits on a rewrite as a plain `if`/`else if` chain: 60 with the integer constant, 0 with the float.

Cross-jumping merges the thirteen copies of the draw call back into one, and the two arms with equal x also share the
`sprintf` call. The global is read again before the eleventh comparison; the C does not need to say so (hypothesis: cse's
path length limit, `PATHLENGTH` in `cse.c`, not checked in this compiler's source).

Failed experiments: `goto_shared_tail`, `goto_nested_run_copy`, `goto_arm_tail_inline` and `goto_run_copy` offer nothing
on this draft, since each guard continues with the next one and not with what follows the label.

What is controlled and what is one function: the two edits were scored alone and together on one function.
`float_argument_prototype` reads the target's `jal` by the callee's name, so assembly kept under the old `func_` name
must be renamed first (the app's `repair_asm.current` does this).
