---
title: A table pointer whose slot sum sits too late, staged through the pointer itself
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

Two N64 triangle-table walkers (a 438-instruction ray test and a 761-instruction ground probe), each loop iteration
reading a model index and then two tables of pointers with it: `vtx = VERTS[o->model];` and
`tri = &FACES[o->model][entry.face];` (or `FACES[m] + k`). The targets compute both slot sums right after the shared
`m * 4` (`sll $v1, $v1, 2; addu $a0, $v1, $t0; addu $v1, $v1, $v0`) and only then load the face index; every plain
spelling put one of the slot sums just before its own load (several insns later), and the statement order also decided
which table's address loop.c hoists (the first `la` in RTL order).

The source change, and the measured result before and after (scores, the judge used):

- The pointer written in two steps through the same local:
  `tri = (Tri *) &FACES[o->model]; tri = &(*(Tri **) tri)[entry.face];` (vtx first, unchanged): 210 -> 55 on the ray
  test (decompdecomp's standalone judge under `-mfp32`; the app recorded 3439 -> 3329 under the project's `-mfp64`
  profile). The loop head's instruction order is then the target's; two `la` registers and the slot sums' registers
  are left (the hoisted table is the vertex table where the target hoists the face table).
- On the ground probe, the face pointer first and the vertex pointer staged after it
  (`verts = (MVtx16 *) &VERTS[m]; verts = *(MVtx16 **) verts;`): the loop head's schedule is the target's exactly; the
  score is 195 (from 90) because the staging adds two references to `verts` and global.c's priority
  (`floor_log2(refs) * refs / live`) then ranks it above `tri` (30/341 against 28/340), swapping `$s3`/`$s4`.
- Why (read in gcc-2.8.1 sched.c, `birthing_insn_p`): the first scheduler raises an insn whose destination is set exactly
  once to the maximum priority when it becomes ready; scheduling backwards, that puts the slot sum right before its
  load. A second set of the same local removes the boost and the sum stays next to the other table's. That this is the
  deciding rule here is a hypothesis the two measurements agree with.
- decomp-repair `table_slot_self_stage` (0.6.49) offers both children (staged in place, staged after the next
  statement).

Failed experiments that would otherwise be repeated:

- A separate slot pointer (`Vtx16 **vp = &VERTS[m]; v = *vp;`, block or function scope, with or without a dead
  initialisation) is set once and boosted again: 530, or 4752 with a frame change.
- Staging both pointers changes which table loop.c hoists (5032, 2968); `tri = T[m]; vtx = ..; tri += k` raises `tri`
  to 32 references (floor_log2 5) and above the record pointer (200); `do { } while (0)` around a statement that names
  `tri` adds the weight but breaks the schedule (390-1130).

What is controlled evidence and what is one function:

The family's child reproduces the hand step exactly on both functions (55 and 195). The scheduler rule is from the
compiler source; the rest is two functions of one game.
