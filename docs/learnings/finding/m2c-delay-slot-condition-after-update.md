---
title: m2c tests the updated register when a delay slot steps what the condition read
kind: finding
compiler: m2c 708d2d2c
evidence: observation
date: 2026-11-27
---

m2c (pinned 708d2d2c), `-t mips-gcc-c --valid-syntax`. For

    lbu $v0, 0($s0); beq $v0, $v1, done; addiu $s0, $s0, 1

in a loop it wrote `var_s0 += 1; if (*var_s0 != 0x2E)`: the byte after, where the target tests the byte before.
`slti $v0, $s0, 0xA; bnez $v0, loop; addiu $s0, $s0, 1` came out as `var_s0 += 1; } while (var_s0 < 0xA);`. With the
step written above the branch (same meaning for a plain branch) m2c keeps the old value in a temporary. The app now
gives m2c that order (`decompnet/m2c_delay_slot.py`). The explanation (the condition is recorded before the slot's
write and is not re-checked) is read from m2c's output and its translate.py, as a hypothesis.

Controlled: synthetic loops and one real function's draft.
