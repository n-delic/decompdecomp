---
title: a caller that reads $v0 or $f0 after the call proves the function returns a value; callers decide the draft's result type where the exits cannot
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (an N64 game's own profiles)
evidence: observation
date: 2026-11-22
---

draft-start-candidates.md (section 1) found a function's own exits cannot say whether it returns a value ('tested':
KMC GCC copies a flag into `$v0` in void functions too) and that a first look at callers (`caller_use`) did not
separate them either (1 right, 6 wrong). That look read only up to eight callers' assembly. This one reads every
direct caller (the stored callees inverted, `RomNetwork.xrefs`), three witnesses together, and decides only what each
can prove (`app/call_evidence.py`, MIPS reader `arch/mips_callsites.py`, tool `get_callers`).

- The callee's result register after the `jal` (the delay slot runs before the callee, so the scan starts one
  instruction later): `$v0` (`$v1` too: 64 bits) or `$f0` read before any write or later call: 'value' (a float when
  it is `$f0`). Written or clobbered first: 'discarded'. The caller returns with it untouched, or a branch comes
  first: no evidence.
- A matched caller's own C: `x = g();`, a tested or returned call, an argument: value; a bare `g();`: dropped.
- The shared header's declaration of the function, and a matched look-alike (>= 90 %) in the game.

Measured on all 1,692 matched functions of an N64 game with a readable own type (1,105 `void`), judged against the
type of each one's matched C (read-only database; no judge needed for this part):

| rule | fires | right | wrong |
|---|---|---|---|
| a caller reads `$v0`/`$f0` after the call (asm) | 453 | 437 | 16 |
| a matched caller's C uses the value | 408 | 377 | 31 |
| either | 480 | 449 | 31 |
| 1 caller drops the result, none reads it: `void` | 869 | 798 | 71 |
| >= 2 callers drop it, none reads it: `void` (the weak rule shipped) | 358 | 333 | 25 |
| >= 3 callers drop it | 215 | 202 | 13 |
| the shared header says `void` / a type | 12 | 12 | 0 |

The 16 "wrong" asm reads are mostly right about the machine code and wrong about the saved C: the matched C declares
`void` for a function that ends with a call whose result stays in `$v0`/`$f0` (a squared-distance helper ending in a
tail call; the callers read a float). The bytes are the same either way, so for matching a draft they are not
errors. 1,413 of the 1,692 have direct callers; the other 279 are reached through tables and pointers and get no
verdict.

Draft effect (as draft-start-candidates.md: the stored m2c draft of each matched function, the candidate m2c run again
with the function declared as the callers say, both scored by `decomp judge-worker`, isolated and attach-only, no saved
match touched, no permuter, Δ = candidate - raw draft; production judges the candidate beside the raw draft and keeps
the better):

| | |
|---|---|
| drafts in the sample (stored seeds with a readable signature) | 1,589 |
| where the callers retype the draft (exits ambiguous) | 24 (23 `void` drafts made `s32`/`f32`/`s64` by a strong 'value', 1 value draft made `void`) |
| candidates m2c could make and compile | 20 |
| better / worse / same than the raw draft | **5 / 7 / 8** |
| newly exact from the candidate alone | **2** (45 -> 0, twice) |
| Σ Δ over the better ones | -2,655 (buildPlaneFromPoints, a weak `void`, 7834 -> 5404, was the largest) |
| extra m2c runs and compiles | 20 of 1,589 drafts (1.3 %) |

Against the exits alone (draft-start-candidates.md section 1: 32 fires, 0 better, 10 worse): the callers fire on a
different, smaller set and about a quarter of the fires improve the start, two of them exactly. The worse ones cost
a compile and keep the raw draft. So `draft_return_type` is now on by default and takes the callers' verdict; where
the exits are unambiguous ('value', 'void') they still decide, and when callers cannot be read the old guess applies (`retype(..., callers=None)`).

Caveats: one game and compiler; the stored seeds of matched functions (some were already exact as `void`, those are
the "worse" ones: the matched C had the result dropped); the header's declaration is left out of the measurement (it
can come from the matched C). Not measured: whether the retyped starts reach more matches later. Other processors
(ARM, PPC, x86) have no reader yet (ROADMAP).
