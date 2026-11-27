---
title: A local passed as a masked call argument is narrow, and a folded first loop test is a while
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 141-instruction N64 digit-drawing routine (gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`, exact). The saved source was
2000 under that profile.

- The target masks two arguments on the way into their registers (`andi $a1,$s1,0xffff` before the `jal`) where the
  source only moved them: the locals are `u16` (`call_argument_width_local`, 2000 -> 1295).
- m2c wrote `if (flag && t[8] == 0) { i = 0; do {..} while (t[8 + i] == 0 && i < 4); s = a; }`; the source was
  `if (flag) { i = 0; while (t[8 + i] == 0 && i < 4) {..} s = a; }` (`guard_first_test_while`, 2000 -> 1575).
- `prove_chain` with both goes 2000 -> 15; the last rows are the loop counter's initial value (a one-shot scope). By hand,
  a clean source (`for` digit loop, two `u16` arrays, `u16` copies of the second and third parameters, the second
  parameter declared `s16`) is exact.

The `s16` parameter alone took a clean candidate from 125 to 0 (register allocation); on the saved source it made the
score worse, so it is one function's observation and not a repair.
