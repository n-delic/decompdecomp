---
title: collision query clean rewrite arm tails and do while
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 1445-instruction N64 collision query (KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): it walks an object's actor
list and then an object list, and for each mesh runs one of two triangle loops (u16 or u8 indices); each triangle
builds a plane, tests the point, and either pushes along the plane normal or towards the closest edge point, then
rotates the correction back, adds it to the caller's position and flags a hit. A cell list walk at the end picks the
next object. 54 calls, frame 0x250 with 17 spilled pseudos (eight-byte slots from 0x178). The saved best (7497) was
m2c scaffolding: a stack union, user pointer locals for every `&local`, a volatile counter, identical-arm scaffolding.

The source change, and the measured result before and after (scores, the judge used):

All scores from the app's judge (`fast_judge.py`, then `compile_and_diff`). A clean typed rewrite, not edits of the
scaffold, was the way in (7497 -> 0):

- No pointer locals for `&local`: the address pseudos come from expand (a struct copy's move_by_pieces makes the
  first) and loop.c hoists them; spill-slot order is pseudo order, so the frame and every slot came right once the
  outer loop was a real `do { } while (self != NULL)` (the hoists then sit before it, as in the target).
- Per-triangle temporaries declared in the triangle loop's block, not shared by the four blocks: a variable set in
  four places is not "birthing" for sched.c, and the vertex sums' argument copies stopped interleaving (19834 -> 11729).
- Separate temporaries for the plane arm and the closest-point arm, and the rotate call written in each arm
  (cross-jumped later; 11729 -> 5597). `pos.y += cy` before `pos.z += cz` (a grid of the store order).
- The callee's real symbol (`sphereContainsPoint`; `Sphere_OverlapsPoint` is not a known symbol: 150 per call).
- One mesh and one triangle variable per half (the second half's were long-lived pseudos shared with the first),
  the mesh assigned after its early tests (the target tests a temporary and copies it in the call's delay slot).
- The tail after the plane/closest `if`/`else` (position update, hit flag, actor mask) written in both arms: the
  position parameter's weighted references 112 -> 208, priority 0.59 -> 1.20 above the actor cursor's 0.97
  (5082 -> 4828). decomp-repair `else_tail_into_arms`.
- A matrix pointer local set after the second rotation call (`mtx = MATB;`): cse turns it into the target's copy of
  the call argument's register, and one more address pseudo spills (4828 -> 2700).
- Loop step order `tri += 3, i++`; the self parameter copied into a local; the second half's triangle pointer as
  `q = mesh->tris; q += j * 3;`; mask tests as `m & lo` (2700 -> 1330).
- The cell-list tail as a goto-structured middle and inner loop with the sphere-fail advance placed before the inner
  loop, so loop.c hoists `(s8) hit`'s shift into the inner loop's preheader (1330 -> 350).
- The object loop as `if (obj) do { ... next: obj = obj->next; } while (obj);` with its `continue`s as `goto next`
  (350 -> 0). Traced in cc1 with lldb: reorg's relax_delay_slots swapped the inner u16 loop's `bnez top; j exit`
  in its first pass and swapped it back in the second, because a rotated `for`'s continue label is followed by
  NOTE_INSN_LOOP_VTOP and mostly_true_jump calls a branch there a likely loop test. decomp-repair
  `for_continue_do_while`.

Failed experiments that would otherwise be repeated:

- Reordering the saved best's pointer-local declarations: slots right, frame still 8 bytes big (a second cursor
  user variable spilled); a 720-order grid of the entry statements compiled to identical code.
- The loop condition spelled five ways (`!(j >= n)`, `n > j`, `++j`, casts): identical; the u16 loop's exit shape
  is reorg's, not the condition's.
- Tail as one do-while with a null check: the `(s8)` return merged with the plain return (no hoisted shift).
- Swapping the order of the u16/u8 arms: 19625.

What is controlled evidence and what is one function:

One function. The reorg swap is controlled (lldb trace of `mostly_true_jump` return values per jump uid, and the
VTOP note after the label in the `-dd` dump). The priority numbers are from the `-dl` dump's `Register N used`
lines. That the original source wrote the arm tails twice is a hypothesis: the cross-jump makes it invisible.
