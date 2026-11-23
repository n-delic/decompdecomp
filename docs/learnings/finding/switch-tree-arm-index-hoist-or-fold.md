---
title: Panel builder: switch trees, per-arm index offsets and the or-fold (13726 -> 9321 recorded)
kind: finding
compiler: KMC GCC 2.7.2 -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 534-instruction N64 panel builder (KMC GCC 2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`): an outer loop over slots, a
three-iteration inner loop with a `switch` of three arms that each call one routine with a slot's object, a buffer
and float-derived coordinates, then a second part creating label objects and an inner loop over the other slots. The
saved best (13726 recorded, 15981 re-judged, 310 differing rows) had the target's shape only in outline: the calls
passed four arguments where the target stores six, the frame was 0x88 where the target's is 0x98. A clean typed
rewrite from the assembly (25457) was edited one named step at a time, each step judged with the app's own judge
(`fast_judge`), and the app recorded 9321 at the end (201 differing rows, 526 of the target's 534 instructions).

The source change, and the measured result before and after (scores, the judge used):

Each row is one edit on the previous source (alone, in that order); the library module that now offers it is named.

| edit | score | family |
| --- | --- | --- |
| clean typed rewrite, structs for the slot record | 25457 | (hand) |
| nested `if` for the two coordinate constants as the saved best has it | 24722 | (hand) |
| the three arms' constant offsets (`+ 0x418`, `0x41C`, `0x420`; `0x438`, `0x448`, `0x458`) with the switch variable (`+ (0x418 + j * 4)`), a `switch (j)` instead of an `if` chain | 18163 -> 16151 | `arm_index_offsets` |
| the narrow callee's fourth parameter `s32`, not `s8` (the target passes the masked index as it is) | 16151 -> 15436 | (signature) |
| `if (A \|\| B) { body }` as `if (!A) { if (!B) { continue; } } { body }` | 15436 -> 14651 | `or_guard_continue` |
| the slot record's table accessed through raw casts (no member flag), the pause test after the store | 14651 -> 14200 | (hand) |
| the compare tree over the slot count as a `switch` (`case 2:`, `case 3: case 4:`) | 14200 -> 13755 | `statement_tree_switch` |
| the second tree, with its default written first | 13755 -> 13464 -> 12296 | `statement_tree_switch` |
| table row 24 bytes wide, not 23; the converted coordinates staged in locals before the address local | 12296 -> 11136 | (hand) |
| the info record's field at 0x12 (the flag read), `if (i == k) continue; if (kind == 5) continue;` | 11136 -> 10386 -> 9321 | `or_guard_continue` |
| the slot count read through `volatile s32 *p` assigned in the loop's test (`i < *(p = &count)`) and in the body's `switch (*p)` | 9321 -> 9151 | `loop_bound_pointer` |

`statement_tree_switch`, `arm_index_offsets`, `or_guard_continue` and `loop_bound_pointer` (decomp-repair) each reproduce their row
from the source before the edit with the exact judge: the library's child equals the hand edit.

What the compiler did (read from `cc1 -dL -dr -dl -dg` of the candidate; the reasons are hypotheses where not quoted from
the source of loop.c):

- A `switch`'s compare chain (`beq 2`, `slti 2`, `slti 5`, then the arms) is what a `switch` with `case 2:` and
  `case 3: case 4:` compiles to; nested `if`s over the same variable give another chain. The arms follow the labels in
  the order they were written, so a `default:` written first is laid out first.
- With the arms' constant offsets, each arm folds `ent + 0x418` into its load (`lw $a0, 0x418($s5)`) and nothing is
  hoisted. With the index in the address (`ent + 0x418 + j * 4`) cse replaces `j` by the arm's value, the address is
  its own pseudo with a use in the arm, and loop.c moves it out of the inner loop (`move-insn savings 1 ... moved`):
  the target's six hoisted addresses, four of them spilled (the frame's 8-byte-stride slots at 0x2C..0x44).
- loop.c moves an invariant when `threshold * savings * lifetime >= insn_count`, and `threshold` starts at
  `1 * (1 + n_non_fixed_regs)` for a loop with a call and goes down by 3 with every insn moved (read in loop.c's
  `move_movables`): constants used in two arms are moved while the first few moves are cheap and are left inline
  after about eight (here 34.0 and the 0xA60 flag word stayed, 16.0 moved; the exact count depends on the loop's size).
- `A || B` of two plain comparisons becomes one branch on `xori`/`sltu`/`or`; two nested `if`s give two branches, which
  also keeps the flag's load after the loop's first store (hypothesis: fold-const turns TRUTH_ORIF into TRUTH_OR for
  operands without side effects).

Failed experiments that would otherwise be repeated:

- `volatile` on the two spilled halfword locals (15795 and 20560): the sign extension no longer folds.
- the global count through a pointer local assigned before the loop (`s32 *pc = &count;`, volatile or not, 9946-10536): cse folds it back, worse than the plain global; only the volatile pointer assigned inside the loop's test keeps the address in a register (the target's `lui $a1; addiu $a1; lw ($a1)`).
- `u16`/`s16`/`u32`/`s32` for the two halfword locals, `s32` for the loop counters, `u32` kind: all worse.
- the case arms in another order (five permutations, 10931 - 14855 at best against 9321).
- spellings of `2.0f * cy - 34.0f` (`cy * 2`, `cy + cy`, an int `34`): identical code.

What is controlled evidence and what is one function:

The three families' rows are controlled (the library child reproduces the hand edit, measured with the judge). Everything
else is one function. Unsolved here: the target keeps both halfword locals in stack slots and gives their registers to
two hoisted addresses (the loop variable ends in `$fp`); the candidate keeps one of them in `$fp`. The two locals'
allocation priorities (14 refs / 238 live, 0.176) are above the hoisted addresses' (about 0.09) in every spelling tried;
`i = 0` sits in the call's delay slot in the target and after the loop's first test here; and 34.0 is hoisted into an extra callee-saved FP register in the candidate (frame 0xA0, target 0x98).
