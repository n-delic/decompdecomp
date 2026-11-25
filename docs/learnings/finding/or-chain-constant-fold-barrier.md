---
title: A constant in a | chain stays where it is only when a non-constant term follows it
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

The function was a 638-instruction N64 texture loader (`gDPLoadTextureBlock` / `_4b` and `gDPLoadTLUT` per texture
depth), compiled with SN GCC 2.8.1 using the project's default flags. Each load tile's `gDPSetTile` word 1 is
`7 << 24 | cmt << 18 | cms << 8` with `cmt == cms == cm` (a local, `$s4`). The target computes it as
`((cm & 3) << 18 | 0x07000000) | (cm & 3) << 8`: one `andi`, then `sll 18`, `or` with the constant register, then
`sll 8` and the final `or`. The candidate produced `(cm << 18) | ((cm << 8) | 0x07000000)`. The instructions were the
same but in a different order and association, so the stores were scheduled differently (1995).

The source change, and the measured result before and after (scores, the judge used):

- Every spelling of the tree with only `|` compiled to the same instructions: operand order, parentheses, `(s32)`
  casts on one term, `(u16) 0 |`, and zero terms present or absent. The score stayed at 1995 each time. fold's
  `associate:` code (fold-const.c) splits `(X | CONST) | Y` into `(X | Y) | CONST` whenever the left operand is the
  same code with a constant operand. So the tree's constant always moves outward. (This is a hypothesis from reading
  the code: the 2.8.1 source used was gcc-2.8.1-pm's.)
- Writing the `cms` term with `+` instead of `|` gave the target's tree. The score fell to 875, and only the opcode
  rows (`addu` for `or`) remained.
- A block-local zero passed for the load tile's G_TX_NOMASK/G_TX_NOLOD arguments
  (`{ s32 _z = 0; gDPSetTile(..., cmt, _z, _z, cms, _z, _z); }`) gave the target's tree with `or`: the score was 75.
  A non-constant term between the constant and `cms << 8` stops fold associating. cse then folds `_z` to 0 at RTL
  level, so no instruction is left for it. Putting the zero only at `maskt` also gave 75, as did putting it at
  `palette` as well.
- Making `gDPSetTile` a `static inline` function gave the same tree, because the parameters are RTL constants. Its
  scheduling was worse, though: 435.

Failed experiments that would otherwise be repeated:

- All 720 orders of the function's six entry statements compiled identically.
- `u8`, `s8`, `u16`, `s16` and `u32` for `mask`, `cm` or the texture type scored 75 or worse (1050 to 5175).
- `do { } while (0)` around `mask = 0` or around block macros was worse (155 to 8590).
- The last rows ($s5/$s6 swapped) are a global.c priority. From `cc1n64 -dl -dg`: the type pseudo has 5 refs over 466
  insns (priority 0.0215) and the mask pseudo has 6 refs over 1088 (0.011). The target needs the mask to outrank the
  type. No spelling tried changed either pseudo's refs.

What is controlled evidence and what is one function:

All of this comes from one function. The fold rule is consistent with every spelling tried. It should hold for any
`|` / `+` / `&` chain whose target keeps a constant inside the chain under GCC 2.x. Watch for it when the GBI macros'
`_SHIFTL(0, ...)` terms are literal zeros in a load tile.
