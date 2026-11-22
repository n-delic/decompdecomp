---
title: Temporary hoist and reassociation families: the audit's missing steps, re-run
kind: finding
compiler: KMC GCC 2.7.2 (-G0 -mips3 -mgp32 -mfp32 -O2)
evidence: verified
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

The two top missing steps of the whole-path audit (`whole-path-audit-library-alone`): a subexpression held in a
temporary (3 of its small stalls) and a reassociated chain (1). The audit entry lists counts, not functions, so the
cases were found again in two KMC N64 games' matched functions (50 KMC projects scanned read-only): the pre-match best
(lowest-scoring compiled attempt before the first 0) against the matched source, with at most a few changed lines, where
inlining the matched source's new local, or regrouping its parentheses, gives the pre-match best back. 5 functions
(12 to 86 instructions) had such a step as their finishing edit:

- `return (x + (D + 0xFFFC)) & 0xFFFF;` -> `t = D + 0xFFFC; return (x + t) & 0xFFFF;` (12 instructions, score 10);
- `out[5] = (u32) (state + (index << 4) + 0x300);` -> `state + ((index << 4) + 0x300)` (48 instructions, 60);
- `__m = __repair_i * 0xC;` at the head of the loop's `if` block, two statements before the call that used it, `s32 __m`
  declared with the locals (86 instructions, 120);
- `0.0f` stored three times -> `z = 0.0f; a[2] = z; a[1] = z; a[0] = z;` (40 instructions, 710);
- a load from a constant address held in a local across two compound stores (59 instructions, 800).

The source change, and the measured result before and after (scores, the judge used):

decomp-repair's `temporary_hoist_repairs` (`temporary_hoist`, `temporary_hoist_block`, `temporary_hoist_pair`) and
`reassociation_repairs` (`reassociate`), wired into the drafts and the Deep Permuter's local lane. The isolated judge
only (`decomp judge-worker`, attach-only), `prove_chain.py`'s greedy chain from the pre-match best with every family,
6 steps, 400 children a step (the temporary families come late in the proposal order, so 80 cut some of them off);
before = every family except these four, after = every family:

| function | start | before | after | finishing step |
| --- | --- | --- | --- | --- |
| 12 insns, `D + 0xFFFC` | 10 | 10 | **0** | `temporary_hoist` |
| 48 insns, `state + (i << 4) + 0x300` | 60 | 60 | **0** | `reassociate` |
| 86 insns, call argument held at the block head | 120 | 120 | **0** | `temporary_hoist_block` (assignment at the head of the block) |
| 40 insns, repeated `0.0f` | 710 | 215 (`float_zero_local`) | **0** | `temporary_hoist` |
| 59 insns, constant-address load | 800 | 800 | 675 | `temporary_hoist` |

4 of 5 reach 0 (0 of 5 before; one improved 710 -> 215); the fifth improves by 125.

What running it on real functions taught, each fixed in the library with a test:

- The reader must skip preprocessor lines: an m2c file's `#define M2C_FIELD(...)` lines were read as the start of the
  `extern s32 D;` after them, so `D` had no type and the first case produced no child at all (the synthetic tests had
  passed).
- The assignment has to be offered earlier than its use, at the first statement of the block it can cross; the third
  case matched only there.
- A floating literal stored repeatedly and a load from a constant address are worth holding; a run carries a load across
  a store only when the store goes into a local array or struct the function never lets out.

Failed experiments that would otherwise be repeated:

- Looking for the audit's functions by one-step identity (a child of the pre-match best equal to the matched source,
  names canonicalised): over 2000 small-diff pairs, 0 before the preprocessor fix, 2 after; the other three cases above
  differ from their matched source by renames and `__repair_*` scaffolding too, and were found by the inline test, not
  identity.
- Signed `+` chains of plain `s32` operands are never regrouped (a partial sum could overflow where the source's did not);
  chains of narrow operands, unsigned ones, bitwise ones and pointer arithmetic are.

What is controlled evidence and what is one function:

Each row is one function judged by the isolated judge; the families are covered by tests in decomp-repair
(`tests/test_temporary_hoist_repairs.py`, `tests/test_reassociation_repairs.py`) and the wiring by
`tests/test_temporary_hoist_reassociation_wiring.py`. Five functions are a sample chosen for having such a finishing step,
not a rate. As a rate check, one step of the two families on the saved best of 92 stalled KMC near-matches (a seeded
sample of open functions of 10 to 200 instructions, saved score 1 to 400, across the same 50 projects): 44 had children
(401 in all), 1 improved (385 -> 320), none matched in one step. Most stalls are not these steps; where one is, it was
not found by anything else.
