---
title: an 874-instruction spawner matched by a clean typed rewrite (1176 -> 0)
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags: the 874-instruction N64 object spawner of
block-weight-scope-and-frame-view-defines.md (KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`). A switch on the model's
type byte; one arm creates the object, copies position and rotation, scans the model's command stream, allocates the
slot record's buffers in a two-way `if`, walks the vertices, builds the display list and copies the 0x4C-byte record
back; the default arm does the same through an inner switch that sets a mode and a `kind` flag. The saved best (1176,
after 3786 -> 1176 by families) carried `do { } while (0)` weight scopes, `__repair_rec_*` record casts, a frame struct
with `reserved[20]`, identical-arm guards on uninitialised locals and one shared length local for the arms.

The source change, and the measured result before and after (the app's exact judge, `fast_judge.py`, each new best
confirmed with `compile_and_diff`):
- A clean rewrite from the target's assembly instead of more edits to the scaffolded best: typed records for the slot
  (0x4C bytes), the spawn definition, its model and the object; every slot access written `slots[sp->id].field`
  (the target recomputes `id * 0x4C + slots` each time); scalar locals for the frame slots in first-`&` order (the
  frame came out at 0xA0 with no padding array); each arm's `if` calling `heapAllocTagged(rec->len * 6, 4)` in place
  (the arms' `$v0`/`$v1`/`$a0` rows were the shared length local: a toy function with the same two arms matched at
  once). 2730, with 232 rows that were all saved-register choices.
- The display-list block's three values (`header[4]`, `header[6]`, the list) declared inside each arm's
  `if (header != NULL)` block: block-local there, local-alloc gives them `$s0`-`$s2`, and every global pseudo live in
  that block (the spawn pointer) is pushed to `$s3`, as in the target (2730 -> 1170). One function-wide name for both
  arms makes them global and shifts every saved register.
- The vertex walk indexed (`&model->verts[i]`, loop.c's giv for the offset), `commands` read before the copied
  `planes = rec->planes`, the allocation stored first: then a `do { } while (0)` over the allocation, the loop and
  `buildCollisionPlanesFromCommands` ranks `planes` (0.52) and `commands` (0.50) above the object pointer (0.44), the
  `-dl -dg` table's order the target needs (1170 -> 580). This scope (and one nested inside it, below) is scaffolding:
  no natural spelling put two two-reference pseudos above a 41-reference one.
- `if (slots[sp->id].loaded == 0)` without a `slot` local (575), the 9-bit top field as a bit-field `|= 0x10`
  (`field_rmw_bitfield` shape, 525), a second scope nested around the loop with the outer one closed after the
  `(*objp)->slot` store (`grid.py`, 465).
- Case 0x73 `flags |= 0x1000; kind = 1; break;` as `flags |= 0x1000; goto set_kind;` (465 -> 15): its scheduled
  `li` came first, so its `ori; sw; j` tail cross-jumped into another case. decomp-repair `switch_case_tail_goto`.
- `kind = 0;` above the `scanCommandStream` call instead of after it (15 -> 0). The target's flag is in `$s0`, which
  global.c only gives a pseudo that crosses a call (pass 0 takes any used call-clobbered register that is free;
  `move $v0,$s0` is the `s8` flag's extension). The target clears it in the switch's range-check delay slot all the
  same. decomp-repair `statement_slide` (now also past member stores).

Failed experiments that would otherwise be repeated: the natural `if` arms inside the old best's scope (the arm that
jumps to the loop end was moved out of the loop by loop.c, 3476); `kind` as `s32`, `u8`, `u16`, a one-member struct,
uninitialised, cleared in `default:`, shared with the loop offset or the display list (370 to 5170); a second
`if (kind != 0)` after the arms (an `$s` register but the second test stays, 975).

What is controlled evidence and what is one function: one function. The allocation orders come from the `-dl -dg`
tables and global.c's `find_reg` (read: pass 0 only reuses registers already used, call-clobbered ones count as used,
so a pseudo that crosses no call never lands in an unused `$s` register).
