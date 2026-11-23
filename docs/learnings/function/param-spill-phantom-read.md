---
title: param spill phantom read
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -fno-strength-reduce
evidence: verified
date: 2026-11-23
---

A 157-instruction N64 effect spawner (14 parameters, a three-arm switch jumping to one shared call, nine saved
registers), `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -fno-strength-reduce`. Saved best 368; matched (judge 0, the
app's compile verdict 0) in five edits, each with the score it gave from the one before:

1. A parameter's spill slot read as an uninitialised local (`field = sp1E;`, `sp1E` assigned nowhere; m2c had seen
   the reload of a slot it never saw written) written as the parameter, the identical-arm guard `if (arg4) {S} else {S}`
   written once: 368 -> 1866 alone. The extra `spNN` was an 8-byte frame slot, and the parameter gained a reference
   and took a saved register from the one the target keeps there (priorities from `-dl -dg`: 3 refs / 71 live = 0.042
   against 2 refs / 50 live = 0.040).
2. The rival's only statement in `do { var_a2 = arg3 & 0xFFFF; } while (0);` (flow.c counts a reference once more
   per loop round, so the rival outranks the parameter and the parameter is the one spilled): 1866 -> 225, the
   frame and the registers right, three rows of the shared call left.
3. The call after the `goto` join given its first argument as a copy set on every path (`var_a0 = temp_v0;` beside
   `var_a1`/`var_a2` in each arm, three sets), so the target's `move $a0` lands in each arm's jump slot: 225 -> 160.
4. The callee's second parameter `unsigned short` -> `int` (the arguments are `lh`/`lhu` loads, no `andi` in the
   target): 160 -> 100 (alone, without step 3: worse, 520).
5. The statement after the float-constant stores joined to their line (the assembler put a `nop` after `mtc1` where
   the debug line marker sat between): 100 -> 0.

Library: steps 1+2 are one child of `phantom_param_read`, step 3 is `goto_join_argument_copy` (both new, decomp-repair),
steps 4 and 5 were `callee_parameter_width` and `debug_statement_boundary`. `prove_chain` from the saved best with
those on the path: 368 -> 225 -> 160 -> 100 -> 0.

Failed: removing only the phantom (1866), widening the prototype before the copies (520), the copy set just before each
jump instead of beside the other arguments (225 against 160).

Controlled: the chain above is a replay of the match from its saved best. One function otherwise; the allocation
explanation is read from the priority table, not from `global.c` (hypothesis). The match relies on scaffolding:
the `do { } while (0)` around one statement, and the copy local on the join call.
