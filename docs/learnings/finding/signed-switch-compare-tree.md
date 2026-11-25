---
title: A small switch on a u32 state word is a beq chain; on s32 a slti compare tree
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 568-instruction N64 NPC state machine (SN GCC 2.8.1, the flags above). An outer switch on `obj->x90` has two jump
tables, and the outer case 3 holds a four-case inner switch on `obj->x8C` (0, 1, 2, 3). The target dispatches the
inner switch as a compare tree: `lw $v1, 0x8c($s1); li $a0, 1; beq $v1, $a0; slti $v0, $v1, 2; beql $v0, $zero, ...;
beql $v1, $zero, end` with case 0's `sw $a0, 0x8c($s1)` (state = 1) in the delay slot.

The source change, and the measured result before and after (scores, the judge used):

m2c typed the state word `u32`. With an unsigned index, GCC emits a chain (`beq 1; beqz; beq 2; beq 3`) and case 0's store
reuses the dispatch's `li $v0, 1`. That `sw $v0` sits right after a label, so `find_cross_jump` (jump.c, minimum
lowered by one when a label precedes the matched run) merges every arm's single `sw $v0, 0x8c` state store into it,
and each later arm moves. Typing the field `s32`, or writing `switch ((s32) obj->x8C)` on that one switch, gives the
target's tree: 2835 -> 40, measured with the app's judge (`_judge_with`) under the function's `-mfp32` profile, with
the relocations decompdecomp #3608 and #3610 restore. Then a local reuse made it exact. decomp-repair `signed_switch`
(0.6.45) offers the cast for each switch whose labels are plain non-negative numbers, when the target has an `slti`.

Failed experiments that would otherwise be repeated: `return` instead of `break` after the state stores (no change:
both jump to the same label); moving the outer case 3 before case 2 (6240) or the inner case 0 last (4105). Under
sn-n64-272-0006 the same source scored 3455, so it is not a profile question.

What is controlled evidence and what is one function: one function. The cross-jump rule is read from GCC 2.8.1's jump.c
(the `--minimum` on a label in `find_cross_jump`, and `minimum` 2 for jump against jump). That signedness picks the chain
or the tree is observed, not read from stmt.c.
