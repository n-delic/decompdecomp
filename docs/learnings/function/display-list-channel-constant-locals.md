---
title: A vertex colour literal in a register no allocator gives it - one local per channel set at the entry
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-26
families: constant_channel_locals
---

What the function (or case) looked like and the exact compiler and flags:

A 262-instruction SN64 GCC 2.8.1 display-list builder of an N64 game (profile above), saved at 470 after many agents'
sessions (docs/learnings/finding/sn281-constant-that-is-not-a-birth.md,
docs/learnings/experiment/sn281-constant-placement-session-two.md). After its last calls it fills four vertices; their
sixteen colour bytes are 0xFF. The target loads the 0xFF once (`li $t6, 0xFF`) after every other value of the block,
and `$t6` is the register reload uses for the function's `mflo` copies (insn `(set (reg:SI 14 t6) (reg:SI 65 lo))` in
the greg dump: no pseudo is allocated to `$t6`). Ours had the literal as one block-local pseudo with 17 references,
which local-alloc coloured first (`$v1`), so every other value of the block was one register round: 50 register rows.

The finding: in the target the colour pseudo got no hard register at all, and reload rematerialised its REG_EQUIV
constant at each store into the spill register, with inheritance keeping one `li`. A block-local pseudo with 17
references always gets a register; the shape that gets none is a global pseudo live across calls whose priority
(floor_log2 (refs) * refs / live length, global.c) is below every saved-register candidate, so the call-saved
registers are all taken when its turn comes, and caller-save is not profitable (`4 * calls >= refs`, regs.h). A
single colour local set at the top has 17 references and outranks the image pointer (it took `$s5` and spilled the
parameter: 1397). One local per channel (`r = 0xFF; g = 0xFF; b = 0xFF; a = 0xFF;` at the entry, each stored four
times) has five references each over the whole function: lowest priority, no register (the four pseudos are absent
from global.c's dispositions in the `-dl -dg` dumps).

The source change, and the measured result before and after (scores, the judge used):

Scores are `fast_judge.py` (the app's judge), then confirmed by `compile_and_diff` (matched).

| Edit | Score |
| --- | --- |
| saved best: literal 0xFF in each of the 16 stores, stores in a hand-tuned interleaved order | 470 |
| one local per channel, set at the entry after `size = 600;`, read by the stores | 82 |
| the same, with each vertex written field by field (`ob[0..2]`, `cn[0..3]`, `tc[0..1]`) | 0 |
| (also exact) field order `ob`, `tc`, `cn` | 0 |

The interleaved order was the earlier searches' answer to the old allocation; once the colour no longer takes a
register, the plain order is the target's. decomp-repair `constant_channel_locals` (0.6.87) offers the exact child from
the 470 saved best in one step.

Failed experiments that would otherwise be repeated:

- One colour local set once or twice, at any of twelve places from the entry to between the vertices, with and without
  passing it to the earlier `func(0, 0, 0, 0xFF)` call, `s32` or `u8`: 740 at best (set after the loop: sched1 moves the
  `li` beside its first store; set before the loop: a saved register, 1397-1907).
- One local per vertex (`c0..c3`) at the entry, or one local per channel set after the loop or at the vertex block:
  740 or worse (why the per-vertex locals, also five references each, keep a register was not dumped).
- The channel locals with the vertices' fields in the orders `cn, ob, tc` / `cn, tc, ob` / `tc, cn, ob` / `tc, ob, cn`:
  2855 / 2855 / 2220 / 526.
- No compiler profile helps the saved best: every installed SN, KMC and gcc-2.8.1 profile at `-O1/-O2/-O3`,
  `-mips2/-mips3`, `-G0/-G8`, `-mfp32/-mfp64` scored 470 or worse.

What is controlled evidence and what is one function:

One function. The allocation facts (no disposition for the four pseudos, `$t6` as the reload register) are read from
its dumps; the global.c, local-alloc.c, reload1.c and regs.h rules are read in gcc-2.8.1's source. That the same shape
explains other SN 2.8.1 constants the target holds in a reload register is a hypothesis: it needs calls between the
entry and the uses (a leaf function has no such path).
