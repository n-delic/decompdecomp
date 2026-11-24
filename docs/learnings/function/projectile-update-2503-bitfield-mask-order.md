---
title: projectile update 2503 bitfield mask order
kind: function
compiler: KMC GCC 2.7.2 -O2 -mips3 -mgp32 -mfp32 -G0
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:
A 2,503-instruction projectile update (KMC GCC 2.7.2, `-O2 -mips3 -mgp32 -mfp32 -G0`) at 5 with one register row: a
flag word cleared with `G->58 &= -0x1C1` (bits 6..8). Target `li $v0, -0x1c1; ...; and $v0, $v0, $a3` (mask
register first); ours `and $v0, $a3, $v0`. Continues projectile-update-2503-cross-jump-and-sched1.

The source change, and the measured result before and after (scores, the judge used):
- The word is a bit-field word: the same function updates another object's word at 0x58 as
  `(v & 0xFFFF0003) | ((((v >> 2) & 0x3FFF) | 0x100) * 4)`, a 14-bit field at bits 2..15. Declared
  `struct { u32 hi:16; u32 f:14; u32 lo:2; }` and cleared `->f &= ~0x70;`: 5 -> 0 (fast_judge, then the app's
  compile_and_diff: matched, recorded best 0).
- Why (hypothesis from output, not traced in combine): on the word, cse's fold_rtx (cse.c ~5284) swaps a known
  constant second. A compound `&=` on a bit-field wider than the cleared bits expands to extract, and, insert
  (store_fixed_bit_field, expmed.c); combine folds that into one word `and` with the mask register first, and cse
  never had the constant beside the word.
- Toys (one cleared run, bits 6..8): fields (lo, width) (0, 9), (0, 10), (0, 12), (2, 14), (4, 8), (5, 4), (6, 4),
  (6, 10) give mask-first; the exact field (6, 3), the whole word (0, 32), a field reaching bit 31 (6, 26) and a
  halfword-aligned (0, 16) field (`andi` on a halfword) do not.
- decomp-repair `bitfield_member_clear` offers one child per layout; all three it offers here score 0, from the plain
  `&= -0x1C1` and from mask_local_twice_set's staged form (whose sched1 effect the bit-field also gives).

Failed experiments that would otherwise be repeated:
- `f = 0` / `f &= 0` on the exact 3-bit field (bits 6..8): the canonical (and F M), like the word.
- a function-scope mask variable `mask & G->58`: cse no longer knows it (mask first), but the spilled REG_EQUIV
  pseudo is rematerialised by reload into `$t0` after the loads: 280. As `s16`: the copy survives but needs sll/sra.
- `F ^ (F & 0x1C0)`, `(F | 0x1C0) ^ 0x1C0`, `F - (F & 0x1C0)`, `~((u32)0x1C0)`, `~(m << 6)`: not an `and`, or mask second.

What is controlled evidence and what is one function:
The layout table is controlled (toys); the match is one function. The `block_tail: do { goto block_263; } while (0);`
scaffolding from the earlier entry remains in the matched source.
