---
title: a 996-instruction collision sweep worked region by region; the score's branch offsets reward any count change
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

The collision sweep of `finding/loop-hoist-threshold-and-frame-layout.md` (nested object-list loops, six triangle
loops calling a helper, a tail that copies a vector out), KMC GCC 2.7.2 `-O2 -G0 -mips3 -mgp32 -mfp32`, the first large
function worked with `regions.py` (19 regions of at least 40 instructions). Saved best 6118 at the start.

The source change, and the measured result before and after (scores, the judge used; `fast_judge`, each new best
submitted with `setup.py try`):

| edit | score |
| --- | --- |
| saved best | 6118 |
| `prove_chain --families all` (m2c_refinement, argument_spill_tail, identical_arm_collapse, allocation_initialization_order) | 5107 |
| the two entry stores in the target's order (`gPlayerActor = NULL;` first) | 5067 |
| pointer locals declared in the order of the target's spill slots (`sp164, sp16C, sp174`): reload gives slots by pseudo number, which follows declaration order | 5057 |
| m2c's object-skip if-chain written as the `switch` its compare tree is (pivot 0x3B, then 0xB / 0x3F), the result in an `s32 keep` set to 1, 0 at a shared `zero_:` label, then copied to the flag; the flag `u16` | 4982 |
| the prototype `func_800487F0(void *, void *, f32)` back (m2c_refinement had unprototyped it: `cvt.d.s; mfc1` pair) | 4767 |
| `func_8004E418` returns `u16`, not `unsigned char` (the target's `andi 0xffff` after the call) | 4737 |
| m2c's caller-save round trip `sp1A0 = var_t2; var_t2 = sp1A0;` and its first store dropped, `var_t2 = temp_t2` folded | 4497 |
| one counter for both loops of the first triangle pair (the app's `loop_counter_reassign`, found by its local pass) | 4387 |
| the shared tail `if (arg0 != NULL) { *(SweepVec *) arg2 = vec28; return 1; } return 0;` with `arg0 = NULL;` before the `goto` that reaches it | 3992 |
| the triangle base `obj->0x34` read in each call argument, not staged in a temp before the call (its `lw` sits after `lhu (\$s3)`: sched1 places a single-set pseudo late) | 3632 |
| `u16 *` back for the two halfword loops: m2c_refinement had made them `char *` with `+= 3`, a 3-byte walk where the code steps 6 | 3332 |
| `(u8) ((word >> 0x11) & 0x3F) & arg7`: the mask then stays on the field (`andi $v0,$v0,0x3f; and $v0,$v0,$t3`), not on the argument | 3322 |
| one counter for the second set's loop pairs (`arm_variable_share`) and m2c's identical `if (var_s6)` arms collapsed (`identical_arm_collapse`), both from `prove_chain --children 120` | 3112 |
| the dead tail kept: `s32 hit = 0;` at the top, `goto block_131;` when nothing was hit, `block_131: if (hit) { *(SweepVec *) arg2 = vec28; return 1; } return 0;` | 2522 |
| the second triangle loop's counter zeroed before its guard and its `do` made a `while` (decomprepair `zero_guard_while`) | 807 |
| `temp_v1_3` merged into `temp_v0` (`disjoint_local_merge`, `prove_chain --children 800`) | 747 |
| the tail reads `u1.b.v.y` back (the target reloads it after the store through `arg4`), and the no-actor branch's `return 1` is a `goto` to the main path's | 340 |

The flag: the target keeps the constant 1 in `$a0` and copies it to `$s2` at each way out. A separate `keep` is
needed, and its width must differ from the flag's: with both `s32` (or both `u16`) global.c gives them one register
and the copies vanish (5727); `s32 keep` with a `u16` flag (or `s16`/`u32`) gives the target's region exactly.
The flag lives on to the first triangle loop's guard (`sltu $v0,$s2,count`: cse uses the register known to be 0), so
it crosses calls and takes `$s2`.

The tail block is dead at run time (the loop ends with `arg0 == NULL`), but the target keeps it; without a store to
`arg0` before the `goto`, cse knows the value and the block is deleted. With the store it is kept, one `sw`/`lw` pair
more than the target (scaffolding: the program is equivalent, the store is not in the target).

Failed experiments that would otherwise be repeated:

- The flag as an `inline` helper with `return`s (any spelling) writes the returns straight into the flag (6142-6627);
  `if (b >= 0xE || b < 0xA)` is folded into one range test (`addiu -0xa; sltiu 4`): write two `if`s.
- One counter for the second and third triangle pairs too: each pair moves the counter to the target's `$s0` but
  grows the frame by 8 (a new caller-save slot), so every saved register's offset differs (k1 4437, k3 3652 with the
  frame off by 0x10). Not kept.
- `var_s0_2 = 0;` above the guard of the first loops (the target's delay slot has `move $s0,$zero`): 3022 alone, but
  the frame grows by 8 and the else loop's guard `sltu` disappears.
- `call_arity` dropping `func_800487F0`'s arguments scored 3627 from 4737: not faithful, it only realigned offsets.

The dead tail, read in the 2.7.2 source (reload1.c and global.c): the target tests a register reload materialises as
`move $t3,$zero`. A pseudo set once to a constant has REG_EQUIV to it; one that lives across calls and gets no hard
register is never stored, and reload puts the constant in its spill register at each use. So the flag is set at the
entry and crosses the loops' calls; cse knows its value only near the entry, where the `arg0 == NULL` path jumps
straight to `return 0`, and the test far away stays (an `arg0 = NULL;` before the `goto` did keep the block but added
a store and a load).

Still open (rows left at 340): the second loop's guard. In the target it keeps `sltu $v0,$v1,count` (the `& 4` value
known zero) and its delay slot holds the counter's `move $s0,$zero`. Written as m2c has it, the slot gets the join's
`li $v0,-1` instead (both loops, two instructions too many); with the zero before the guard (`zero_guard_while`) cse
puts the counter in the compare, combine folds it, and the dead condition-code result leaves a `use` that takes an
8-byte stack slot (frame 0x1e8 for 0x1e0). Hypothesis, from reorg.c: `mark_target_live_regs` finds the join's block
with `find_basic_block`, which scans back to the previous BARRIER (the first loop's closing `j`) and starts from the
second loop's guard block, so the second loop's `move $s0,$zero` makes `$s0` look live at the join and the fallthrough
fill is refused. Why the target escapes it is not found. Also open: `sub.s $f2,$f0,$f2` (local-alloc gives the first
load `$f2`; staging `end.y` in a local fixes the order but moves every `zero` compare to `$f2`, 520).

What is controlled evidence and what is one function:

All of it is one function. A general lesson about the score: scoring version 2 charges every branch whose target
offset differs, so any edit that changes the instruction count above a region shifts every later branch and moves the
score by hundreds (the unprototyped call, the dropped arguments, the hoisted counter all "improved" it while adding
or removing instructions the target doesn't have or has). Judge a large function by `regions.py`'s non-offset rows
(`r`, `o`, `<`, `>`, `|`, `s`) as well as the total. caller-save.c making the save slot is read in the 2.7.2 source;
the declaration-order rule for spill slots is a hypothesis measured here. Library: decomprepair
`caller_save_slot_drop` (0.5.644), `m2cfix.refine_draft` keeps float-parameter prototypes (0.5.643) and element-stepped typed pointers (decomp-repair #1225).
