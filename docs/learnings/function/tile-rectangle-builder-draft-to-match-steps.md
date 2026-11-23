---
title: tile rectangle builder draft to match steps
kind: function
compiler: KMC GCC 2.7.2 -O2 -G0 -mips3
evidence: controlled
date: 2026-11-23
---

A 315-instruction N64 display-list builder (KMC GCC 2.7.2, `-O2 -G0 -mips3`): a double loop over image tiles, each tile a
seven-command texture load in one of two arms (a clamped `x0`/`y0`/`x1`/`y1` rectangle) and a three-command texture
rectangle in one of two more, one cursor `Gfx *` stored back at the end. Its saved best was 19139 (a draft with
uninitialised pointer temps, a volatile frame and search scaffolding on every line), 334 differing rows, a frame of
0x88 against the target's 0x58. A matched neighbour (a 369-instruction builder of the same kind, its ratio scaling aside)
had the shape; a rewrite from it matched in a few steps (0).

The draft is not the saved best: the library's `gbi_loop_cursor` fires on the m2c draft (the two pointer chains become the
SDK macros on one cursor, 32141 -> 9894) and on nothing a search had already edited. From that child the steps to 0, each
measured with the exact judge by undoing it on the matched source (a controlled audit: no existing family recovered any of
them) and each now a family of decomp-repair:

| step | alone | family |
|---|---|---|
| `(f32) var` of a `u32` counter -> `(f32) (s32) var` (unsigned conversion has a bias branch) | 8832 -> 6765 | `unsigned_counter_float_cast` |
| m2c's `a & ((s32) ~a >> 0x1F)` -> `if (a < 0) a = 0;` (the pair `t = a - 1;` + mask is one assignment) | 6765 -> 6000 (cfg_integer_lifetimes and dead_scratch_reuse reach 485 from the idiom) | `clamp_zero_idiom` |
| `+ D_800D2EF0` (an `extern f64` in `.data`, loaded `lui $at; ldc1`) -> `+ 0.5` | 5980 -> 855 | `double_literal_global` (needs the bytes: `app/float_literal_words.py` reads the eight bytes of an `ldc1` constant, like the four of an `lwc1` one) |
| `((s32) (x + 8) >> 3)` lost its cast when the macro took the field's mask (`sra` -> `srl`) | 855 -> 655 | `gbi_macro_commands._strip` kept the cast of a shifted operand; `signed_shift_operand` restores it in a best that lost it |
| two values of the outer counters only the inner loop reads (`temp_ft1`, `temp_t9`): one assigned at the top of the loop, one written at its use | 7713 -> 0 for that mix (6115 with both written at the uses) | `inner_loop_invariant_inline`, every mix of the two forms as one child |
| the high-resolution arm's `t * 8` and the other arm's `t * 4`: stage `t * 4` above the `if` | 655 -> 185 | `arm_operand_pre_stage` |
| `temp = a * 4;` then reads of `temp` with `a` dead -> `a = a * 4;` | 185 -> 0 (495 alone from 655) | `scaled_copy_in_place` (disjoint_local_merge reaches 40 from it) |

The last two are each better than their parent alone and exact together. Every step keeps the program faithful: no extra
stores, no phantom locals (the staged operand is a real local the game plausibly had), the literal is the value the data
holds.

Failed or neutral: `for` against guarded `do`/`while` loops and block against function-scope locals compile identically
here (a hint that the loop pass normalises them); the `D_800D2EF0` spelling is only a register-order cost, never a
different instruction count.

Controlled: the audit undoes one step on the 0 and asks every family of the library at the time (decomp-repair 0.5.827).
One function: the order the steps pay off in and the sizes above.
