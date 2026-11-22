---
title: sched1 birthing priority and store order
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: verified
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

A 94-instruction 4x4 rotation-matrix builder (three sin/cos pairs, fourteen float products, sixteen word stores through
one pointer; `-O2 -G0 -mips3 -mgp32 -mfp32`, KMC GCC 2.7.2). The saved m2c best stood at 160 with only register and
order rows left: the first four products were computed in the wrong order, and so the registers followed.

The source change, and the measured result before and after (scores, the judge used):

Project judge, one function. 160 -> 15 -> 0 in two steps, each a decomp-repair family now:
1. The stores in order of their offsets, the zero words, `-sy` and the copied parameters kept ahead of the products as
   written (`offset_store_order_values`, 160 -> 15; every store sorted by offset was 35, the locals just before their
   store 80). m2c writes the fields in the order its expressions needed them; the original wrote the matrix by row.
2. The m2c union view of one word store (`{ union { f32 f; int i; } v; v.f = x; *(int *)((char *)p + 4) = v.i; }`)
   written as the plain float store `*(f32 *)((char *)p + 4) = x;`, with the product two stores share named once
   (`a = sx * sy;` used in both; `union_view_store_plain_shared`, 15 -> 0). Neither is better alone (the plain store 15,
   the named product 15). The plain float store still compiles to `mfc1 $v0,$f14; sw $v0,4($s0)` in the target: the
   word move is the allocator's choice for that product, not a pun in the source.

Why (read from the compiler's source, sched.c): the first scheduler works backwards and ranks the ready insns by
priority. `adjust_priority` raises the priority of an insn that `birthing_insn_p` says makes a register live (its
destination is a plain `REG`, live at that point, set exactly once) to the highest value, so all such products are
placed last in the backward pass and come out in the order of the original insns (`INSN_LUID`). A product written into a
union is `(set (subreg:SF (reg:SI v)) ...)`, never a plain `REG`, so it is not raised, is placed first by the backward
pass and comes out ahead of every other product; so is a product whose pseudo is set twice. The target had that product
fourth, in the order of its store (offset 4), which is the plain float store. Read the trace with `passes.py
--sched-verbose` ("ready list at T-N", `7f000001` is the raised priority) and map insn numbers with the `-dc`/`-dS`
dumps.

Failed experiments that would otherwise be repeated:

- Hoisting the product temporaries to the top, moving every single statement, 2-move beams and random walks around the
  union form all stayed at 10 for an hour: with the union in place no statement order puts its product anywhere but
  first. Unions are the lever, not the order.
- Making the other products non-raised too (a second set by accumulating in place: `a *= cz`) reproduced the target's
  first four products but disturbed the allocation (score 2245).
- A union at function scope, swapped member order, `u32`/`s32` casts: all the same 10, `union` types are always an
  integer mode (stor-layout.c `layout_type`), so the member order cannot give a float-mode pseudo.

What is controlled evidence and what is one function:

The two-step chain from the saved best was run with `prove_chain.py` and the judge (160 -> 15 -> 0, exact bytes in the
app). The scheduler rule is read from the compiler's source and agrees with the order of the products in the traces of
three variants. It is one function; the plain float store matching an `mfc1/sw` target is that function's observation.
