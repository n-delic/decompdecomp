---
title: Two remainders of one global named in fresh locals before their expressions
kind: finding
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mfp32
evidence: observation
date: 2026-11-26
families: shared_operand_remainders, for_header_companion_index
---

What the function (or case) looked like and the exact compiler and flags:

A 194-instruction N64 HUD drawer (SN64 GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float`, judged also
with `-mfp32`) computed a pulsing alpha from one frame counter global:
`a = 0xFF - (G % 144) * 4; b = 0xFF - (G % 36) * 6;`. Both remainders come from one `mult` by the magic
`0x38E38E39` (`mfhi >> 5` for 144, `>> 3` for 36). The target finishes the 36 remainder before
`addiu $v1,$zero,0xff; sll $v0,$a2,2; subu $s7,$v1,$v0` (the `a` value); the inline form put the `a` subtraction
between the two remainder chains and moved every register of the shared chain (27 rows: magic in `$a0` for `$v1`,
the 144 remainder in `$a3`/`$v1` for `$a2`, the colour constants after it).

The source change, and the measured result before and after (scores, the judge used):

```c
s32 rem144;
s32 rem36;
...
rem144 = G % 144;
rem36 = G % 36;
a = 0xFF - rem144 * 4;
b = 0xFF - rem36 * 6;
```

Isolated judge (the app's judge-worker): 450 -> 105 under `-mfp32`, 475 -> 130 under the function's own profile
(recorded in the app). The two assignments in the other order, or the declarations swapped, score the same 105.
decomp-repair `shared_operand_remainders` (0.6.62) proposes it.

Failed experiments that would otherwise be repeated:

- The function's other locals reused for the remainders (`r = G % 144; g = G % 36;`, `r` and `g` assigned again
  later as colour channels): 395; `a = G % 144; g = G % 36;` 405; `g = G % 144; r = G % 36;` 505.
- The result variables staged (`a = G % 144; b = G % 36; a = 0xFF - a * 4; b = 0xFF - b * 6;`): 435.
- The global copied into a local first (`r = G; a = 0xFF - (r % 144) * 4; ...`): 1345; copied and split: 635.
- `a = (G % 144) * 4; b = ...; a = 0xFF - a; b = 0xFF - b;`: 775.
- Earlier agents: `b`'s statement before `a`'s 620, `a = 0xFF; a -= m * 4;` 745.

Hypothesis (not confirmed in the compiler's source): fresh single-set user variables keep the two remainders as
statements of their own through expansion and the first scheduler, so both chains are emitted before the outer
expressions; a reused, multi-set variable is ranked differently by the allocator.

What is controlled evidence and what is one function:

One function, measured in the app's judge. The rest of that function's chain, from 105: `prove_chain --fp-flip`
found `assignment_at_use` (45) and `for_step_order` (40); the last rows (the loop's `px` and its `i * 2` halfword giv in
swapped saved registers, `move $s3,$a3` / `move $s2,$t2` and the increments' order) went with the header's companion
written off the counter, `for (i = 0, px = x; ...; px += w, i++)` as `for (i = 0; i < 5; i++)` with `x + i * w` at
the uses: 40 -> 0, decomp-repair `for_header_companion_index` (0.6.63). Matched in the app under a per-function
`-mfp32` profile applied from an exact `compiler_experiments` trial (25 under the default `-mfp64`).
