---
title: draft candidates from a look-alike's C and from m2c's byte steps start closer; a re-typed result and merged triples do not
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (an N64 game's own profiles)
evidence: observation
date: 2026-11-22
---

Four ideas from one 108-instruction N64 function (func_8005C24C; walked-table-index.md: a byte-cursor table walk, its
shared offset local and a `void` tail that had to return the callee's result, each edit worse alone and exact
together) tried as ways to start every draft closer. Method as draft-cleanup-measurement.md: the stored machine draft
of each matched function (`functions.seed`, read-only), every candidate scored by `decomp judge-worker` (isolated,
attach-only; no app try, no saved match touched, no permuter), Δ = candidate − raw draft, both compiled. The fixed
sample is that entry's 300 functions plus func_8005C24C; where a rewrite fires rarely, every draft of the game it
fires on was judged too ("game"). In production a candidate is judged beside the raw draft and the better is kept, so
a worse candidate costs one compile.

## 1. The result type the exits suggest (`draft_return_type`): off

decomprepair `mips_return_paths.contract(asm)` reads the return-register paths: 'value' (a `$v0` write nothing reads
reaches an exit), 'tested' (every exit's `$v0` is a callee's result or a value only branches read), 'void' (no path
sets `$v0`). Against the 1,689 matched functions' own C: 'value' was right on 474 of 475, 'void' on 49 of 49. But
m2c already gets those right: of 1,061 stored drafts whose type could be read, 14 had the wrong type (13 `void` that
return a value, 1 the reverse), and neither 'value' nor 'void' fires on any of them. Ten of the 13 are in 'tested',
which is also what 555 void functions look like: KMC GCC copies a flag into `$v0` to test it in void functions too
(func_8005C1D0 matched as `void` with the same tail instructions as func_8005C24C). Callers that read `$v0` after the
call (`caller_use`) do not separate them either (1 right, 6 wrong).

Candidate: m2c run again with the function declared `s32` (or `void`) in its context, which makes m2c write the
`return`s itself. On the sample it fires on 32 drafts ('tested' 31, 'value' 1); against the same m2c run without the
declaration (the control) 0 better, 10 worse, 8 same (func_8005C24C: 2986 -> 3580; walked-table-index measured the
tail alone as worse too). Shipped as `app/draft_return_type.py` and `Arch.return_contract`, off (project setting
`draft_return_type`).

## 2. A close matched look-alike's C as a draft (`twin_drafts`): on

For a function whose instructions are at least 90 % those of a matched function in the same game (the similarity
search `find_similar` uses, sizes within 75 %), that function's matched C with this function's names (from the
relocations both fill at aligned instructions, `twins.rom_renames`; refused when one name would mean two things) and
the immediates that differ substituted where unambiguous (`similar_matches.literal_variants`), at most two donors,
two candidates each.

| sample (301) | value |
|---|---|
| functions with a matched look-alike ≥ 90 % | 58 |
| a transplant offered (names mapped) | 44 |
| compiled, raw draft compiled and unmatched | 42 |
| best transplant better / worse than the raw draft | **36 / 6** |
| become exact from the transplant alone | **14** |
| starting-score change, summed | -48,965 |
| extra compiles | 69 |

Caveat: the donors are matched now; at draft time a look-alike may not be matched yet, so this measures what the
stage does when one is (it then also overlaps the similar-code reuse pass, which tries the same transplant later in a
separate job). On by default (project setting `twin_drafts`); ROM mode, MIPS. Project mode: ROADMAP.

## 3. Table cursors as the counter's index, and m2c's byte steps: on

m2c writes a cursor's step in bytes whatever its type: `s32 *var_v1; ... var_v1 += 4;` for a 4-byte `addiu`, which as
C steps 16 bytes (the drafts are compiled as written). decomprepair `pointer_byte_step` (`pointer_scale_repairs`,
already a permuter family) rewrites the step when the target's immediate says bytes; the new `table_cursor_index`
writes each use of a cursor that steps beside a counter as the table at the counter's index (`*(&G + i)`, or in bytes
`*((T *)((u8 *)&G + i * K))`), reading the step as bytes only on the same evidence. Game-wide (1,578 drafts):

| candidate (first child, alone on the raw draft) | fires | compiled | better / worse / same | exact | Σ Δ (gains) |
|---|---|---|---|---|---|
| `pointer_byte_step` | 122 | 94 | **84 / 1 / 9** | 3 | -12,465 |
| `table_cursor_index` | 62 | 34 | 19 / 11 / 4 | 3 | -10,426 |

The stage now chains record view → gbi macros → byte steps into the one cleaned copy, and offers that copy with its
table cursors as indexes as a second candidate (`draft_cleanup_index`). Old stage against new, the better of the raw
draft and the stage's candidates as the starting best:

| | old stage | new stage |
|---|---|---|
| sample: drafts the stage touches | 150 | 169 |
| sample: start better than the raw draft / exact | 69 / 4 | **81 / 5** |
| game: drafts touched (of 1,578) | 150 (9.5 %) | 256 (16.2 %) |
| game: start better / exact | 69 / 4 | **142 / 8** |
| game: starting-score change, summed | -35,886 | **-54,766** |
| game: candidate compiles | 150 | 290 |

New against old, draft by draft: 82 start better, none worse. Rewriting costs 1.0 ms a draft (0.9 before). Newly
exact from the stage alone: func_80008258, func_8000A6F4, func_80048734, func_80073A00 (150-230 -> 0, by the byte step
or the index). On by default (the existing `draft_cleanup` setting).

## 4. Merging a stalled step's best children in pairs and triples (prove_chain --combine): off

`prove_chain.py --combine K` merges the K best children of a step where no child improves, in pairs then triples whose
pairs merged (token edits that do not touch each other; at most C(K,2) + C(K,3)). From the saved bests before their
matches, with the family that now does the whole fix excluded (so the question is whether merging finds it):

| function (pre-match best) | combine 0 | combine 10 | merges judged |
|---|---|---|---|
| func_8005C24C (100), without `walked_table_index` | 100 | 100 | 120 |
| func_8002424C (30), without `arm_switch_volatile_chain` | 30 | 30 | 131 |
| func_8003C94C (25) | 25 | 25 | 87 |

None: the edits these needed are each much worse alone (func_8005C24C: 720 to 2287 from 100), so they rank far below
the ten best of ~250 children, most of which leave the score where it was; and no other family offers them apart.
Off by default; the skill says to narrow `--families` before using it.

Not measured: other games and compilers; whether starting closer leads to more matches later.
