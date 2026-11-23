---
title: ray caster zero guard counters and caller save masks
kind: function
compiler: gcc-2.7.2-kmc -O2 -mips3
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 1134-instruction N64 ray caster (KMC GCC 2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`): an outer `do { } while (list)` over
object lists, an actor pass and an object pass, each with a grid mask from a call and two triangle loops (`u16` and `u8`
index triples), then a tail that copies the hit point and returns. Saved best 6336 (205 rows, frame 0x1f0 for 0x1e0),
handed off once with the note that the two grid masks sit in `$t2`/`$t3` with caller-save spills at 0x198/0x19c. Matched
6336 -> 0, one edit at a time from the saved best (no clean rewrite), each judged with the app's judge (`fast_judge.py`).

The source change, and the measured result before and after (scores, the judge used):

- Masks computed before the call that follows them, no `sp198`/`sp19C` round trips: 6336 -> 5885. The masks then rank
  below all nine saved-register pseudos, global.c's first pass fails and the caller-save retry puts them in `$t2`/`$t3`
  (caller-save.c writes the 0x198/0x19c spills itself). The round trips were extra references that lifted them into `$s0`/`$s1`.
- The list parameter used directly instead of m2c's copy (`sp124 = arg0`): its spill slot is allocated first (the
  parameter's pseudo number is lower), 0x124 as in the target. 5885 -> 5866.
- Each loop guard `if (flag < n)` (m2c's) written as a `u16` counter: `i = 0; if (i < n) do { ... } while (i < n);`. cse
  replaced the counter's 0 in the copied entry test with the oldest register known to be 0 (the flag the branch had just
  found zero), so the target compares the flag (`sltu $v0, $v1, $v0`); written with the flag, cse folds it to `beqz`.
  Also removed two stale 8-byte spill slots: frame 0x1f0 -> 0x1e0. 5796 -> 5425 over the six loops. decomp-repair
  `zero_guard_u16_counter`.
- A hit distance read through a struct member (`scratch.spF0`) was reloaded after each store to the output pointer
  (cse invalidates in-struct memory on a varying in-struct store); copied once into a scalar local in the test chain
  (`((hitd = scratch.spF0) >= 0.0f) && (hitd < best)`) it stays in `$f2`: 5425 -> 5180.
- The 16-byte plane output as one struct copy (`*(Plane *) out = *(Plane *) plane;`, `lw`/`sw` pairs) and the plane's
  three points as `verts + tri[k] * 12` from one triangle pointer: 5180 -> 3240. The absolute-value call takes the mesh
  hit's float at 0x10 (a missing argument): 3140.
- One triangle pointer per loop (`tri[0]`, `tri[1]`, `tri[2]`, `tri += 3`) instead of m2c's two cursors: loop.c makes the
  `tri + 4` giv itself and the counter outranks it: 3140 -> 2555. One `u16` counter for all six loops (decomp-repair
  `loop_counter_unify`'s idea): 2555 -> 2230. The `tris + i * 6` sum loaded before the mask word: 2230 -> 1605.
- The vector at 0x18 not `volatile`: 1605 -> 1585 (a volatile store pinned `li $s7, -1` away from the branch delay slot).
- The tail: the target tests a register it has just set to 0 (`bc1tl ...; move $t4, $zero` then `bnez $t4`). That is a
  pseudo set once to a constant: reload substitutes the constant for the spilled pseudo, so the test survives to the
  output. A pointer local set to NULL at the top and tested at the end did it (`found`), with the early-out and the
  no-hit path sharing one `return 0` (a label in the `if (found == NULL)` arm) and the two hit paths as `if`/`else` with
  one `return 1`: 1585 -> 890 -> 420 -> 60.
- The last 60: sched1 boosts an insn whose destination is set exactly once (`birthing_insn_p`), so the call result's
  `andi $v0, $v0, 0xffff` went after the argument moves. A second, live set of that variable in a later loop's mask test
  (`(m & word) && ((temp_v0 = hi & word) != 0)`, the spelling a matched neighbour uses) leaves it unboosted and in `$v0`: 0.

Failed experiments that would otherwise be repeated:

- Moving the hit distance out of the scratch struct into its own scalar: 5296 but frame 0x1e8 (the scalar's slot comes
  after the struct). The copy in the test chain keeps the struct layout.
- `if (call & 0xFF) if ((hitd = ..., ...))` nested: 7380 (the branch shape changes).
- The tail test on the loop pointer (`if (list == NULL) return 0;`): cse2 follows the loop exit and folds it; `while`
  instead of `do`, an `end:` label, `goto`s: all 3140 (the copy block is dead and deleted).
- The same mask variable for both grid-mask calls: the order is right, but local-alloc gives `$v0` to the flag load in the
  second section and the mask pseudo (global) goes to `$v1`: 150. Reusing it for a value computed and read in `$v0` with
  nothing else local there (an `and` test) keeps `$v0`.
- `(u16)` cast spellings of the mask (`& 0xFFFF`, `&=`, single cast, `u16` local, a `u16` prototype): identical or worse;
  the order is decided by the set count, not the spelling.

What is controlled evidence and what is one function:

One function. The cse substitution of a known-zero register into a copied loop test, the caller-save retry for masks
ranked below nine saved-register pseudos, and the reload constant for a pseudo set once are read from the 2.7.2 source
(cse.c, global.c `find_reg`, reload1.c) and the `-dS`/`-dl`/`-dg` dumps; the `found` local and the shared `return 0`
label are scaffolding the match relies on.
