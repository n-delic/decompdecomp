---
title: bit stream decoder subtable pointer and loop roles
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 681-instruction N64 bit-stream decompressor of `bit-stream-decoder-table-loops-and-register-roles.md` and
`bit-stream-decoder-frame-slot-and-allocation-simulator.md` (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, the app's
`-fno-schedule-insns`), resumed at the saved 515 (83 rows, `prove_chain --families all --combine 10` found nothing).
Every kept source was built on the host beside the 515 source (itself checked against a clean transcription) and run
on 600 generated streams: stored blocks, coded blocks whose canonical codes reach length 15 (second-level lookups in
both tables), 102-symbol trees with many long codes, matches with extra bits, the "reuse the previous table" path, and
random tails; output buffer, error word and all five tables compared. The generator's own expected output agreed with
the reference on all 480 valid streams.

The source change, and the measured result before and after (scores from the app's judge-worker; each new best
confirmed by `compile_and_diff`):

| step | edit | score | family |
|---|---|---|---|
| 1 | both second-level lookups `(T + K)[(e >> 7) + ((x >>= 10) & m)]` as `(T + K + (e >> 7))[(x >> 10) & m]` (the literal table's shift written plain again) | 515 -> 595 | `subtable_base_offset` (new, decomp-repair #1579) |
| 2 | the code-length sort loop's counter (it shared the decode window's variable) as the next pass's counter `i` | 595 -> 415 | `loop_role_rename` (new, #1582) |
| 3 | fuzz-checked rename climb, reduced: the sort counter and the last pass's code word as one local, the decode loop's lengths a local of their own | 415 -> 410 | `loop_role_rename`-like |
| 4 | the third pass's byte-table and code-table cursors (`bp = T; tp = tab; i = 0; .. bp++; tp++;`) as `T[i]`, `tab[i]` | 410 -> 350 | `cursor_counter_index` (extended, #1583) |
| 5 | the code-length pass's bound `while (H[c] >= i)` read through `u32 *lim = H + c;` named first in its guard, as the later passes have it | 350 -> 290 | `loop_bound_pointer_local` (new, #1584) |

Step 1 is the decode loop's 21 rows at once: written as one index the sum's first operand is the entry's offset and
local-alloc ties the sum to it; with the offset in the pointer the window's masked bits come first (`and $v1,$v1,$v0;
addu $v1,$v1,$a0`), and with the shift written plain its result is a new temporary that takes the window's dying
register (`srl $v1,$v1,0xa`), so the window keeps `$v1`. Alone it lost 80 elsewhere: the window variable doubled as the
sort loop's counter, which then sat in `$v1` too; step 2 freed it. The earlier `last_use_shift_in_place` (1500 -> 1465)
was the right move for the one-index spelling only.

Step 4: loop.c hoists the error constant (`li $t0,-8`) to the preheader before the givs it makes of `T[i]`; a cursor
of the source's own is set up before the hoisted constants, so the order flips with the spelling.

Step 5: the inner test's address copy (`move $a1,$a0`) is emitted where the source names the bound: as the index it
came after the store cursor's set-up, as a pointer named first in the guard it comes before it.

Failed experiments that would otherwise be repeated:

- Pair and triple grids of per-region renames (every scalar in every marked region to every other, or to a fresh
  local): no improving pair at 515, 415 or 290; random fuzz-checked climbs of 4000-6000 steps each found only step 3.
- Spellings of the three pass bounds and pointer inits (`H + c`, `&H[c]`, `c + H`, the symbol instead of `H`, integer
  sums `(u32 *)(off + (u32)H)`): all compile identically. The target's fresh `lui $v0,%hi(D_80112EE0)` inside the
  guards is loop.c's choice: in `-dL` every such symbol load is a movable combined ("done move-insn matches") with
  the other loads of the same symbol and hoisted to the function entry, spilled, and rematerialised into `$s7`.
- The three passes' outer loops, the phase loop or the main loop as goto loops: 325 to 9281 (the hoisting above does
  not change; registers and the frame do).
- Merging the four passes' `off` variables or the table pointers (`q2`/`q3`/`q4`): no gain (290-315).
- The R1 histogram increment (`H[sp1C] += 1`, `++`, the symbol, the store order): identical code; its `$v0`/`$v1`
  swap is local-alloc's.
- The R2 sort loop's counter and inner counter as every pair of free function-level locals: 290 at best.
- The last pass's mask as `(1 << tb) - 1` (the target's `sllv $v0,$s7,$s3`): 415 -> 495, it lifts `tb` past the
  saved count `m` and swaps `$s2`/`$s3`.

Still different at 290 (58 rows): the first passes' counter (`$t3` where the target has `$a3`) and the histogram
increment's `$v0`/`$v1`; the sort loop's counter and pointer (`$a0`/`$a3`); the code-assignment pass's four pointers
one register off and its `D_800B283C` giv set-up from a hoisted copy; the three passes' bound symbol from the hoisted
copy (`$s7`) where the target loads it fresh into `$v0`; the last pass's inner pointers (`$t5`-`$t9`, `$s0`).

What is controlled evidence and what is one function:

One function. Each new or extended family reproduces its step's exact source from the source before it (tests in
decomp-repair), and every kept source passed the 600-stream behaviour check; `loop_role_rename`'s 48 children on two
of these sources all passed it too.
