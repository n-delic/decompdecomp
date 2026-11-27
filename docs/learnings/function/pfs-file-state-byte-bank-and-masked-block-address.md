---
title: A remembered byte member and a masked block address (a controller pak file-state routine)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
a 99-instruction N64 SDK controller pak routine (it walks a file's page chain, loading one inode bank at a time) built
with KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`. Its saved best was an m2c draft under the game code's own compiler
(3152); the SDK's usual shape under KMC was 1000 before any other edit.

The source change, and the measured result before and after (scores, the judge used):

- The target keeps the walk's test at the top (`bnez` out, `j` back). `while (cond)` is rotated; the exit written as the
  `else` of the test (decomp-repair `loop_else_break`, already there) or as a `goto` past a `for (;;)` stops it
  (930 -> 210).
- The remembered bank was `s32`; declared `u8` like the member it holds, the converted member is computed once
  (`andi $a3,$v0,0xff`), compared with the local unmasked and passed on (210 -> 0). Library: `member_width_local`.
- The block address argument is a word sum the target masks (`andi $a2,$a2,0xffff` before the call) while the shared
  header's prototype takes a word: cast it at the call (160 -> 0 with the byte local already declared). Library:
  `call_argument_width_cast`.

Failed experiments that would otherwise be repeated:
`for (;;) { if (c) break; ... }` and `do { if (c) break; ... } while (1);` are still rotated (1135). A `(u8)` cast on the
call argument or a second `u8` temporary for the member, with the remembered local left `s32`, changed nothing. A `u16`
remembered local scored 15. `-O1` scored 2410 on the first rewrite; `-O3` scored the same as `-O2`.

What is controlled evidence and what is one function:
each score is the exact judge on this one function. That `expand_end_loop` rolls only a conditional jump to the loop's
end label is read from the pinned compiler's `stmt.c`.
