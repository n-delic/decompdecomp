---
title: Dead float copies kept by an overwritten unsigned conversion, and a clamp compare fenced off its copy
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 1133-instruction effect spawner from #3178/#3179, at 70 (14 register rows, `gcc-2.7.2-kmc -G0 -mips3 -mgp32
-mfp32 -O2`). Two puzzles were left. (1) Each screen dimension has a long-lived float and a copy; the target keeps the
copies (`mov.s $f26,$f24` in the switch arms) although the copy's register is never read again before the sine loop
reloads it. (2) A fade clamp `out = level; ...; if (level >= 0x100) out = 0xFF;` where the target compares `level`
(`slti $a0,$a0,0x100`) and every spelling compared the copy (`slti $v0,$a1,0x100`).

The rules (read in the compiler's source):

- flow.c deletes a set whose register is dead (`insn_dead_p`, every pass), so a copy the target keeps was read when
  flow ran. A read that disappears *after* flow is the one shape that leaves it: an unsigned float conversion
  (`(u32)(2.0f * copy)`) whose result is overwritten before use. flow deletes the `trunc` insns but not the branch of
  the conversion (jumps are never dead), so the copy stays live; jump2 after reload then deletes the emptied branch and
  its compare, and nothing of the read is left in the output. A toy reproduces it (`a=K; b=a; if (c) {a=K2; b=a;}
  g(2*a); u=(unsigned)(2*b); u=*ip; h(u);` keeps both `mov.s` copies of `b`). An unused result is not enough: cse's
  `delete_trivially_dead_insns` removes a pseudo with no uses before flow, and a signed conversion is one insn flow
  deletes outright.
- local-alloc.c `optimize_reg_copy_1` rewrites reads of a copy's source into the copy up to the source's death in the
  block; its scan stops at a label, a jump or a NOTE_INSN_LOOP_BEG/END, which `do { out = level; } while (0);` leaves.
- local-alloc gives block-local quantities registers before global.c, so the compare's result takes the first register
  no local holds across it. With the clamp's `hi`, `mask` and `and` result as their own locals and the compare computed
  into a flag before the `and`, `hi` (`$v0`) and `mask` (`$v1`) are live across it and the flag lands in `$a0`.

The source change, and the measured result before and after (scores, the app's isolated judge, then the app):

| step | score |
| --- | --- |
| saved best | 70 |
| every horizontal read through `width`, `var_v1_4 = (u32)(2.0f * var_fs2);` before the vertical read | 80 |
| entry: constant into the copy (`var_fs2 = 160.0f;`), `width = var_fs2;` three statements later; arms: constant into `width`, then the copy | 40 |
| the vertical read through `var_fs1`, `if (height > 0.0f) var_v1_4 = 0;` after the conversion (keeps `height` live) | 35 (recorded) |
| fade: pixel loop's `<< 5` shares the level temp; `hi`, `mask`, `and` result as own locals; copy fenced; `flag = level < 0x100;` before the `and`, `if (!flag)` | 0 (recorded match) |

The fade step is decomp-repair `copy_compare_fence` (410 -> 0 from the state before it). The keep-alive is
scaffolding: dead code that reproduces what the original's dead reads did, not a known original spelling. A second
`(u32)` conversion for the vertical copy instead (90) adds references to the hoisted `0x80000000` constant and swaps
`$s6`/`$s7`; the float compare in the `if` has no such constant.

Failed experiments that would otherwise be repeated:

- Fence alone 5, flag alone 275; a fresh local for the level 140 (cse makes the copy canonical and folds it); a fresh
  local for `out` 480-575; the `and` after the `if` 155; a `do { } while (0)` around the `and` instead of the flag 275.
- Dead conversion of the copy before the first loop 2645-3047, inside the loop 105, in the late block 225: the place
  sets the copy's live range.

What is controlled evidence and what is one function:

The flow/jump2 keep-alive is reproduced on a toy; the clamp rules are read in local-alloc.c; the scores are one function.
