---
title: A pointer stepped in the loop condition keeps gcc's single test (string length)
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

An 8-instruction N64 string length, SN GCC 2.8.1 (`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`).
Target: `j test; move $v1,$zero; loop: addiu $v1,1; test: lbu $v0,0($a0); bnez $v0,loop; addiu $a0,1; jr $ra;
move $v0,$v1`. There is no copy of the exit test in front of the loop.

The source change, and the measured result before and after (scores, the judge used):

m2c's draft `if (*p != 0) { do { p += 1; n += 1; } while (*p != 0); }` scored 310 (the app's judge), through 16
attempts. `while (*p++ != 0) { n += 1; }`, `for (n = 0; *p++ != 0; n += 1) {}`, `for (n = 0; *s++; n++) {}` and a
hand-written goto loop that tests `*s++` at the bottom are all 0. decomp-repair `scan_postinc_while` writes the
first form from the draft (310 -> 0 in one `prove_chain.py` step).

Failed experiments that would otherwise be repeated:

No child of the library's other loop families improved the draft (11 judged, `guarded_do_while_loop` and
`guarded_sentinel_while` among them: they give `while (*p != 0) { p += 1; .. }`). The saved best at -O1 and -O3
was 310 as well, at -O0 3035.

What is controlled evidence and what is one function:

One function. That the side effect in the condition is why the compiler does not copy the test is a hypothesis,
not read from the compiler source. The two forms are the same program only when the pointer is not read after the
loop (it ends one element further).
