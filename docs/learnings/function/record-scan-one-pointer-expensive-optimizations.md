---
title: a 55-instruction record scan needs one record pointer, and -fno-expensive-optimizations keeps the (u16) sentinel andi
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (-fno-expensive-optimizations)
evidence: verified
date: 2026-11-23
---

A 55-instruction N64 record scan (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, saved best 165): a table lookup by
slot pointer, then a walk over 0x1c-byte records ended by a 0xffff halfword, calling a helper on the record whose
key matches and bumping its count. m2c's shape had a second pointer (`mark = base + 2`) stepped beside the base, a
match arm that moved the base (`base += 8`), a backward `goto`, and a `u32` key loaded with `*(u16 *)`.

Three source steps (decomprepair `pointer_pair_loop`, 165 -> 100 on the app's judge) and one flag (100 -> 0):

1. the `goto` as `do { if (key == want) { ...; break; } base += 0x1c; key = *(u16 *)base; } while (key != 0xffff);`:
   loop.c then hoists the sentinel constant out of the loop (`ori $v0,$zero,0xffff` once) where the goto form reloads
   it every pass;
2. one record pointer: every field read or write is `base + k` and the arm's `base += 8` is sunk into its uses
   (`call(&sp, base + 8, ...)`), so the loop pass derives the target's single stepped register `$s0 = base + 2` itself
   (the explicit second pointer plus an arm that moved the base made it derive another one: `addiu $t0,$a1,8` and
   `lw -4($t0)` in place of `lw 2($s0)`);
3. the offset product written `i * 12` (the `(i * 2 + i) * 4` spelling gave `addu $v1` / `sll $v1,$v1,2` where the
   target has `addu $v0,$v0,$a2` / `sll $v1,$v0,2`, a register choice under the flag below).

The one row left at 100 (and 105 with a `u32` key) was `andi $v1,$a3,0xffff` before the first sentinel compare.
Every spelling folded it away (cast, `& 0xffff`, a `u16` temporary, a `u16` local, a second variable, `volatile`,
bit-fields, a copy shared with the table count, a `do { } while (0)` fence), and a `u16` local brought the `andi`
back in the first test but added a second one at the loop head, which the target does not have. Adding
`-fno-expensive-optimizations` to the function's flags kept the first `andi` with a `u32` key and the `(u16)` cast
(115 -> 10 -> 0 with `i * 12`); `try_compiler` says it matches byte for byte. A flag scan over the 38 options of this
compiler found nothing else that changed the sentinel test. Hypothesis, not confirmed in the compiler's source: combine's
`nonzero_bits` path that drops `(zero_extend:SI (subreg:HI x))` is behind `flag_expensive_optimizations`.

Failed: any `u16` key or `u16` parameter (the extra `andi $a3,$a3,0xffff` at the loop head), a rotated `for` with the key
assigned in its condition (the first test shows the `andi` with a `u16` key, still the loop-head one),
`-fno-strength-reduce` (removes the derived pointer's giv but leaves the first test folded), moving the table index
into the loop body, other toolchains (`gcc-2.8.1-pm` 660, the SN builds 290 and worse).

Controlled: no. One function; the whole set is one child of `pointer_pair_loop` (decomp-repair) and the flag is a
`search_flags` variant and a `kmc_expensive_probe` lead (a target `andi ...,0xffff|0xff` the candidate lacks).
