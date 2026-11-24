---
title: variadic tail spills argument registers before the frame
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -O2 -G0 -mips3 -mgas -mcpu=VR4300
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags: a tiny printf-like N64 wrapper whose only
statement calls the formatter. The target opens with `sw $a0,0($sp)` .. `sw $a3,0xC($sp)` (the caller's argument
area) BEFORE `addiu $sp,$sp,-0x18`, then `sw $ra`, `jal`, and the usual epilogue. SN GCC 2.8.1, -O2 -G0 -mips3.

The source change, and the measured result before and after (scores, the judge used; isolated app judge):
- `s32 arg0..arg3` fixed parameters: 400 (no spill stores at all).
- `volatile s32 arg0..arg3` (or `volatile long long`/`volatile int`): 110 (spill stores present, but after the
  frame and at frame-relative offsets).
- `void f(s32 arg0, ...) { callee(); }` with `s32 callee();` declared: 0. The `...` makes the compiler spill all four
  argument registers to `sp+0..0xC` before the frame, and nothing else writes them there.
- `void f(s32 arg0, s32 arg1, ...)`: 100, `$a0` is not stored. With SN 2.8.1 the spill run starts at the register of
  the LAST NAMED parameter (hypothesis: va_start starts there), so spills from `$a0` mean one named parameter and
  from `$a1` two. (GCC 2.7.2 KMC stores only the unnamed registers, so there `$a1` can mean one named parameter.)
The library family `variadic_home_spill` (decomp-repair) rewrites the definition's parameter list (and an earlier
prototype) from that evidence: 110 -> 0 from the saved best.

Failed experiments that would otherwise be repeated: no existing family reached it (`varargs_signature` needs the C
to take the address of a spilled parameter; `empty_home_leaf` needs an empty body), and `volatile` parameters of any
width cannot reproduce the order, because the spills there come after the frame.

What is controlled evidence and what is one function: one function, one compiler; the named-count rule is read from
one `$a0` case and one `$a1` case of the same function (both measured).
