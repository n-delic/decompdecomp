---
title: string decode store copy arm split
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

**Function and compiler.** A 300-instruction N64 text-entry handler (a keyboard-style input, several `menuItemIsActive`
tests, a decode loop over a string list), `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, saved best 230 and then 0
(`compile_and_diff`: byte-identical). The inner loop writes each decoded byte twice, `buf[i] -= 1` through the first
address and `buf[i + 1] = c`; the target's second store is `move $v0,$a0; addu $v0,$v0,$a2; ... sb $v1,($v0)` with the
first address `addu $v1,$a0,$a2`, the string pointer in `$a1` and the buffer in `$a0`.

**What was wrong in the saved best (230).** `buf[(unsigned char) i] = c` (an `andi` the target does not have), the
buffer and string pointers' registers exchanged, and the `move`. The cast was scaffolding that kept loop.c from reducing
the address givs; without it the default profile strength-reduces the loop (330), so the cast came out together with
the rest.

**The edits, with each step's score (`fast_judge`, default profile).**
1. Drop the cast, second store as `q = buf; q += i; *q = c;` inside do-while(0) scopes: 105 (one `move` missing).
   `-fno-strength-reduce` is not needed and no profile changed.
2. Write the add in both arms of `if (g) { q += i; } else { q += i; }` between the copy and the store, `g` a long-lived
   local the loop does not assign (`var_s0`, `temp_s1`, the loaded byte): 105 -> 30, the `move` appears; the buffer and
   string pointers' registers are still exchanged.
3. The address statement `t = buf + i;` in one scope, the copy in two, and the scope that held the store through `t`
   and the next byte's load split so the load is outside it: 30 -> 0. Each of those alone, and any two, stay at 30.

**Why the arms (read in the compiler's source, cc1 `-dr -dj -dc` dumps; a hypothesis for the original code).** Every
foldable spelling of the second address (`buf[i]`, `*(buf + i)`, `q = buf; q += i;`, `(u8 *)((u32) buf + i)`, inline
helpers, array or pointer base, 400 forms in a corpus loop) compiled to one `addu $v0,$a0,$a2`: cse propagates the copy
and combine merges it. combine.c folds `q = buf` into `q = q + i` only along a LOG_LINK, which flow.c (propagate_block)
makes from a use to its set only inside one basic block; cross-jumping (jump2) runs after register allocation. Two
identical arms are therefore two blocks at combine time, and merge afterwards: the copy stays, the branch (it tests a
register already live) is deleted. The saved best already carried the same device in front of the loop (`if (g) { S }
else { S }`).

**Why the guard matters (hypothesis from the allocation table).** A guard on the string pointer (incremented in the loop)
adds a reference that lifts its priority above the buffer pointer's: 30. A guard on a long-lived local the loop does not
assign leaves the table alone: 0.

**Failed experiments.** Both accesses through `sp20`; `sp20` in the first and a pointer in the second; the store in the
loop condition `while ((buf[i] = *p))`; a `while` loop; goto loops; `static inline` helpers (`put`, `at`); a pointer
local reused from after the loop (`out`, `temp_s0_2`: cse keeps the copy but combine merges it); every flag in
`FLAG_EXTRAS`, `-O3`, `-fno-cse-skip-blocks`, `-fno-rerun-cse-after-loop`; `prove_chain.py` with every library family from
the cast-free draft reaches 130 (`loop_goto_form`, `local_weight_ladder`), never the copy.

**What is one function and what is general.** One function; the mechanism (copy survives when an identical-arm guard
separates it from its add) is read from the passes and holds on a corpus loop. Library family `store_copy_arm_split`
(decomprepair, RetroReversing/decomp-repair#1279) offers the three edits as one child; from the saved best at 230
`prove_chain.py --families store_copy_arm_split,store_copy_arm_split_plain` reaches 0 in one step (6 of 36 children).
The match relies on scaffolding: the guard, the scopes and the pointer local; polishing removes them.
