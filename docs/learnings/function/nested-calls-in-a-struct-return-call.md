---
title: helper calls nested in the argument list of a call with a structure result
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 261-instruction N64 visibility test: four times, two helpers write a vector each through their first argument, a
third call takes both vectors by value and returns a vector, and a fourth reads it.

Written as separate statements (`sub(&u, b, a); sub(&v, c, a); n = cross(u, v);`) it judged 340: every row left was
the order the stack addresses were computed in and the saved registers that followed. The target computes the address
of `n` right after the call before the group, ahead of both helper calls.

Written as `n = cross((sub(&u, b, a), u), (sub(&v, c, a), v));` in all four groups it judged 0. decomp-repair
`out_call_into_argument` makes that child (340 -> 0 on the separate-statement source).

The explanation, that `expand_call` handles the structure result's address before it expands the arguments, is a
hypothesis read from the target's instructions, not from the compiler's source.

Failed or misleading:
- The project's default `-O2` judged the same plain C 9018; the target's prologue saves every callee register before
  it copies the float parameter, and `-O1` judged it 340.
- `*sub(&u, b, a)` as the argument (the helper returns its destination) loads through the returned pointer, which the
  target does not (5677).

One function; only the unit test of the family is a controlled check.
