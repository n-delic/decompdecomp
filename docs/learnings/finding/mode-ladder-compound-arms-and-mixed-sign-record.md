---
title: mode ladder compound arms and a member read in both signednesses
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-28
---

A 267-instruction N64 spawner (three allocations, two `if (mode == 0/1/2)` ladders that advance a copied angle),
judged by the app. Plain typed C matched after statement order only; from the m2c draft (5925) these changes were needed:

- Merge the second and third allocation results into one local (they share a saved register in the target).
- Move single stores until they follow the target's order. The last one: the angle copy written before the three
  constant scale stores, not after them.
- Both ladders as `place += K;` per arm instead of m2c's gotos into a shared store: from the otherwise exact source,
  905 -> 0. Each part alone: nested `!=` form 640, `place = t + K` 1110, the constant through a scratch local 955.
  decomp-repair `shared_store_ladder` makes them together.
- One record per pointer. Three halfword offsets stored as `s16` and read as `u16` stayed raw `M2C_FIELD`
  (best `struct_retype_raw_offsets` child 270); as members with one type they gave 0. The raw scalar stores may
  alias the mode global, which held its load in place. decomp-repair `struct_retype_raw_offsets` now offers that.

Failed experiments: `do { } while (0)` around the angle copy (1595) or around the copy and the scale stores (1385);
`goto_paths_written_out` alone on the ladders (2050); `prove_chain --families all --max-judged 45` from the draft
reached only 5670.

Controlled: the part-by-part scores above. One function: the statement orders.
