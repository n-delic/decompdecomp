---
title: literal read once needs two scopes
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 149-instruction N64 distance check (loop over 7 slots, `sqrtf` of three float differences, a few stores and a
`v = (1.0f - v) * k + v` update). Matched neighbours of the same shape were already `-O1`; the saved best was an `-O2`
draft at 2655. Plain C written like the matched neighbour was 6150 under `-O2` and 675 under `-O1`.

675 left a float position in `$f30` where the target keeps it in `$s4`, and `1.0f` and `0.025f` in each other's
registers. loop.c hoists both constants; `0.025f` has two uses (5 references) and `1.0f` one (3), so `1.0f` ranked last.

- one `do { } while (0)` round the update: 230 (`run_weight_scope`)
- two round it: 205, all FP registers right (`literal_weight_depth`)
- the s32 prototype in the shared header with no `return` made the loop branch `bnel`; `return (s32) ((u8 *) arg1 + i);`
  gave the target's `bnez`: 5 (`slot_value_return`)
- `addu $v0, $s0, $v0` operand order: `((M *) base)->e[i].v` on a typed record: 0 (`base_sum_local` finds it too)

Failed: products kept in locals, `x += (1.0f - x) * k`, a local record pointer (1070), chained assignments (910).

The two wrappers and the return are scaffolding: the function returns nothing in the original (its matched caller drops
the result). Changing the header's `s32` to `void` was refused, because the header-edit recheck listed seven unrelated
matched functions as no longer matching; their saved C also judges non-zero with the unchanged header.

One function; the compiler explanation is read from the allocation table, not confirmed in its source.
