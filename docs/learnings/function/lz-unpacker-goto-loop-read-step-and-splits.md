---
title: LZ unpacker as a goto loop, read-then-step byte reads and a split byte-swap shift
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 165-instruction N64 LZ unpacker: a bit reader and two length readers called with the addresses of three stack
locals (stream position, bit buffer, bit count), three copy loops, a byte-swapped 32-bit output length in the header.
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.

The source change, and the measured result before and after (scores, the judge used):

The app's judge, from the saved m2c best (6538):

| step | score |
| --- | --- |
| plain structured rewrite, `while (--len != -1)` copy loops | 3218 |
| one goto loop (no `for`/`while` around it), so loop.c does not hoist `-1` out of the copy tests | 5050 |
| in-loop calls pass pointer locals set before the loop; the calls at the shared long-match label and the end pass `&x` | 1475 |
| the literal run reads `*(*inp)++` through the pointer local | 1200 |
| `do { } while (0);` before the goto label (the target fills delay slots from the loop top) | 880 |
| the header length in separate statements; the flag read before `len = 3` | 540 |
| `byte = *in++;` as `byte = *in; in++;` | 180 |
| `len = byte + 8;` above `if (byte == 0) goto end;` | 120 |
| `total = (..) << 16;` as `total = (..); total <<= 16;` | 0 |

The last four steps are decomp-repair families: `condition_call_before_store` (now run in goto functions),
`deref_postinc_split`, `exit_guard_assign_hoist`, `shift_assign_split`. Called in that order on the real source they
went 760 -> 540 -> 180 -> 120 -> 0 (each child judged). `address_then_fence` already offers the fence.

Why (hypotheses from the target's order and the pass dumps, not confirmed in the compiler source): `byte = *in++`
leaves a register holding `&in` that cse hands to the next block that passes `&in` to a call, so its `addiu` is
computed in the read's block; with the length assigned above the exit, reorg takes it into the branch's delay slot.

Failed experiments that would otherwise be repeated:

- Nested `for`/`do` loops around the same body: 3604 (and the `-1` of the copy tests is hoisted into a saved register).
- `do { } while (0)` around the byte read or after the end label: no change (540).
- A `u32 hi = size >> 16;` second variable: combine folds the shift into the mask (1050 from 880).

What is controlled evidence and what is one function:

All of it is one function.
