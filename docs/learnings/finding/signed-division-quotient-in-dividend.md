---
title: a signed division's quotient kept in the dividend's own variable, with the product inside the division
kind: finding
compiler: SN GCC 2.7.2 (sn-n64-272-0001), -G0 -mips3 -O1 -mgas -meb -mhard-float
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 134-instruction N64 object setup routine
(SN GCC 2.7.2, `-G0 -mips3 -O1`) that scales a table value, `v = 0x1000; if (table[i].flag) v = table[i].scale;`, by
0x10CC / 4096 and keeps the result live across three calls. m2c wrote the division as its sign-bias block with the quotient
in a new local:

    var_s1 = var_s1 * 0x10CC;
    if (var_s1 < 0) { var_s1 += 0xFFF; }
    temp_s1 = var_s1 >> 0xC;      /* var_s1 never read again */

The target keeps the dividend in `$s1`, the product in `$v0` and the quotient back in `$s1`
(`sll $v0,$v0,2; bltzl $v0; addiu $v0,$v0,0xfff; sra $s1,$v0,12`): one variable, with the product a temporary of the
division's expander. The saved best had every other row right; only those 4 register rows were left (score 20).

The source change, and the measured result before and after (scores, the judge used): `var_s1 = var_s1 * 0x10CC / 4096;`
with every later `temp_s1` read as `var_s1` and its declaration dropped: 20 -> 0, recorded matched by the app's judge.
decomp-repair `division_in_place` offers it (and the quotient-in-its-own-local spelling as a second child);
`prove_chain.py --families division_in_place` reaches 0 from the saved best in one step.

Failed experiments that would otherwise be repeated (all from the 20 base, isolated judge):
- quotient kept in its own local, `temp_s1 = var_s1 * 0x10CC / 4096;` (what `signed_division` gives after
  `product_inline`): 50; the dividend then takes `$v1` and the index `$a0`.
- the division in place but as its own statement, `var_s1 = var_s1 * 0x10CC; var_s1 = var_s1 / 4096;`: 310.
- the m2c block kept with only the quotient renamed into `var_s1` (`var_s1 = var_s1 >> 0xC;`): 20 (no change).
- `(var_s1 * 0x10CC) >> 12` in place (no sign bias, unfaithful anyway): 200.
- earlier agents: the product in a separate temp (445), a ternary on a temp (445), an `if` on a temp (385), the product in
  `var_v0` (50), a u32 cast (90).

What is controlled evidence and what is one function: one function, verified by the app's judge; the two failed halves
show the edit only works as one statement with one variable.
