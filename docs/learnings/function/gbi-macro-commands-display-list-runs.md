---
title: m2c's raw display-list stores written back as libultra Gbi macros (gbi_macro_commands)
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

N64 display-list builders in one game (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`). m2c prints a run of the SDK's
`gDP*`/`gSP*` macros as one read of the cursor global, the words stored at byte offsets in the scheduler's order, the
values computed between them, and then every `cursor = temp + 8n` in a row. It also prints the advances with the
local's declared `s32 *` type while counting in bytes, so a draft that compiles has the wrong pointer arithmetic. In a
565-instruction text renderer, m2c moves a texture rectangle's first word (computed in both arms of the hi-res `if`)
past the join.

The source change, and the measured result before and after (scores, the judge used):

decomp-repair `gbi_macro_commands` (0.5.620) decodes each command by its opcode against `gbi.h`'s encodings and
writes the run as macros on `cursor++` (`gbi_macro_commands`), on a block-local `Gfx *` (`gbi_macro_local`), as raw
words on the `Gfx` record (`gbi_raw_commands`), or one run at a time (`gbi_macro_run`, `gbi_macro_local_run`).
Scores from the app's judge (`fast_judge.py`, re-scored today, so they differ from the scores the database recorded):

| function (twine) | state | before | best variant |
|---|---|---|---|
| a 653-instruction display-list function (open) | saved best | 14912 | 13862 (`gbi_macro_run`, one run) |
| a 565-instruction text renderer | m2c draft as the autopilot compiled it | 27240 | 26315 (`gbi_macro_run`); all runs 28545 |
| a 343-instruction display-list function (open) | saved best | 2480 | 4165 (every variant worse) |
| a 664-instruction display-list function (open) | saved best | 14083 | 14083 (no variant better) |
| four matched display-list functions | matched source | 0 | 0 for some single runs, 150-1315 for the rest |

On the matched functions, the hand matches write some commands as a pointer read, an advance and then the words, with
the second word stored raw (`*(s32 *)((char *)p + 4)`), not as a struct member. Turning those into macros costs
150-1315 points. This fits the alias-class finding from `member_alias_pair`: a struct-member store and a raw store
schedule differently. So in this game not every command was written with the macros, and the family has to stay a
candidate for the judge to accept or reject, not a rewrite that is always applied.

The 21920 -> 3770 step recorded for the text renderer ("rewritten as the SDK's own macros") was a hand rewrite of the
whole function at once (types, structs, `ENT` expressions, macro arguments spelled the way the original wrote them).
The family's mechanical version of the macro step alone gives 27240 -> 26315 on the m2c draft. The rest of that step
came from the argument expressions and the record types, which the family does not recover.

Failed experiments that would otherwise be repeated:

- Writing every run of the text renderer's draft as macros at once: 28545 (worse than one run alone). The local-`Gfx *`
  form was worse everywhere (35000+ for all runs).
- Macros and raw words on the same record scored the same in every case measured (the `_SHIFTL` masks fold to the
  same code). The score changes come from the cursor's read/advance shape and from moving the stores, not from the
  macro spelling.

What is controlled evidence and what is one function:

These are observations on one game: no controlled undo of a known match was run. Every variant writes the same words
to the same places as the draft. A host-compiled test in decomp-repair checks this for each variant. Hypothesis, not
tested: the bigger gains need the macro arguments as the original wrote them (`ENT.w + 1` rather than m2c's
temporaries computed before the run), and folding m2c's `if (lrs >= 0x800) lrs = 0x7FF` clamp back into
`gDPLoadBlock`'s `MIN`. Neither is implemented.
