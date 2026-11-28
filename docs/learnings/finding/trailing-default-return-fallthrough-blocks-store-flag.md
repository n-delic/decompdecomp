---
title: Trailing default return fallthrough blocks jump.c store-flag and preserves cross-jump labels
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 3,756-instruction N64 grid-piece adjacency validator built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`
(11 of its 12 nearest matched neighbours used `-O1`, whereas this function's default profile was `-O2`).
In the target, several decision arms end with an explicit zero-return branch sequence
(`beqz $v0, epilogue; move $v0, $zero; j epilogue; addiu $v0, $zero, K` for `K` in `{1, 2}`), and six earlier
two-flag blocks (`if (f1 && f4)`) keep their own `if (f1 && f4) return 3; if (f1) return 2;` bodies before
cross-jumping only their trailing `if (f4) return 1; return 0;` tail to the last `beqz` block.
In the candidate seed, ternary returns (`return call(...) ? K : 0;`) and inner arm-ending `return 0;` statements
compiled to `sltu` / `sll` store-flag sequences instead, `reorg.c` turned five `beqz`/`bne` branches into
branch-likely `beql`/`bnel`, and `jump2` cross-jumped four of the six `(f1 && f4)` blocks all the way back to the
first one.

Reading `gcc-2.7.2-kmc-source/jump.c` (`jump_optimize`, lines 727-833 and 1014-1305) confirmed why:
- Under `-mips2`/`-mips3` (`PROCESSOR_R4000`, `BRANCH_COST == 2`), `expr.c` expands `cond ? K : 0` via store-flag,
  and `jump.c` lines 727-833 convert `if (cond) return K; return 0;` into `$v0 = 0; if (cond) $v0 = K;` (and then
  store-flag at lines 1014-1305) whenever the zero-return label immediately follows the jump and every reference to
  that label can be walked backwards without crossing another jump or `CALL_INSN` (`LABEL_NUSES` reaches 0).
- When inner `if`/`else if` arms omit their local `return 0;` and instead fall out to a single trailing `return 0;`
  at the end of the function, `LABEL_NUSES` on the shared zero-return label includes earlier outer guards across
  intervening `CALL_INSN`s and branches. `jump.c` breaks out of the backward scan (`nuses != 0`), leaving the explicit
  `beqz $v0, epilogue; move $v0, $zero; j epilogue; addiu $v0, $zero, K` sequence intact.
- In `jump2`, cross-jumping to that intact trailing sequence creates a new label during the pass (`jump.c` only
  searches `jump_chain` partners for labels older than the current pass), which stops the six `(f1 && f4)` blocks
  from merging their earlier instructions together.

The source change, and the measured result before and after (scores, the judge used):

Measured with the app's judge (`fast_judge.py` and `compiler_experiments` + `suggest` under
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`):
1. Switched per-function profile from `-O2` (saved score `68127`; structured seed `79590`) to `-O1` (`10515`).
2. Applied all four parts of the trailing-default-return fallthrough rewrite together:
   - Removed 22 redundant arm-ending `return 0;` statements whose blocks already fall out to the function's final
     `return 0;`.
   - Converted two `return (call(...) && a == b) ? 2 : 0;` arms in the third state to `if (call(...) && a == b) { return 2; }`.
   - Converted the six sequential `if (f1 && f3) ... if (f1 && f4) { ... return 0; } if (f2 && f3) { ... return 0; } ... return 3;`
     blocks into `if ... else if ... else { return 3; }` chains without inner `return 0;`.
   - Converted the eight `if (f1) { return K; } if (f2) { return call(...) ? K : 0; } return K;` blocks into
     `if (f1) { return K; } else if (f2) { if (call(...)) { return K; } } else { return K; }`.
   Combined result: `10515 -> 0` (exact byte-for-byte match).

Failed experiments that would otherwise be repeated:

- `-G0 -mips1 -mgp32 -mfp32 -O1` (`157045`, load delay nops) and `-mcpu=4600 -O1` (`104425`, `BRANCH_COST == 1`
  stopped `&&` condition `sltu`/`and` folding throughout the function).
- Changing callee declarations from `u8(u8)` to `s32(s32)` (`10515 -> 22495`, lost required `andi $v0, $v0, 0xff`
  narrowing).
- Converting `return call(...) ? K : 0;` to `if (call(...)) return K; return 0;` while keeping local `return 0;`
  statements (`10515 -> 10840`, `jump.c` still folded them to store-flag).
- Applying any single sub-edit of the fallthrough rewrite alone or partially:
  - Dropping the 22 arm-ending `return 0;` statements alone: `10515 -> 12470`
  - Rewriting the third-state `? 2 : 0` ternaries alone: `10515 -> 13605`
  - Converting the six multi-flag blocks to `else if` without inner `return 0;` alone: `10515 -> 16205`
  - Converting the eight `f1`/`f2` blocks to `else if` fallthroughs alone: `10515 -> 16430`
  - First two together: `11485`; first three together: `11915`; all four together: `0`.

What is controlled evidence and what is one function:

Controlled: `decomprepair.trailing_return_fallthrough` (family `trailing_default_return_fallthrough`) takes the
`10515` pre-fix `-O1` seed and emits a single combined child that scores `0` in the exact judge, matching the
mechanism verified in `gcc-2.7.2-kmc`'s `jump.c`.
