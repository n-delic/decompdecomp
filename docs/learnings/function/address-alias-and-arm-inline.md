---
title: Address alias spelling and arm-inline locals close a menu function (1930 -> 920 -> 0)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function looked like: a 335-instruction N64 menu function (KMC GCC 2.7.2) that formats text into one global
buffer in several calls, one before a four-way `switch` whose arms call it again, then two two-armed branches whose calls
both take `p - 0x28`. Saved best was an m2c-shaped source with agent scaffolding (goto ladders, `& 0xFF`, identical arms).

The source change, and the measured result: a clean source scored 1930. (1) The four call-argument uses of the buffer's
address that the target shares in one register (two calls and the switch's first two arms) written
`(s32 *)((u8 *)&D_800D2858 + 0xC)`, the others plain: 1930 -> 920. The target's last two arms load the address afresh;
with every use the same `symbol_ref` cse shares it and jump2 merges all arms (equal REG_EQUAL notes let find_cross_jump
replace `move $a1,$s0` by the constant); a different rtx at the same address is a separate class and blocks the merge.
(2) `x = p - 0x28; if (c) f(x..) else g(x..)` written with `p - 0x28` in each arm, first for one branch 920 -> 860, then for
the other 860 -> 0. A `cc1` built with prints in reorg.c showed why: the shared local's setter is moved into the branch
slot by the simple pass, leaving a `USE` that `mark_target_live_regs` reads as "$a0 live" for an earlier branch's target,
so that branch could not take its own `$a0` setter into its slot.

Failed experiments: a pointer variable set in both arms of a diamond (to lose cse's class) also split the arms but moved
the `lui` before the call and cost an extra reference that swapped two saved registers; sets inside a skipped block are
re-linked by the second cse pass; shuffling case order, `la` presets, dead statements and relay temporaries changed
nothing real (a relay temp moved one row and left junk moves).

Evidence: one function, but each step was measured alone and together (alias 1930->920; inline needs the alias). The
library families are address_alias_spelling and arm_shared_arg_inline (decomp-repair #1343); prove_chain from the clean
source reaches 0 with just those two.
