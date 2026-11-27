---
title: nested failing guards that share one tail are one early exit
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

Symptom: branch targets and one `j end; sw` differ. The target has a store of a "nothing to do" value, reached by
`beq`/`bne` from each of several tests, placed *before* the work and jumping to the epilogue (the last test is a `beq`
over it into the work). The draft has it after the work.

An 85-instruction N64 timer update. m2c wrote the six tests as nested `if`s whose failing paths `goto` two labels after
the outermost one, with the shared store after them. Written as

    if (a == NULL || b != 0 || a->x == 0xFFFF || c != 0 || d < 2U || a->y != 0xC8) {
        gTimer = 0;
        return;
    }
    /* the work */

and nothing else, the app's judge went from 720 to 0. One `if` per test, each with its own copy of the tail, also
scored 0 (the compiler merges the tails).

`goto_tail_early_exit` handles one level only; `prove_chain` on the 720 source reached 620 with `exit_run_outline` and
found nothing further (a few families, capped at 30 variants). decomp-repair `guard_ladder_early_exit` walks any depth.

One function and one compiler: whether other compilers lay these out the same way is not tested here.
