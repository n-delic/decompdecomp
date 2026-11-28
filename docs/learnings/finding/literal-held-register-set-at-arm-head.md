---
title: A register holding a literal in + and ^ means the constant is set at the head of that arm
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64)
evidence: observation
date: 2026-11-28
---

A 263-instruction N64 object-update routine (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`).
The target had `li $a2,1` in the delay slot of `bnez`, then `addu $v1,$v1,$a2` and `xor $a2,$v0,$a2` in the branch's
target, while the fall-through side loaded its own `li $v0,1` for a compare with 1. That reads like a variable the
compiler did not know to be constant. It was not one.

The source was `if (kind == 0) { .. } else { flag = 1; count += flag; if (two) { flag ^= count < 2; } .. }`: the
assignment is the first statement of the arm, not in front of the `if`. The `li` reaches the delay slot from the head of
the branch's target. The cse dump still adds the constant 1, so the register appears later; that it is
`reload_cse_regs` is a hypothesis, not read from the compiler's source.

Measured with the app's judge run standalone: assignment in front of the `if` 2390, at the arm's head 1880.
decomp-repair `constant_into_reading_arm` offers the move.

The rest of the match: `a = b = c -= 2;` with `c` a byte field computed into a `u8` local that the next test reads
(`chain_value_stage`, 1880 -> 270; keeping the test on the field scored 1395 on the final source), `page = G = 6;`
(`chained_assignment_split`, 270 -> 60), and `p->frames++; p->total += 0x28;` written in the other order
(`independent_statement_swap`, which did not read `x++` before, 60 -> 0).

Failed experiments, on a small copy of the shape, none of which hid the constant from the other side with the right
layout: `do { } while (0)` around the assignment, around either arm or around the whole `if`; `for` and `while` forms of
the test; an inverted test with `break`; the assignment in the condition. Some of them (a one-shot loop with `break`)
produced the `addu` and `xor` and cost the layout or the other side's `li` instead. A `u8` or `s16` flag gave the other
side its `li` but added an extension where the flag is added.

Evidence level: one function; the before and after scores are controlled single edits on it.
