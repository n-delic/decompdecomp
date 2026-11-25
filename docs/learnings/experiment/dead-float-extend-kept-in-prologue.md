---
title: A dead lwc1 + cvt.d.s kept in the target: what deletes the conversion and what keeps it
kind: experiment
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:
A 310-scored N64 sound-loop function (`sn-n64-281` default profile): two guarded `lwc1 $f2, 0xDC($a0); cvt.d.s
$f2, $f2` whose result nothing reads (`if (A->anim == 0x17B) ...; if (B->anim == 0x18B) ...`).

The source change, and the measured result before and after (scores, the judge used):
Unsolved. Every dead `(f64)` value is deleted by flow (or by cse's trivially-dead pass); a volatile load keeps the
`lwc1` but not the `cvt`. Inline asm and `register ... asm("$f2")` are refused by the judge (compiler.py).
Observation: a compare that survives flow and is deleted later keeps both insns. `f` set in both ifs and
`if (f > 0.0) { D += K; } else { D += K; }` after them: jump1 keeps the identical arms (no cross-jumping before
reload), jump2 cross-jumps them and deletes the branch and the compare, and the `lwc1`/`cvt.d.s` stay. It scored
1557 only because `f` is uninitialised on one path, so flow makes it live from entry across the first call and global.c
gives it `$f20` (an `sdc1` in the prologue). Per-if forms whose arms are empty or trivially dead lose everything at
jump-after-cse (1500); nesting the second if into both arms of the first gave 1850.

Failed experiments that would otherwise be repeated:
Empty-body float compares, `f != f`, `f + 0.0`, static inline empty `dbg(f64)`, a non-inline helper (adds `jal`),
`(f64)` expression statements, extra volatile f64 store (328), `while (f > 0.0) break;`, self-copies in the arms.

What is controlled evidence and what is one function:
One function. Next: a consumer that survives flow, is deleted by jump2 or reorg, and whose variable is set on every
path into it without crossing the first call (per-if compares whose identical arms are code the target has once).
