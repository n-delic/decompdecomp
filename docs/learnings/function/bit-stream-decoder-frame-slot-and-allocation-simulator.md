---
title: bit stream decoder frame slot and allocation simulator
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

The 681-instruction N64 bit-stream decompressor of `bit-stream-decoder-table-loops-and-register-roles.md` (KMC GCC
2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, no calls), resumed from the last faithful source (2252; the app's saved 2072
was a wrong program). Every step below was checked by building the candidate and a clean transcription on the host
and running both on generated valid bit streams (stored blocks, coded blocks with code lengths past the first-level
width, matches, end symbols, then random tails that reach the error returns and the "reuse the previous table" path):
600 streams per step, output buffer, error word and all five tables compared. The harness found the saved 2072 wrong
on every stream, and rejected about one hill-climb merge in five.

The source change, and the measured result before and after (scores from the app's judge-worker; each new best also
confirmed by `compile_and_diff`):

| step | edit | score | family |
|---|---|---|---|
| 1 | the table-sort loop's guard `if (i != 0) { u32 j; j = 0; ...` written `u32 j; j = 0; if (j < i)` | 2252 -> 1965 | `guard_counter_compare` (extended, decomp-repair #1521) |
| 2 | variable merges found by a fuzz-checked hill climb | 1965 -> 1835 | `disjoint_local_merge`, `local_web_split` |
| 3 | the code-assignment loop's `u32 *s = T + k; .. s++; } while (*s == c)` as `} while (T[k] == c)` | 1835 -> 1575 | `cursor_counter_index` (existing) |
| 4 | the lookup length and the extra-bits count split into two variables | 1575 -> 1515 | `local_web_split` (existing) |
| 5 | the saved `k` (`m = k; ... k = m;`) a variable of its own | 1515 -> 1500 | |
| 6 | the window's second-level read `((x >> 10) & mask)` as `((x >>= 10) & mask)` | 1500 -> 1465 | `last_use_shift_in_place` (new, #1528) |
| 7 | the length loop's counter merged as the climb found, the per-table pointers merged | 1465 -> 1340 | `disjoint_local_merge` |
| 8 | `cnt = a; if (a != 0) {` (the copy before the guard fills the branch's delay slot) | 1340 -> 1240 | `guard_copy_hoist` (existing; prove_chain) |
| 9 | inner-loop temporaries written as the function's long-lived variables (below) | 1240 -> 850 -> 760 | `block_temp_role_merge` (new, #1533) |
| 10 | the histogram counters merged into `a` and `i` as the target's registers show | 760 -> 685 | by hand (role table) |
| 11 | `i = 0;` first in the code-length pass's set-up | 685 -> 625 | `statement_slide` (existing; prove_chain) |
| 12 | the count copy, the sort counter and the decode symbol given their own roles (one variable each across loops) | 625 -> 590 | by hand (role table) |

The frame (step 1): the target's sixth 8-byte spill slot (0x24, never read or written) is the dead compare pseudo a
`for`-style guard leaves: GCC 2.7.2 tests `j < i` before the loop, combine folds it to `i != 0` and deletes the insn,
and reload still gives the pseudo a slot. Exactly one guard written as the counter's compare gave the target's 0x58
frame and every save offset; m2c's form puts the counter's init inside the guard, which `guard_counter_compare` did
not see (it now moves `u32 j; j = 0;` out in front, in a block of its own).

The register rows (steps 9-12) were read off a role table, not guessed: compile the candidate with `cc1 -dp -dl -dg`
(the `.i` from `preprocess.py`), map each assembly line's insn uid to the pseudos the `.lreg` dump gives that insn, pair
those lines with the judge's rows, and count, for every pseudo, which register the target has where ours has the
pseudo's register. A simulator of global.c's colouring then reproduces our own assignment exactly from the `.greg`
dump (allocnos in priority order, `floor_log2(refs) * refs / live`; the first register in number order that no
conflicting allocno already holds and that is not a hard conflict; callee-saved registers nobody uses yet only in a
second pass; the allocno's own `;; N preferences` first and other conflicting allocnos' preferences avoided in the
first pass): 84 of 85 allocnos, the 85th being the spilled reload register. Asking the simulator which priority
changes reproduce the target pointed at a handful of pseudos each time, and the table showed the cause: an inner
loop's temporary sits, in the target, in exactly the register of one of the function's long-lived variables (the bit
reader's `a` in `$a2`, the distance entry in `$a1`, the counter `i` in `$a3`, the symbol `c` in `$t1`). The original
reused those variables: the scatter index of one table pass, the loaded code and the shift count of another, the bit
being reversed, the code-length histogram's running total are all `a`, `i` or the distance variable. A short block
temporary is a small high-priority pseudo that takes the lowest free register; the long-lived variable ranks lower and
pushes the loop's pointers into the registers the target has. Each such merge moved 30-120 points; `block_temp_role_merge`
offers them (one child per temporary and free function-level local, most-used locals first).

Failed experiments that would otherwise be repeated:

- Natural `H[c]` loops for the three histogram passes (dropping `off`, `lim` and the pointer cursor): cse merges the
  guard's and the inner test's addresses into one, where the target keeps `lim` as a separate `H + 4c` computed inside
  the guard (2290-5099 against 1665).
- The code-assignment pass as `D[k]` everywhere (no `q2`, no `off`): 3270; the target's outer test reads through a byte
  offset (`off`) of its own, the guard through a pointer, the inner test through the index.
- Spellings of the histogram increment (`H[x]++`, `+= 1`, a pointer) compile identically; so do the R4 bit-reversal
  spellings (`<<= 1; +=`, a pointer `w`, the shift in the test); `for (; mm != 1; mm >>= 1)` adds a test (2800).
- Role edits applied one at a time to a source whose other roles were still wrong usually scored worse (the merges
  move every later colour); the gains came from applying them in the order the simulator's priority list gave, and
  from grids over all subsets of five or six role edits (32-64 compiles).
- Removing the `H` pointer variable (writing the symbol): the R6 pointer's `H + 1` folds into `%lo(sym+4)` (1240).
- Hill climbs over merges, splits and declaration order alone plateau within ~100 points of their start; the climb
  accepted merges the fuzz rejected (the input pointer merged into a table cursor, `tb` into a counter).

Still different at 590 (88 rows): the decode loop's peek window in `$a0` where the target has `$v1` (the target's
local-alloc puts the second-level index temporaries in `$v0`/`$a0` and the window in `$v1`), the code-sort loop's
counter/pointer pair (`$a0`/`$a1` swapped), the last pass's three loop pointers one register off (`$t5`-`$t9`, `$s0`),
and the constant addresses the target loads into `$v0`/`$v1` inside the guarded blocks where ours rematerialises a
hoisted, spilled copy into `$s7`.

What is controlled evidence and what is one function:

Step 1 (one guard, one slot) matches the controlled finding in `loop-guard-counter-compare-slot.md`. The simulator's
reproduction of global.c is checked on this function's dumps only (84/85 allocnos); the role table is a diagnostic.
Steps 6 and 9 are one function: each new family reproduces its step from the source before it, and every accepted
source was fuzz-checked against the clean transcription.
