---
title: A 280-instruction event interpreter rewritten from m2c scaffolding, and the four steps it needed (2525 -> 0)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function looked like: a 280-instruction N64 sound-sequence interpreter (KMC GCC 2.7.2): 64 slot records of 0x28
bytes in a player struct reached through one global pointer, an inner `for (;;)` that decodes one 4-byte event per
slot while its due time has passed (note-on with a velocity/key clamp and a free-list node, a patch load from two
tables, a parameter store, an end marker). The saved best (2525, 111 differing rows) was m2c plus search scaffolding:
`inline_fn2345(x, 0xFF)` wrappers, one-member `Field_*` record casts, a duplicated uninitialised test, `register`
locals. `prove_chain` from it and from its scaffold-stripped form (2903) found nothing.

The source change, and the measured result (the app's judge in a standalone worker, `fast_judge`): a plain rewrite with
a typed record view of the slot (`(EV *)((char *)p + off)` with the fields at their real offsets, so the offset stays a
register addition) and the loop as the target has it: 11337 -> 4242 (`p = gSeqPlayer;` at the outer top, `e = p + off;`
first in the inner body, the player pointer reloaded just before the loop test, a copy `k = off;` the post-call accesses
read, the note path reading `gSeqPlayer->...` directly, not `p`) -> 2587 (a second local for the note path's slot
pointer, so the two lifetimes are two pseudos) -> 1300 (reads after the call through `gSeqPlayer + k`, not the slot
pointer, so it is not saved across the call) -> 730 (clamps written into the clamped locals) -> 335 (`te` locals dropped:
the chan*4 address computes before the table address) -> 225 (the call result written back into the local that carried
its first argument) -> 15 (a masked index shared by both arms, with a byte copy that one arm reads) -> 10 (the index
as a `u16`) -> 0 (the copy's read inside two nested `do { } while (0)`). The recorded best is the 0 source; it relies on
that double scope as register-priority scaffolding (one scope 20, three scopes 0).

Why each step (read from the `.lreg`/`.greg` dumps and `global.c`/`cse.c`; hypotheses where not confirmed): the clamp as
`x = (x < K) ? E1 : E2;` builds the conditional in a temporary moved into `x` (`and $v1,..; move $a3,$v1`), a plain
`if`/`else` assigns `x` directly (200) and a separate result local is a second pseudo (390). `R = f(A, ..)` with `A` dead
afterwards is `A = f(A, ..)` in the target (`move $a0,$v0` kept: the compare and the store read `A`'s pseudo). The
index pair: `global.c` allocates by `floor_log2(refs) * refs / live length` with references weighted by loop depth, and
`expand_preferences` merges the preferences of a dying source and its destination (a `& 0xFF` argument result prefers
`$a2`, and so does the byte it is made from, so a pseudo allocated before it skips `$a2`: a byte that is the first
argument's source stays in `$a2` only when nothing lower-priority that overlaps it prefers that register).

Failed experiments: `volatile`/`register` hints, declaration order, `u8`/`s32` index types alone, separate clamp locals
(1260), the copy defined inside the arm (cse folds it), two copies (both fold), a comma-expression copy, `do { } while (0)`
around other statements, and `prove_chain --families all --combine 10` on the 730 and 10 sources (nothing found; the
chain stops at the first step with no improving child).

What is controlled evidence and what is one function: the library families are `call_result_argument_reuse` (335 -> 225
on the stage before it, 2525 -> 2510 on the saved best), `clamp_result_operand_reuse` (390 -> 10 on its stage, 2525 ->
2325 on the saved best, 2903 -> 2593 from the stripped form with every clamp at once) and `shared_index_byte_copy`
(320 -> 0 in one child; one scope 20, two or three 0; a `u16` index needed). The structural rewrite from the m2c form
(typed slot view, `k = off` copy, reloaded pointer, direct `gSeqPlayer->` reads) has no family yet.
