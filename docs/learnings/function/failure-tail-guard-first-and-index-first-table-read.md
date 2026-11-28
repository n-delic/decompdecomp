---
title: failure tail as the guard before the work, and a table read with its index first
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

A 256-instruction N64 display-list loader (loads two textures and returns the advanced command pointer; a flag
tells the caller whether it did). The function sat under the project's KMC GCC setting, its saved best the machine
draft after 39 attempts, while the matched functions around it use SN GCC 2.8.1.

Measured with the app's judge:

- The machine draft's saved best: 20089 (KMC).
- A clean rewrite with gbi-style macros on `gfx++`: 16291 under KMC, 1195 under SN. KMC's score gave no hint that
  the source was nearly right: judge a clean source under the neighbours' profile before tuning it.
- `if (slot < n) { work; return gfx; } *loaded = 0; return gfx;` with an earlier guard doing the same two things:
  the two failure blocks were merged (1195). Written `if (slot >= n) { *loaded = 0; return gfx; } work; return gfx;`:
  220. The jump2 dump shows both blocks still apart until that last pass; that jump.c's block move before it is what
  decides is a hypothesis. decomp-repair `tail_exit_guard_first`.
- `T[slot]` put the table's `lui` above an earlier store, in the other register. `*(u32 *)((slot << 2) + (int)T)`:
  0. decomp-repair already had it (`element_index_first`).

Failed, each 1195 or worse: a `goto` to a failure label, an `else` arm that returns, nesting the work in the `else`
of the first guard (identical code), and one shared `return` (5059). Failed on the table read, each 220: `*(T + i)`,
`void *T[]`, a `char` table cast, a one-member struct array, `i * 4 + (u32)T`, and the slot as `u32` or `s32`.
