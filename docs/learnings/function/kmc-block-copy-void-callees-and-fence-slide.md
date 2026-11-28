---
title: Three KMC near-misses: a record copy, void callees and a fence before the first call
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1/-O2 -g
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

Three functions of one N64 KMC game, each with only register or order rows left:

1. A 24-instruction `-O1 -g` reset routine at 20: a 64-bit global zeroed through `$a2/$a3` where the target has
   `$v0/$v1` (`move; move; sw; sw` is reload's pair for the DImode zero, `movdi_internal` has no memory-from-zero
   alternative). The callees were m2c's `M2C_UNK f(...)`: a call that sets `$v0` makes it ever-live, and reload1.c's
   `order_regs_for_reload` ranks registers never live first, so the pair skipped `$v0`.
2. A 27-instruction `-O1 -g` loop copying 96 three-word records at 25: the loop counter in `$a2` where the target has
   `$a3`. The saved best copied through three word temporaries (and a `volatile` on the last store that only stood in).
   A struct assignment is a `movstrsi` block move with its own scratch registers (`$v1, $a0, $a1` and `$a2`), so the
   counter goes to `$a3`.
3. A 49-instruction `-O2 -fno-expensive-optimizations -g` chunked transfer loop at 60: the last prologue save before
   the first `jal` where the target has it in the call's delay slot. The saved best had `do { total = 0; } while (0);`
   above the call: reorg filled the slot with that constant, the nearest earlier insn.

The source change, and the measured result before and after (scores, the judge used):

1. Both callees `void`: 20 -> 0. decomp-repair's `discarded_result_void` already reaches it from the saved best
   (`prove_chain --families all`, one step).
2. Each copy as `*(Rec *) dst = *(Rec *) src` (a typed record with `dst[0x60]`/`src[0x60]` arrays also matched; the
   `volatile` went): 25 -> 0. No family reached it (`prove_chain --families all --max-judged 0`: 239 children, none
   better); decomp-repair `word_temp_block_copy` (0.6.384) does in one step.
3. `total = 0;` plain, after the call: 60 -> 0. Opened in place it is 445, so a greedy chain never took it (667
   children, none better); decomp-repair `fence_unwrap_slide` (0.6.384) offers the opened-and-slid child.

Failed experiments that would otherwise be repeated:

1. A `u64` store (20), two `s32` zero locals (210), an `s64` zero local (20), an `s64` extern (1500); the SN packs at
   the same flags (20, 430).
2. Moving the counter's declaration last (25), dropping only the `volatile` (185).
3. Removing the fence in place (445).

What is controlled evidence and what is one function:

Each is one function, judged by the app. The reload ordering and the block move's scratch registers are read from the
compiler's dumps (`.lreg`, final `.s`); the reorg reading is a hypothesis consistent with the one function.
