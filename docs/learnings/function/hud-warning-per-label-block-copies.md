---
title: HUD warning banner: per-label constant copies back to block copies (3819 -> 0)
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

An 84-instruction N64 HUD routine that draws two warnings, each in a colour copied from read-only data. Compiler:
SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Verified by the app's judge.

From the autopilot (m2c) draft, with library families only:

| step | family | score |
|---|---|---|
| draft | | 3819 |
| one load per label and one store per stack scalar written as `__builtin_memcpy` into sized buffers | `named_symbol_block_copy` | 1182 |
| `p = &colour;` set before the `if` whose join makes the call | `branch_join_address_local` | 161 |
| the first call given the trailing `0` the other call has | `stack_argument_tail` | 60 |
| the callee prototyped with an `f32` fourth parameter | `float_argument_prototype` | 0 |

Rules seen here:

- A three-byte copy with `lb` x3 and `sb` x3 and a 22-byte copy with words and a final halfword are GCC block moves
  (an aggregate initialiser or a builtin string copy), not scalar assignments.
- The size of the last buffer is the room up to the first register save (`sw $s0, 0x1A8($sp)`).
- `addiu $s0, $sp, OFF` before a branch whose join calls with `$s0`: the address was in a pointer before the branch.
  That the original named it is a hypothesis.

A hand-written source with a compound literal (`colour = (Color) { 0xFF, 0, 0 };`) and `strcpy` into a 256-byte
buffer also compiled to the same moves. `memcpy(colour, "...", 3)` did not: it copied a halfword and a byte
(controlled, this function only).

Also: `call_load_temps_inline` raised `KeyError: 'char'` on this draft and stopped `prove_chain`; fixed in the same
library change.
