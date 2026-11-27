---
title: a pairwise search with an early exit: m2c goto into the else, 1941 to 0
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

An 84-instruction N64 pairwise collision test: two nested loops over 12-byte element arrays, `u16` counts in
globals, a callee tried with the pair in both orders and `return 1` at the first hit. The saved best (1941) was m2c's
ladder: the exhausted inner loop `goto`s into the `else` of its guard, where the outer step is, and every hit `break`s
to one `return var_v0`.

From the saved best, with the app's judge:

1. The second call got one argument; the target also sets `$a1` (`addu $a1,$s0,$zero`): `f(temp_s1, temp_s0)`.
   1941 -> 1931 (`missing_call_argument_local`, which now offers the variable named after the copied register).
2. The ladder as `do { } while` with `return 1` / `return 0`, the two tests joined by `||`. -> 1667
   (`nested_exit_loop_ladder`).
3. `symbol_base_unwrap` -> 665, `symbol_base_inline` -> 380, `statement_slide` (the outer counter's start after
   the call) -> 170, `symbol_base_inline` -> 0.

By hand from a clean rewrite, a counted inner `for` was 421 and the same loop as `j = 0; if (j < count) do { }
while (j < count);` was 0 (`counted_for_guarded_do`): the `for` kept a second `lui` for the count global in the inner
preheader, a ninth saved register and a frame 8 bytes larger. Which pass decides it is a hypothesis.

Failed experiments that would otherwise be repeated: two separate `if (..) return 1;` instead of `||` (325);
the same for the global as a one-element array or a cast (421); a pointer local to the count (1295); the
guarded `do` keeping the result variable and `void *` temporaries (1641 against 865 with returns).

This is one function; the compiler behaviour behind each step is not confirmed in the compiler's source.
