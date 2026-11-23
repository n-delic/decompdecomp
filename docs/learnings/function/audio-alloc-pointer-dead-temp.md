---
title: an indirect call's pointer loaded into a dead temp, and a product held in a later-reassigned local
kind: function
compiler: KMC GCC 2.7.2 (gcc-2.7.2-kmc), -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-23
---

What the function looked like and the exact compiler and flags: a 157-instruction N64 audio setup function (KMC GCC
2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`, the project's default profile). Inside `if (ms != 0)` it computes
`size = ((rate * ms / 1000) / 192) * 192 + 192`, stores it to a global and calls a global function pointer
`alloc(size * 2, 0, size)`. Saved best 35, seven register rows: the quotient in `$v1` (target `$a2`), the product in
`$s0` (target `$v0`), the pointer load `lui/lw` in `$v0` (target `$v1`), with the load between the product's last
`sll` and `addiu $a2, $v0, 0xc0` in the target.

The source change, and the measured result (fast_judge, then the app's judge recorded 35 -> 25 -> 0):
1. The first quotient back into the masked argument's local (`temp_a2 = (rate * temp_a2) / 1000;`: `$a2`), the second
   quotient into the rate local, which is dead by then (`temp_v0 = temp_a2 / 192;`: `$v1`, the rate's register), the
   product into a local that a later block reassigns (`var_v0 = temp_v0 * 192;`), the size a fresh local
   (`s = var_v0 + 192;`). With a direct call: 255 (the load after the `addiu`).
2. The pointer loaded into the rate local right after the product, and the call through it:
   `temp_v0 = (s32) gDataBankAlloc; ... D_800E4238 = ((s32 (*)(s32, s32, s32)) temp_v0)(s * 2, 0, s);`: 255 -> 0.
   Each part alone from the 35 best is worse (pointer only 435, product only 265); the quotient in a fresh local instead
   of the rate local is 60; the product in a fresh local is 215.
Scaffolding: none new. The match reuses dead temps for unrelated values (the m2c naming the saved best already had);
the earlier `inline_fn` identity, `volatile D_8010309D` and the loop's identical-arm guard remain from the saved best.

Why (read in sched.c, local-alloc.c and calls.c; a hypothesis the match agrees with): `expand_call` loads a global
function pointer into a fresh pseudo in `prepare_call_address`, after the arguments. That pseudo has one set, so the
first scheduler's `adjust_priority` (`birthing_insn_p`) raises the load to `LAUNCH_PRIORITY` once the call is
scheduled; it then wins over the size's `addiu` (whose local, shared with the quotient, has several sets and keeps its
path priority 0x1c) and lands after it, so the product is dead at the load and both get `$v0`. Through the dead rate
local the pseudo has several sets (no launch priority) and an anti-dependence on the local's last read, so the load is
scheduled right after that read and shares its register (`$v1`). The product must be a global pseudo (a local that
another block reassigns): a local product dies at `s = p + 192` and local-alloc's `combine_regs` ties `s` to it, which
pulls the whole chain into `$a2`.

Failed experiments that would otherwise be repeated (all >= 25): the size, quotient and argument as one local (315);
the pointer in a fresh local at any position (315, cse and the scheduler place it as the direct call); an inline
helper around the call (315); the store or the third argument dropped (diagnostic: one consumer less moves the load
one slot too early, 325); `(q / 192 + 1) * 192` and other spellings (315); the product in `var_v0`, `var_v1`, `temp_s0`
or `var_a1` with a direct call (100 to 640); the pointer in the masked argument's local (430) or the product's local
(255).

What is controlled evidence and what is one function: one function, each step scored with the app's exact judge. The
step 255 -> 0 is decomp-repair `call_pointer_dead_temp` (0.5.719+, declares REPAIR); step 1 (which value goes into
which dead temp) has no family yet.
