---
title: narrow copy web save slot rank
kind: function
compiler: gcc-2.7.2-kmc, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like: a 154-instruction save-slot handler (halfword slot index, a byte "kind" read from a
record, a rank loop over the other slots when the kind is 2, otherwise a two-entry comparison; three calls). The saved
best (m2c plus earlier agents' scaffolding: identical-arm `if (temp) cursor++; else cursor++;`, a `u8 limit = rank`,
a do/while(0) around the arm, one shared `label` pointer, an unscaled `((u32) table) + index`) judged 655, 25 register
rows. `gcc-2.7.2-kmc`, flags as above, isolated judge (`decomp judge-worker`) and the app's `compile_and_diff` agree.

The source change, as an edit list (score alone from the cleaned base 770 -> combined 215, each step improves on its own):
1. `temp = kind;` (both `s32`) -> a copy local of the byte's own type (`u16 t2 = kind;`) read by the compare and the other
   arm's index: 770 -> 595 alone. cse (`make_regs_eqv`) makes a same-mode copy the byte's quantity and rewrites every
   read to it; a copy of another machine mode (`(set (reg:HI) (subreg:HI (reg:SI)))`, reads
   `(zero_extend:SI (reg:HI))`) is left alone (`insert_regs`), combine then turns the zero extension into a plain
   `move` (hypothesis for combine). With the copy gone the byte took the first saved register ahead of the slot
   (priority from refs and live length) and every saved register after it was exchanged. Any narrow width gave the same bytes.
2. `if ((temp++) != index) {..}` -> `if (temp != index) {..} temp++;`: no copy of the old value (it cannot share
   the counter's register) and reorg puts the increment in the branch's delay slot: 770 -> 655 alone.
3. one shared pointer `label` -> one pointer per arm (`local_web_split` already offers it): 770 -> 570 alone.
4. the second arm's element address spelled like the first's (`(Label **) inline_fn((char *) table, (s32) (((u32) index) << 16))`
   instead of `&table[index]`): 770 -> 585 alone; the base `lui` then goes to the register the target has (not
   offered by any family yet).
5. the rank loop's bound: `while (temp < gTotal)` (a `move $t0, $a2` from the first read) -> as the first statement of the
   do body `limit = ((s32 *) &gVisibleCount)[-3];`, `while (temp < limit)`: 215 -> 60. `gTotal` is 12 bytes below
   the volatile counter the function reads at its top; cse's related-value lookup (`find_best_addr`) keeps `-12(reg)`
   (cheaper than the two-insn symbolic address), loop.c moves a first-insn load (`maybe_never` still clear), cse2
   leaves the register form. `count[-3]` through the pointer, `(&sym)[-3]` in the condition, a read before the
   loop and a read after the first `if` all fold to `lui`+`lw`, keep the load in the loop, or copy the first read.
6. `idx = index;` as the body's first statement and the compare on `idx`: 60 -> 0 (the sign-extension movable is
   discovered before the bound's load, so the preheader has `move $a0,$s0` before `lw $t0,-0xc($t0)`; hypothesis: loop.c
   emits movables in discovery order).
Library families (decomp-repair): `narrow_copy_web` (1), `postincrement_after_test` (2), `sibling_symbol_offset_read`
(5), `loop_body_key_copy` (6); `local_web_split` already did 3. From the saved best `prove_chain` now reaches
655 -> 465 -> 360 -> 350 -> 330 (the scaffolding still blocks the rest); from the cleaned 215 source the two last
families reach 60 -> 0.

Failed experiments that would otherwise be repeated: `volatile` limit read at the loop top (the read stays in the loop),
`temp` kind types and casts (`(u8) kind`, `kind & 0xFF` give an `andi`), a dead `temp = 0;` at the function's end
(cse canonicalises the other way and merges both), `-fno-rerun-cse-after-loop` and `-fno-cse-*` flags (every other
row moves), a pre-test `for` loop (an extra 8 bytes of frame).

What is controlled evidence and what is one function: the match is one function. The cse mechanics in 1 and 5 were
read in the compiler's source and reproduced on toy functions (the dumps show the HI-mode copy and the
`(plus reg -12)` address); the combine and loop.c halves are hypotheses.
