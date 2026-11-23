---
title: A 557-instruction state handler, 1950 to 0 in twelve source edits, nine of them new library families
kind: function
compiler: KMC GCC 2.7.2 (gcc-2.7.2-kmc) -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 557-instruction enemy state handler (a counter-driven machine that plays a sound, advances a timer, and unlinks a
handle), KMC GCC 2.7.2 with the project's flags. The saved best was 1950 after about 24,000 automatic trials and carried
a lot of search scaffolding: a `volatile` three-float array that was only written, two `__repair_store_N` locals, a
`char` callee return, a `u8` counter masked with `0x80`, three copies of one inline helper. The judge's rows showed
about twelve places, none of them a structural difference, so the work was twelve small edits, each scored in the
standalone judge (`fast_judge.py`) and the last one confirmed by `compile_and_diff`.

The source change, and the measured result before and after (scores, the judge used):

| # | edit | score after | reached by the library before this? |
|---|------|-------------|--------------------------------------|
| 1 | a callee declared `char f(..)` whose result is only tested: `s32` (the `sll $v0,$v0,24` before the `bnez` goes) | 1950 -> 1900 | no: `callee_return_wide` (`callee_prototype_repairs`) |
| 2 | `u8 n = byte - 1; ... if (n & 0x80)` as `s8 n; if (n < 0)` (`andi`/`beqz` becomes `sll`/`bgez`) | 1900 -> 1600 | no: `byte_sign_test` |
| 3 | that counter's temporary no longer reuses the name of an unrelated pointer local | 1600 -> 1360 | yes: `local_web_split` |
| 4 | `t2 = t - 1; if (t > 0) { *p = t2; if (t2 == 0)` as `t -= 1; *p = t; if (t == 0)` | 1360 -> 1345 | yes: `derived_copy_in_place` |
| 5 | `(s32) *(s16 *)(p + 0x178) == 0` read as `*(s32 *)(p + 0x178) == 0` (the target's `lw`; the halfword beside it is cleared with it) | 1345 -> 1135 | no: `halfword_pair_word_test` |
| 6 | two staged values (`__repair_store_0/1`) and their stores as two direct stores | 1135 -> 995 | no: `staged_store_inline` |
| 7 | the two neighbouring stores `*(s16 *)(p + 0x178) = 0; *(u16 *)(p + 0x17A) = ...;` swapped | 995 -> 785 | no: `adjacent_cast_store_swap` |
| 8 | the record pointer the next statement reads loaded into a local above the previous global store | 785 -> 580 | no: `load_above_global_store` |
| 9 | `if (h == -1) { S; return; } T; S; return;` as `if (h != -1) { T } S; return;` (`bne`/`nop`/`j` becomes one `beql` whose slot is `S`) | 580 -> 180 | no: `shared_return_tail_merge` |
| 10 | the write-only `volatile f32 cameraAngles[3]` plain | 180 -> 60 | yes: `volatile_local_drop` |
| 11 | in the opening, `{ f32 pitch = LOAD; v = 1; b = arg; ca[1] = pitch; } t = ...;` as `v = 1; b = arg; t = ...; { f32 pitch = LOAD; ca[1] = pitch; }` | 60 -> 1 | no: `block_extract_sink` (each half alone is 60 and 1420) |
| 12 | a switch whose arms preset a value: the kinds the target sends past the default arm (`case 10: case 11: case 12: case 13: case 20: break;`) | 1 -> 0 | no: `linked_switch_empty_arm` (`targeted_switch_labels`) |

The score 1 before the last edit had every instruction equal; the app's verdict said "every instruction reads the same,
but the bytes differ because of the linking notes": five jump-table entries (cases 10-13 and 20) pointed at the default
arm's `addu $a0,$zero,$zero` where the target's pointed at the code after the switch. A case that only breaks has no
code, so the table entry is the join; no relabelling of the existing arms can reach it (the compiled table's
destinations are the arms'), which is why `linked_switch_labels` (a closed permutation of the entries) found nothing.
The match was verified by `compile_and_diff` (557 instructions, byte-identical), the function stayed matched afterwards.

Failed experiments that would otherwise be repeated:

- Rewriting the opening as the natural source (`cameraAngles[0] = m->yaw; cameraAngles[1] = m->pitch;` with the table
  entry read twice), all 5040 orders of its seven statements (720 with `var_s6 = 1` fixed): best 165. Hypothesis, not
  read in the compiler's source: the target's second read of the table entry (`lw $v0,($v0)` after the first float
  store) survives because the store may alias it, and the marker local the saved best carried keeps the first read
  separate. Keep that shape and change the order (edit 11).
- `prove_chain.py --families all --combine 8` from the saved best (before the new families): 1950 -> 1775 in three steps
  (`volatile_local_drop`, `arm_private_rename`, `derived_copy_in_place`) and stalled; none of the other nine edits is a
  child of any family that existed.
- Leaving `cameraAngles` `volatile` kept the score at 180 whatever the order of the opening (observation: no order of the
  stores tried reached the target's `sw $zero,0x20($sp)` before `swc1 $f0,0x1c($sp)`); plain, the first reorder reached 1.

What is controlled evidence and what is one function:

All twelve are one function's measured edits (verified: the judge and `compile_and_diff` accepted the bytes). For each
new family the source before the edit is an input whose child equals the edit and scores as measured (or better), and
each family has its own unit tests in decomp-repair. Which families fire on other games is not measured here; each is
offered as a proposal and judged like every other.
