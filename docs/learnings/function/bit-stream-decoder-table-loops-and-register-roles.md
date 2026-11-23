---
title: bit stream decoder table loops and register roles
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 681-instruction N64 decompressor (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, one pointer parameter, no calls, a leaf that uses
every callee-saved register and spills five locals): a bit reader inlined at about twenty sites (`bits -= n; if (bits < 0) {
dst = word; word = next; next = *p++; dst = (dst + (word << (bits + n))) & mask; word >>= -bits; bits += 32; } else { dst = word &
mask; word >>= n; }`), a stored-block copy, and for a coded block two rounds of canonical-code table building (count, sort, assign
codes, fill a two-level lookup) followed by a decode loop with literal and match arms. The saved best was 15412 (m2c's draft plus an
earlier agent's scaffolding: `register`/`sp1C` workarounds, `hoist_*` pointers, `do { do { } while (0); } while (0)` fences, one
pointer-cursor loop forms). `prove_chain` from it found nothing; every spelling of the loops differed.

The source change, and the measured result before and after (scores, the isolated judge, the app confirmed 4235 and 2427 by
`compile_and_diff`; not matched, see the end):

A clean rewrite from the target's assembly (the bit reader as one macro whose destination doubles as the arm's temporary, as the
target's `move $a2,$t2` shows), then one edit at a time. Every step was checked by undoing it on the working source and asking
every declared decomp-repair family for a child that makes it:

| step | edit | score | family |
|---|---|---|---|
| 1 | the clean macro form of the whole function | 15412 -> 12971 | (none: m2c's expanded arms as one macro, by hand) |
| 2 | the 15-bit stored-block count is `s32` (`n-- != 0` keeps the old value in a copy; unsigned folds to `n - 1 != -1`) | 12971 -> 11781 | `signed_postdec_counter` (decomp-repair #1450) |
| 3 | the table index sum written `(y >> 7) + ((v >> 10) & mask)`: the shifted entry is evaluated first | 11781 -> 11371 | `index_sum_swap` (#1450) |
| 4 | the loop bound `cnt` copied from the read inside the `if (a != 0)` | 11371 -> 11151 | `guard_copy_hoist` (existing) |
| 5 | `j = 0;` before the two pointer set-ups (the delay slot of the skipped loop's `beqz`) | 11151 -> 10736 | `statement_group_order` (existing) |
| 6 | the parallel table cursors (`*bb`, `*cc`, `*dd`, `k++`) written as `T[k]` | 10736 -> 6927 | `cursor_counter_index` (decomp-repair #1451) |
| 7 | `tab` and `phase` declared before the other locals: spilled pseudos take their slots in pseudo-number order (0xc, 0x14, 0x1c) | 6927 -> 6885 | declaration order (a search target, not a family) |
| 8 | `*bz++ = 0` for the byte-table clear, `tab[ix]` (not `*w; w += t5`) for the code fill | 6860 -> 5725 | `array_index_cursor`, `cursor_counter_index` (#1451) |
| 9 | a mask local `(1 << tb) - 1` and a bit local `1 << c` named once in the block that opens their loops | 5320 -> 4665 | `invariant_shift_local` (#1450) |
| 10 | `(tab + ((ent >> 7) + t5))[ix]`: the two integers add before the scale, so `t5 * 2` is not a hoisted, spilled term of its own | 3775 -> 2627 | `pointer_sum_regroup` (#1450) |
| 11 | one pointer local per loop (`q1`..`q4`) instead of one shared `q`; the decode loop's entry, symbol and length in one variable, the distance entry in another; the stored count and the decode's code length in different variables | 4665 -> 2237 | `local_web_split`, `disjoint_local_merge` (existing) |

The register rows are the rest: the target gives the bit count `$t4`, the word `$t2`, the loop counter `$a3`, `c` `$t1` in five
loops, and spills the table pointer, the pass number, the code-length word, the mask and the incoming argument (`$s7` is the reload
register). First-fit allocation by priority (`floor_log2(refs) * refs / live`) makes each role's register a consequence of which
pseudos are allocated before it; the source spellings that reorder them are variable splits and merges per loop, not statements.

Failed experiments that would otherwise be repeated:

- Declaration-order and variable split/merge hill-climbs (three workers, 2000+ compiles each) plateau at about 2200 from the
  clean form: single moves do not change the rank of the hot pseudos, and ties drift into unreadable renames.
- The saved best's pointer cursors (`hoist_D_80112EE0 = &D_80112EE0`, `*var_a0_5`) were kept as the first reading of the target's
  `lui`/`addiu` set-ups; they were the rewrite's biggest obstacle (11271 with them, 6927 as indexes).
- Making the 8-byte frame hole: the target has six 8-byte spill slots (argument, table pointer, pass number, code-length word, one
  unreferenced, mask) and ours five; the sixth slot belongs to a pseudo whose every reference is deleted after reload. No source
  change that creates it was found.

What is controlled evidence and what is one function:

Steps 2, 3, 6, 8, 9 and 10 are controlled for this function: undoing each on the working source and running the new family
reproduces the edit exactly (`tests/` in decomp-repair, and the same source text). Steps 4, 5 and 11 are measured with the
existing families. The final register assignment is not reproduced: this is one function and the match was not reached.
