---
title: bit stream decoder histogram store and last pass limits
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -fno-schedule-insns
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 681-instruction N64 bit-stream decompressor of `bit-stream-decoder-loop-entry-roles.md` (KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2 -fno-schedule-insns`, no calls), resumed at the saved 110 (22 register rows). Every kept
source was built on the host beside the 110 source and run on 3000 generated streams (stored and coded blocks with
complete canonical code lengths, some invalid lengths, random tails and fully random words; each run in a forked child
with guard pages so a stream that runs off its buffers stops at the same place in both builds); output, error word and
every table compared. A deliberately wrong second-level step differed on 986 of 1500.

The source change, and the measured result before and after (scores, the judge used):

The histogram increment's `$v0`/`$v1` swap (5 rows). The loop body `*q1 = len; H[len] += 1; i++; q1++;` has exactly
three local quantities in its block: the element address (born first), the loaded count, the loop test. local-alloc.c's
`case 3:` exchanges compare quantity *numbers*, not positions (`local-alloc-three-quantity-block-sort.md`), so with
P(count) > P(address) > P(test) the order comes out address, count, test: the address takes `$v0`. Every spelling of the
update compiles the same because the three quantities stay the same. Written

    { u32 hv = H[len] + 1; i++; H[len] = hv; }

the count and address live two insns longer, the test now outranks the count, the exchanges hand out count, test,
address, and the count and the test share `$v0` (they don't overlap): the target's registers. 110 -> 85 (judge-worker
and `compile_and_diff`). `hv = H[len]; i++; H[len] = hv + 1;` and moving `q1++` too give 85 as well; putting the `*q1`
store between the load and the store instead gives 245. decomp-repair `rmw_store_past_step` (#1624) offers all four
children from the 110 source (all 85).

Still different at 85 (17 rows), with the rule that blocks each:

- the decode-table pass's mask (`sllv $v0,$s7,$s3`, 1 row): `(1 << tb) - 1` gives it but adds one depth-3 reference to
  `tb` (53 -> 56 weighted refs over 329 insns, 0.805 -> 0.851), which passes the last pass's `H + c` pointer (15 refs
  over 55, 0.818) and swaps `$s2`/`$s3` (170). The replay puts everything right with `tb` anywhere in (0.774, 0.818), or
  the pointer in (0.851, 0.95) (live 46..52). Every reference to `tb` the target's instructions show is needed (sets 2x
  depth 3; clear loop 4; first table 3 + 4 + 6; mask 3; second-level scan 4 x 4; last pass 3 + 5 + 6 = 56), so the
  remaining freedom is in loop depths or live ranges, not spellings: the clear and scan loops as goto loops (to drop a
  depth) gave 400 and 2425; `H[c]` or `(char *)H + off` for the pointer compile identically (170).
- the last pass (16 rows): the replay needs the inner loop's loop-made copy of `D_800B3134 + k` (11 refs over 15) below
  `off` (1.286), or below both `off` and the second-level base pointer with `off` above it. Two compiler facts make this
  hard and should save the next attempt time:
  1. loop.c emits an inner loop's movables just before its `NOTE_INSN_LOOP_BEG`, after every statement written before
     the loop, so the copy's live range is a subset of any pointer set before the loop: with equal references (11 each:
     a set at depth 5, one use at depth 6) the copy always outranks the base pointer. The copy's range only grows with
     movables emitted after it (the shift amount written inside the loop condition: 15 -> 18, but it then becomes a
     separate high-priority pseudo, 700), or with the base itself hoisted after it (the store's value staged in a
     temporary first: copy 20, base 14 and 2.357, 3354).
  2. find_reg's pass 0 only offers registers already used (`regs_used_so_far`: call-clobbered ones, those local-alloc
     assigned, and saved ones an earlier allocno took). The copy lands in `$s0` only if `$s0` is already used when its
     turn comes or `$t9` is taken; with the copy anywhere in (1.29, 1.77) it takes `$t9` (replay), because the two other
     `$s0` users (the first table pass's limit pointer, 1.08, and the scan's mask, 0.774) come later.
  A user pointer for the copy is folded (replaceable giv: `regno_first_uid` is its set, no biv update in its range, no
  back branch past `k++`); a reused function-level pointer is "not worth while" (its lifetime is the whole function and a
  non-replaceable user giv pays `copy_cost`), 1995; moving `k++` before the inner loop makes it non-replaceable (1.08) but
  moves the givs' increments with it (4798, 5028); the k-loop as a goto loop, 3803.

Failed experiments that would otherwise be repeated:

- The four histogram statements in all 24 orders: 110 or 560, never better (the quantities are the same).
- The last pass's base pointer set late (`a = ent >> 4; sb = ...; a &= 7;` and `a = ent; sb = ...; a = (a >> 4) & 7;`):
  the base reaches the replay's (1.81, 1.9) window (1.833), but with the copy still first the score is 350 / 400.
- The inner loop as `for (;;) { ...; if (...) break; }`: identical; as a goto loop: 3484; the store's operands swapped: 155.

What is controlled evidence and what is one function:

One function. The histogram step's mechanism is the compiler source's (`block_alloc`'s `case 3:`, read in local-alloc.c)
and the family's children were judged; the last-pass statements are the replay (`alloc_roles.py`, 77 of 78 allocnos
agree with cc1) and the 2.7.2 sources of loop.c (`move_movables`, `record_giv`, `check_final_value`) and global.c
(`find_reg`, `regs_used_so_far`), not a controlled test.
