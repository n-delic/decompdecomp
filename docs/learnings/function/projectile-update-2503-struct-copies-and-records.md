---
title: projectile update 2503 struct copies and records
kind: function
compiler: KMC gcc 2.7.2 (-G0 -mips3 -mgp32 -mfp32 -O2)
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

A 2,503-instruction projectile/state update of an N64 game (a switch on a state halfword, ray casts against rooms
and actors, impact effects, a list unlink), KMC gcc 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`. Saved best 28008 (29147
rechecked) after 100+ attempts; worked region by region with `regions.py` and `fast_judge.py`.

The source change, and the measured result before and after (scores, the judge used):

App's judge (judge-worker via fast_judge, confirmed with `compile_and_diff`), in the order applied:
- two uninitialised reads from m2c (a temporary used before its assignment, an `&=` written as `x = K & stale`): the
  frame came back to the target's 0x1e8 (29147 -> 29263, then everything below was measured on the right frame).
- `if (r16 != 0) goto B; goto A;` written `if (r16 == 0) goto A; goto B;` so jump2 does not cross-jump the arm into
  the next one: 29198 -> 27478.
- three-float copies between a member and a frame local as one 12-byte struct assignment (`lw`/`sw`, not
  `lwc1`/`swc1`): 19868 -> 14293 for four together (`float_triple_struct_copy`); one frame-to-frame copy later
  10131 -> 8931 (the others stayed float copies).
- `(v == 1) || (v != 2)` as `switch (v) { default: case 1: A; case 2: B; }`: 23368 -> 21908 (`eq_or_ne_switch`).
- `if ((flags & K) && (v == 1))` as nested `if`s (the `&&` of two simple tests folds into `and`/`sltu`): 21908 -> 20273.
- a zero-test call argument (`(r->flags & 0x2000) != 0`, `r->cb != NULL`) staged in a local before the call (target
  `sltu`, not `srl`/`andi 1`): 20273 -> 19353 (`call_test_argument_stage`).
- a call's literal argument held in a local set before the arms that share the call (`blastMask = 0x20;`, the target's
  `$s1`): 14293 -> 13156.
- m2c's shared tail label moved to the earlier arm (the later arm jumps back): 13156 -> 12071.
- m2c's `goto` list walk as `while (p) { n = p->next; if (n == x) { ...; break; } p = n; }`: 11416 -> 10131
  (`list_search_while`; `for (;;)`/`continue` compile like the goto).
- the last four frame-struct members (only ever reached through `$t0`, 8 bytes apart) as plain locals after the
  struct, its padding 8 bytes shorter: 6151 -> 5435 (`frame_tail_spill_unwrap`). Their spill-slot order followed
  which of two `= NULL` stores came first (4735 -> 3657), not the declaration order (all 24 orders the same).
- a second call's result written into the same local as the first (`var_fp`), so that local outranks the spilled
  ones in global.c.
- a global pointer the target reloads before each store through it as `extern struct { void *block; } g;`:
  5435 -> 4835 (`global_pointer_record`); a counter global `+= 1` with its address formed once as
  `extern struct { s32 value; } g;`: 3302 -> 2797 (`global_scalar_record`).
- smaller: a `u8` store of 0xA0; `(a < b) | (v == 0)` as one expression; an `s16` flag; `(s16)` operands of `/ 1800`
  and `% 30`; a halfword re-read for `< 0`; a `u16` switch value made `s32` (`slti`, not `sltiu`); the player
  flags comma chain as an if/else-if chain.
Result 28008 -> 2797 in the app (98.8%), 77 differing rows, nearly all scheduling order.

Failed experiments that would otherwise be repeated:

- store-address spellings for the reloaded global (array index, integer sum, `M2C_FIELD`, `*(void **)&g`): all
  compiled identically; only the record (or `volatile`) moved it.
- `frame_struct_unwrap`'s full unwrap: 28188. Declaring the spilled locals in any of the 24 orders: no change.
- the impact tail duplicated in both arms with direct pointer arguments: worse (8401 vs 7286).
- moving `var_fs1 = 65536.0f` (scheduled into the prologue in ours, after the first loads in the target): invariant.

What is controlled evidence and what is one function:

One function; each number is the app's judge on the full function, so the gains include knock-on register and
order changes elsewhere. The families named above reproduce their steps from the state before them.
