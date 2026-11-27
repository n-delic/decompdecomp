---
title: a halfword counter read through its neighbour word plus 2 is its own global
kind: function
compiler: SN GCC 2.7.2 -O1 (sn-n64-272-0001, -G0 -mips3 -mhard-float)
evidence: verified
date: 2026-11-27
---

What the function looked like: a 70-instruction N64 per-frame step. The saved C read and stored a halfword counter
as `(*(s16 *)((s8 *)&D_800B84B8 + 2))` (m2c saw only the neighbouring word symbol and a constant), three uses (a test, a
clear and `x = (s16)((u16)x + 1)`), 505.

The source change, and the measured result: the same three uses (and the fourth) written as the global the game declared
at that address, `extern s16 D_800B84BA;` and `D_800B84BA = (s16)((u16)D_800B84BA + 1);`: 505 -> 0 with the app's judge
(`fast_judge.py`). The symbol plus constant is one cse value class that the base's register serves for the read and the
store (`2($v0)`); the symbol itself is re-materialised for each access (`lui; lh %lo(sym)`, `lui; sh`), as the target does.
`global_read_via_base` is the opposite direction (own symbol to base plus offset). Now `global_offset_symbol`
(decomp-repair), found by `prove_chain.py --families global_offset_symbol` as one child from the 505 source.

Failed experiments that would otherwise be repeated: the earlier 555 -> 505 step of the same function was a callee's
missing pointer argument (`func_80051B60(&D_800B84F0)`), not this family; the saved 555 text also had a mangled
`&(*(s32 *)0x800b84ba)` spelling that scored 555 and that no family repaired (the plain-offset spelling of it scores 760).
`prove_chain --families all` from the 505 source stalled at 300 through `expression_or_near_repair`, which is not a match.

Controlled evidence and what is one function: one function, one compiler pair measured on SN (the saved profile was
changed to SN after the match; the draft ran under KMC). Whether the symbol exists at that address in the game's own
symbol table is not read by the family: it needs only the `D_xxxxxxxx` naming.
