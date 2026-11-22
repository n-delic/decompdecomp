---
title: a search-scaffolded sorted-list insertion written plainly, then a loop key copy (240 -> 0)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function looked like and the exact compiler and flags: a 129-instruction N64 function (KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2`) that moves an item between two groups and inserts it, sorted, into a doubly linked
list of groups (head, a forward and a backward link table, 0xFFFF for none). Saved best 145 (15k automatic trials and
five agents had not moved it), rows: 17 register, 2 reordered, one extra `move`. The saved source carried what a long
search leaves: a `union { T *pointer; u32 value; }` sharing one register, three `if (x) { S } else { S }` with equal arms,
an identity `inline static` around a cast, `register` on a parameter, a `(long long)` index, a copy of `(u8) group` made
before the branch that reads it, three word locals holding that byte, and word locals every use truncated.

The source change, and the measured result before and after (the app's judge, recorded 145 -> 0):
1. Strip the scaffolding together: 145 -> 240. Each part alone is no better (union split 485, then equal arms 385,
   identity inline 465, merged byte local 965 before it was set inside the branch, 365 after, narrowed 240): a greedy
   chain never takes the first step, and the families that split a union (with an initializer) or collapse arms
   (inside an `else` it dropped all but the first statement) had gaps of their own.
2. A copy of the loop's invariant comparand (`u16 pos`) taken at the loop, the loop and the stores after it reading the
   copy: 240 -> 50 (copy before the cursor's start) or 240 -> 0 (after it; `pure_preheader_order` does that swap).
Final source: plain natural code, no scaffolding. Matching it needed `u16` for the byte local and the cursor, the
byte re-derived from `(u8) group` at every index, and the copy `key = pos;` written after `cur = head;`.

Why (a hypothesis from the registers, not read from the compiler's source): the loop-header label ends cse's extended
block, so the loop's read of the zero-extended position is hoisted by loop.c into a pseudo that cse2 turns into a
`move`; the target has that `move` (`move $a1,$a3`) and a second one for the stored position (`move $t0,$a1`), which
only a source copy of the invariant gives. The union shared the position's register with the pointer, which put it in
`$s0` where the target has `$a1`/`$t0`.

Failed experiments that would otherwise be repeated: every single scaffolding removal on the saved best (greedy chain
and `--combine 10` both stalled at 145); a beam over the neutral children (all 145); the first natural rewrite with
`u32` locals and an `if (head == 0xFFFF)` first (4671: the empty arm was placed first, not last).

What is controlled evidence and what is one function: one function; each step scored by the app's exact judge and the
whole chain reproduced from the saved 145 by `prove_chain.py --families all` (one step: `scaffold_strip_key_copy`).
Library: decomp-repair `scaffold_strip`, `loop_invariant_key_copy`, `phase_storage_split` (initializer),
`identical_arm_collapse` (else body), PR RetroReversing/decomp-repair#1259 (all declare REPAIR).
