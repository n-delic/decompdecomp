---
title: a busy-wait loop whose volatile global was reached through a pointer and *(&G)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

A 13-instruction N64 delay routine: store zero to a global, jump to the test, then load, add one and store, and load
again for the test. Every delay slot is empty and one `%hi` register serves all the accesses.

Source change, measured with the app's judge: the saved source (`s32 *p; p = &G;`, `(*p) = 0; while ((*(&G)) < n)
(*p) += 1;` with a plain `extern s32 G;`) scored 715. Written as `G` with `extern volatile s32 G;` it is exact (0):
a `for` or a `while` loop both do. Folding the spellings without `volatile` scored 510.

From the untouched machine draft (a `goto` loop, 1220) the library already reached it: `goto_backward_loop` (510), then
`scalar_reload_volatile` (0). From the respelled saved source it did not: `scalar_reload_volatile` refuses a global
whose address is taken. decomp-repair `aliased_global_volatile` offers the folded spellings and the qualifier as one
child (715 to 0 in one step).

One function; not a controlled regression beyond the library's source-shape tests.
