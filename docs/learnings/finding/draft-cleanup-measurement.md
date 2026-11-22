---
title: which source cleanups improve m2c's first draft, measured against matched functions
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (an N64 game's own profiles)
evidence: 90 matched functions of one N64 game
date: 2026-11-22
---

Sample: 90 matched functions of an N64 game with 1,436 matched functions that kept m2c's stored draft
(`functions.seed`), stratified by size (8 to 325 instructions). 80 raw drafts compiled. Each cleanup is a
source-to-source rewrite from decomp-repair applied to the raw draft, scored by the app's judge (`fast_judge`, isolated
worker, read-only), plus a token edit distance to the matched source (lower is closer). Δ score < 0 is better.
"first" applies the cleanup's first variant (what a deterministic stage would do); "best of 3" its best of the first three.

| cleanup (families) | applies | compiles | mean Δ first | median | better / worse | mean Δ best of 3 | better / worse | Δ distance |
|---|---|---|---|---|---|---|---|---|
| (a) offset → counter index (`pointer_walk_index`, `counter_derived_offset`) | 0/90 | – | – | – | – | – | – | – |
| (b) raw access → record view (`field_struct_view`, `raw_address`, `record_slot`) | 33 | 30 | **-133** | 0 | **6 / 1** | -151 | 7 / 1 | +0.2pp |
| (c) shared temp → per-arm (`reused_temp_split`, `switch_variable_split`) | 0 | – | – | – | – | – | – | – |
| (d) masks → narrow widths (`param_mask`, `parameter_mask_drop`) | 2 | 2 | 0 | 0 | 0 / 0 | -38 | 1 / 0 | +1.7pp |
| (e) call arity (`target_call_arity`, `call_site_prototype`) | 8 | 6 | **+384** | +334 | 0 / **6** | +321 | 0 / 6 | +1.8pp |
| (f) if-tree → switch (`if_tree_switch`, `compare_tree_switch`, `range_case_switch`) | 0 | – | – | – | – | – | – | – |
| (g) volatile spill → plain local (`volatile_local_drop`, `volatile_slot_demotion`) | 0 | – | – | – | – | – | – | – |
| (h) goto loops ↔ for/while (`goto_loop`, `loop_goto_form`) | 9 | 7 | +128 | 0 | 2 / 2 | +92 | 3 / 1 | +1.7pp |
| (i) display-list stores → gbi macros (`gbi_macro_commands`) | 2 | 1 | -75 | -75 | 1 / 0 | -435 | 1 / 0 | +5.3pp |
| (j) phase-storage union (`phase_union`) | 7 | 5 | **+313** | +15 | 1 / **3** | +7 | 2 / 2 | +1.1pp |
| m2cfix `refine_draft` (already a draft candidate) | 61 | 55 | +68 | 0 | 10 / 10 | – | – | +2.2pp |

Combinations (first variant of each, in order):

| chain | applies | compiles | mean Δ | median | better / worse | Δ distance |
|---|---|---|---|---|---|---|
| b → i | 33 | 30 | **-138** | 0 | **6 / 1** | +0.6pp |
| b → i → d | 33 | 30 | -138 | 0 | 6 / 1 | +0.7pp |
| b → i → h | 36 | 32 | -104 | 0 | 8 / 2 | +0.9pp |

Findings:
- Most library families are shaped for agent-written source and never fire on raw m2c output (a, c, f, g: 0/90).
  Cleanups for those shapes on m2c's own spelling (`M2C_FIELD`, `var_`/`temp_` names, `goto block_N`) are the gap.
- The record view (b) is the one broad win: a third of drafts, 6 better and 1 worse. gbi macros (i) are rare on this
  sample but never hurt. Both are now the draft cleanup stage (`app/draft_cleanup.py`, project setting `draft_cleanup`).
- Call arity (e) always hurt a raw draft: m2c's extra arguments carry register moves the target has. The phase-storage
  union (j) hurt on average as a first variant (as on a function where its pseudo outranked a record pointer); it is
  not part of the initial draft stage and stays a permuter candidate only.
- The token distance to the final match barely moves for any cleanup (the match's names and types dominate it), so it
  does not rank cleanups; the judge's score does.
- m2cfix's refine is a coin flip on this game (10/10); it stays as it was, a candidate judged beside the raw draft.

Method limits: one game, one compiler; first-variant only for the stage. The raw draft is always judged first and stays
the stored seed, so a worse cleaned copy costs one compile and never replaces a best.
