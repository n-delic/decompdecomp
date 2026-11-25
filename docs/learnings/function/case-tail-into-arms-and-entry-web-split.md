---
title: a case tail written into each arm, and the entry value in its own local
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-25
---

What the function looked like: a 485-instruction N64 audio thread (`for (;;) { osRecvMesg(...); switch (msg->cmd) { 33
cases } }`), SN64 GCC 2.8.1 with the project's `-mfp64` profile. Its stored assembly came from a boundary fix and had
no `%hi`/`%lo` annotations, so the judge's score was mostly relocation rows (150 each) and the real rows were a few
register and order differences.

The source change and its measured results (local judge, current profile):
- One case was `if (arg == cur) { if (handle != 0) break; } else { ...; play(arg, bank); cur = arg; }` followed by
  `handle = start(bank); setvol(handle, vol); break;`. The target builds the bank address on each path (from the hoisted
  `$s6` on one, with a fresh `lui $t0; addiu $a0, $t0, lo` before a `j` into the shared `jal` on the other) and passes
  the call result straight to the next call. Both statements written at the end of each arm: 26300 -> 25060 (the first
  statement alone 25760). jump2 cross-jumps the identical tails, which is where the `j` into the middle comes from.
- The entry code's `h = init(); setup(); report(fmt, h);` reused `h`, which the loop also assigns; the entry value took
  `$s1` where the target has `$s0`. The entry web in its own local: 25060 -> 25050, with no differing register or
  instruction rows left.

What still blocks it: the switch's jump table. Unannotated target assembly means no target relocation for the
table's `lui`/`addiu` pair, so the linker cannot place the candidate's `.rdata` ("your code refers to .rdata+0x0 where
the ROM's code has no address"). decompdecomp #3608/#3610 rebuild the annotations for boundary-adjusted functions.

Failed experiments: the earlier agent's other profiles (sn-n64-272-0001/0004/0006, `-mfp32`) and the init-store orders.

Controlled vs one function: one function. decomp-repair `tail_run_into_arms` then `entry_web_split` reproduce both
steps from the saved best; the existing arm families (`call_into_each_arm`, `switch_arm_tail_calls`, ...) offered no
improving child (they move a single statement call only).
