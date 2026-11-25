---
title: Identical float pool words mean separate source literals (SN 2.8.1 li.s)
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags: a 144-instruction actor update built by SN
GCC 2.8.1 (`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32`, a per-function profile) that dispatches on an
id (1, 3, 4, 5, anything else) and, in each arm, calls a float helper with `ground + 450.0f` and then plays a sound.
The ROM has three separate pool words holding 450.0 (three consecutive addresses), one loaded by the id-1 arm, one by
the shared tail that arms 3, 4 and 5 jump into, one by the default arm.

The rule: SN 2.8.1 emits a float constant as the assembler macro `li.s $fN, 450.0`, and the assembler gives every
macro instance its own pool word; nothing deduplicates them. Cross-jumping runs on RTL, before that, where the three
arms' identical `li.s` insns compare equal. So:
- identical float pool words in one function are as many separate literals in the source (or as many uses left after
  cross-jumping), never one shared `extern f32`;
- arms that share one pool word and one tail (`j` into the middle of another arm, after the arm's own byte test)
  were written out in full in each arm, and cross-jumping merged them;
- an arm with a pool word of its own was not merged, because something before its tail differs (here the id-1 arm
  computes `x * 6` with `sllv` by the register that holds 1, and the merge point is after that).

The source change, and the measured result before and after (scores, the judge used): the draft had one flag byte
selected by an if-chain and a single shared `if (flag) { call; sound; }` block (4650 with `--flags=-mfp32` in
fast_judge, the block placed at the end). Writing the call and sound tail out in every arm, with `450.0f` literals
each time, was exact (fast_judge 0, then the app's `compiler_experiments` trial with `-mfp32` was EXACT and applied
with `suggest`).

Failed experiments that would otherwise be repeated: a `switch` with a `goto check;` join for arms 3-5 (6620), and a
`switch` whose arm re-tests the id to pick the byte (6715).

What is controlled evidence and what is one function: one function. The per-instance pool behaviour follows from the
`li.s` macro and fits every SN 2.8.1 function seen with repeated float constants; the judge places each candidate
literal at the ROM word its instruction loads, so one literal per use links correctly. decomp-repair `label_run_copy`
and `goto_run_copy` already copy a shared tail into its arms; this entry is the signal that tells you to use them.
