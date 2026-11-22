---
title: a run of byte stores keeps one lui/addiu base register only when cse cannot see the base as a constant
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: verified
date: 2026-11-22
---

A 46-instruction state-reset function: nine byte stores at `sym-3 .. sym+5` (`lui $v0` + `addiu $v0,$v0,%lo(sym)` once,
then `sb $zero,-1($v0)` ... `sb $v1,5($v0)`), a dozen single-use byte globals stored through `lui $at` / `sb`, and
two float zeros through `mtc1 $zero,$f0` / `swc1`. KMC GCC 2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`.

The rule: cse folds every plain spelling (`M2C_FIELD(&sym, s8 *, k)`, `((u8 *)&sym)[k]`, a pointer local set to `&sym`)
back to `sym + k`, because `mips_address_cost` makes `sym + k` as cheap as `reg + k`, so the base register dies and the nine stores
become nine `lui $at` / `sb` pairs (4400). Only a base cse cannot see as a constant keeps the register. A term that is
zero but that only the combiner can prove zero does it: `u8 *p = &sym + (byte_global >> 8);` (a byte load is a
zero_extend, cse does not track nonzero bits, combine's nonzero_bits does). 4400 -> 0 with the stores written `p[k]`,
the other globals as `u8`, and the float zero in a local (`f32 z = 0.0f`).

Failed experiments that would otherwise be repeated:
- `(&sym + n) - n` over a word global (what the Deep Permuter's address probe found, 220 with a branch guard, 260 plain):
  keeps the register but leaves an 8-byte frame. The load is used twice, combine does not lower the pseudo's use count,
  and reload gives the dead pseudo a stack slot. The shift uses the byte once, so combine zeroes the count.
- `xor n ^ n`, `u32` casts, a `const` carrier, a pointer carrier: the same frame.
- An uninitialised local in place of the load folds (cse sees `t - t`); a parameter does too.
- `register`, `volatile u8 *`, a pointer to a volatile symbol, a label and `goto`, a `u8 (*)[]` array symbol, a struct
  symbol: all fold. A `-f` flag does not unfold it either (`-fno-cse-skip-blocks`, `-fno-cse-follow-jumps`, ...).
- A use of the address as a value (`Q = p`) keeps only the k = 0 store on the register.

Controlled evidence: `prove_chain.py` from the m2c draft, 5350 -> `opaque_symbol_base` 1115 -> `field_width` 500 ->
`float_zero_local` 0. One function; the carrier can be any byte global, its load is deleted. The family is decomp-repair
`opaque_symbol_base` (gated on the target showing the `addiu $r,$r,%lo(sym)` base with three or more byte stores).
Scaffolding: the match relies on the `(byte >> 8)` term; no developer wrote it.
