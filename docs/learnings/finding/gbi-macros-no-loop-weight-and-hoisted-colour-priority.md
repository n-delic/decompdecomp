---
title: Real GBI macros give no loop weight; a hoisted colour literal ranks by the local it is written through
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 (-mfp32 probed)
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

Three N64 functions of one game, all SN64 GCC 2.8.1 (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp64`; the targets use only even FPRs, so every score below is also given with `-mfp32`, probed with
`fast_judge.py --flags=-mfp32`, never applied): a 516-instruction textured particle quad builder (texture load, four
vertices per particle, `G_VTX` + `G_QUAD` per particle), a 544-instruction ribbon builder (three vertices per point,
then two `G_QUAD`s per segment with the flag argument a variable), and a 537-instruction collision ray test (no
display list at all: plane and vector helpers on a contact list, the sibling of an already matched ground probe).

The source change, and the measured result before and after (scores, the judge used):

- **The SDK macros are not a fence.** libultra's `gbi.h` (2.0I and 2.0K copies) has no `do { } while (0)` in
  `gDPLoadTextureBlock`, `gDPLoadBlock`, `gDPSetTile`, `gSPVertex` or `gSP1Quadrangle`: each is a bare `{ Gfx *_g =
  (Gfx *)(pkt); ... }` block. The quad builder's faithful best rewritten with the real macros (F3DEX_GBI_2 forms,
  `pkt` = `D_80177E18++`, `_SHIFTL` with its `unsigned int` casts) scored exactly what the hand-written `G(w0, w1)`
  pairs scored: 3092 and 2847 (`-mfp32`). The `_DW` wrapper some decomp `gbi.h` files carry is a decompiler project's,
  not the SDK's. Use the macros for readability; they do not change allocation here.
- **The quad builder's saved 2617 is scaffolding**: it is the 3092 source with `extern Camera *volatile D_800B3960;`
  (the app's reloaded-global pass) plus two unused frame arrays. The texture block is the real `gDPLoadTextureBlock`
  (16b and 32b RGBA, clamp, then `gDPSetTextureLUT(G_TT_NONE)` in each case) and the tail is `gSPVertex(gfx++, v, 4,
  0); gSP1Quadrangle(gfx++, 0, 1, 2, 3, 0);`. A fresh rewrite from the assembly with the macros and `v[0..3]` indexing
  scored 14513 (`-mfp32`); the gap is allocation (`alpha` copied from a clamped temporary, `size` and `-size` spilled,
  and the rotated corner values computed through copies that the target loads between the `coss`/`sins` calls).
- **A colour literal stored seven times in the ribbon builder's vertex loop**: written as a literal, cse merges it into
  one pseudo, loop.c hoists it as a move-insn with a REG_EQUAL note, and local-alloc's `update_equiv_regs` doubles its
  live length (REG_EQUIV), so it ranks last (`t0`, 3150 `-mfp32`). Written through a local the later quad loop assigns
  anyway (`c = 0xFF;` at the loop top, every 0xFF store `= c`), the pseudo is set more than once, escapes the
  doubling, is still hoisted ("global move-insn"), and the compiled instructions become exactly the target's: only
  registers differ (2315; through `e`, 2445). The target's allocation needs the colour holder ranked above the
  `D_800CD350` cursor giv (23 refs over 61 insns, 1.508); every local we tried (`a` 1.491, `e` 1.120, `c`, `nv`,
  `last`) ranks below it, so the saved 530 (an in-loop multi-set local for one vertex's channel) stays the best.
- **The ray test: `loop_table_slot_pointers` did not fire on its plain form.** The loop body declares
  `Actor *o = LIST[i].obj; s32 type = o->type; Vert *verts = VERTS[type]; Tri *tri = &TRIS[type][LIST[i].tri];`; with
  the declarations split by hand the family's `top` child put the table pointer above the block's declarations and
  never compiled. decomp-repair 0.6.89 (PR #1797) splits them and places the pointer after the declarations: 5029 ->
  4689, `-mfp32` 3629 -> 3109. A bounded `prove_chain --fp-flip --families all --children 60` from there reached 885,
  of which `goto_shared_tail` (the hit block written in both places) and `inner_loop_invariant_inline` (the `0.0f` /
  `0.0` locals back to literals) are faithful (2449 `-mfp32`, 4154 default); the rest came from `statement_scope_weight`
  (a `do { } while (0)` around the whole hit `if`) and `loop_weight_scope` (a triple fence around `i = 0`), scaffolding.

Failed experiments that would otherwise be repeated:

- Quad builder: `s16`/`s32`/`u8`/`s8` alpha (the `s32` gives the target's `sra` but scores 3127 against 2847).
- Ribbon builder: the colour local set before the loop as well as at its top (same 2315); a user pointer for the
  `D_800CD350` walk (4190); the colour set just before the first channel store (2445, the same allocation).
- Ray test: the matched sibling's whole loop shape (`for` with `continue`s; 6236, worse than the rotated
  `i = 0; if (i < N) do { ... goto next; ... next: i++; } while (i < N)` form); reusing `i` for the box size (3289).

What is controlled evidence and what is one function:

The macro result is a controlled comparison (same source, macros against word pairs, identical scores). The table-slot
fix is one function, measured with the app's judge, and has unit tests in decomp-repair. The colour-holder ranking is
read from cc1's `.greg` dumps (`passes.py`) on one function; the REG_EQUIV doubling is GCC 2.8.1's `update_equiv_regs`
as read from its dumps, not confirmed in the compiler source (hypothesis for the mechanism, observation for the scores).
