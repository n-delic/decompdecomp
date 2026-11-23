---
title: Four float-to-byte clamps matched with u8 result locals, a goto into the shared cap and a two-insn constant arm
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

The 172-instruction N64 sound-voice starter from docs/learnings/experiment/float-byte-clamp-copy-before-branch.md
(KMC gcc 2.7.2, -O2): four `(u32)(x * k)` conversions, three clamped to 0x7F and passed as bytes, one clamped to 0x3FFF
and passed as a halfword. Each clamp's value sits in a register other than the argument's (`move $a1, $v1` in the
branch's delay slot). The first clamp is split over a flag test: the flagged arm branches into the other arm's
`li $a1, 0x7f`. The 0x3FFF clamp puts its result in the flag's register (`bnez $v0; move $v0, $a1; li $v0, 0x3fff`).

The source change, and the measured result before and after (scores, the judge used):

From the saved best (449: a shared result local, calls in the arms, an identical-arm guard and a frame 0x48 too big), a
rewrite of the clamps, each step scored with the app's isolated judge (fast_judge) and the last confirmed by the app:

| step | score |
| --- | --- |
| per-clamp values, `r = v; if (v >= 0x80) r = 0x7F;` (cse swaps the copy into the value's setter) | 2030 |
| one value variable for clamps 1-3, written `v = (u32)(x); v &= 0xFF;` (the fix's `$v0` clobber keeps it out of `$v0`), u8 result locals `b2 = v; if (v >= 0x80) b2 = 0x7F;` | 1195 |
| u16 result for the last clamp, the flagged arm `if (v < 0x80) b1 = v; else b1 = 0x7F;` | 600 |
| clamp 1 as `b1 = v; if (v >= 0x80) goto cap;` in the flagged arm and `if (w < 0x80) b1 = w; else { cap: b1 = 0x7F; }` in the other | 15 |
| the last clamp's constant arm `if (w >= 0x4000) r = w = 0x3FFF; else r = w;` | 0 (app: matched) |

What decided it (read from the compiler source and the dumps; hypotheses where marked):

- A u8 (QImode) result local makes the copy `(set (reg:QI b) (subreg:QI v))`: cse's "set REG0 REG1" swap and
  local-alloc's `optimize_reg_copy_1` both need a REG source, so the value stays live past the copy into the compare,
  conflicts with the result and gets its own register. combine then sees that every set of `b` fits a byte and passes
  it with no `andi`. An SImode result with the same statements was always merged into `$a1`.
- One value variable for the three byte clamps is global (several blocks), so local-alloc never ties it to the
  `sltiu` flag; it is also the fix's destination, which clobbers `$v0`, so it lands in `$v1`.
- jump.c's "if (...) x = a; else x = b" hoist needs no other entry to the else insn and a one-insn then arm. The goto
  gives clamp 1's `cap` store a second entry (and jump2's cross-jump is not needed); `r = w = 0x3FFF` makes the last
  clamp's then arm two insns, so neither the jump before cse nor the one after it hoists, and flow deletes the dead
  `w` store. decomp-repair `clamp_then_chain` (PR 1407) writes the last one from the 15 state.

Failed experiments that would otherwise be repeated:

- `do { } while (0)` fences around the copy or the test (fix the layout of clamps 2-3 only when the value variable is
  shared and outlives the results; the register pair then swaps through the value's copy preference).
- Calls in the arms of the last clamp (510-815), `w & 0xFFFF` in the else arm (fold makes it a subreg again), an unused
  label in either arm (deleted before the hoist test), `?:` forms (folded or hoisted the same way).

What is controlled evidence and what is one function:

One function. The library family has unit tests on a synthetic clamp; the other three steps are not yet library
families (the u8 result local for a clamp, the shared masked value, the goto into the other arm's cap).
