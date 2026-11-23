---
title: display list local cursor macros
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 369-instruction N64 sprite-tile builder, KMC gcc 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`: sixteen arguments (the
caller's display-list cursor as the first, `Gfx **`), a loop over rows and one over columns, and per tile either a
seven-command texture load (set image, set tile, load sync, load tile, pipe sync, set tile, set tile size) in one of
two arms and a three-command texture rectangle in one of two more. The saved best stood at 10891 after 340 attempts,
written as m2c prints it: the cursor `var_t1 = *arg0`, a second pointer `var_t0 = var_t1 - 4` stepped by 8 for every
command, each word `M2C_FIELD(temp, s32 *, -4)` / `(prev, s32 *, 8)`, the cursor advanced by `+ 0x38` and `+ 0x18`.

The source change, and the measured result before and after (scores, the judge used):

That shape is not the game's: it is what the loop pass makes of a cursor that is one local advanced with `gfx++` per
command. gcc reduces the address `gfx + 0` / `gfx + 4` (every `Gfx *_g = gfx++; _g->w0 = ...; _g->w1 = ...;`) to a
base kept at `cursor - 4` and stepped by 8 after each command, leaving the cursor itself to be advanced by whole arms
(`+= 0x38`, `+= 0x18`); the two registers, the `sw -4($t0) / sw 0($t0)` pairs and the `addiu $t0, $t0, 8` per
command all come back by themselves from the macros. Writing the commands as the SDK's macros on one cursor
(`gDPSetTextureImage(gfx++, ...)`, `gDPSetTile`, `gDPLoadTile`, `gSPTextureRectangle`, ...) and then, step by step
(`fast_judge`, the app's own judge, isolated score; frame size 0x58 reached at 7705):

- one cursor, commands as macros on `Gfx *_g = gfx++` (a hand-written `g->w0 = ..; g->w1 = ..; g++` without the `_g`
  copy compiled to a single register and 21849): 12904
- the second set-tile of the 8-bit arm reuses the first one's word (the draft's `temp_v1`), `x1`/`y1` signed so the
  line count shifts with `sra`: 11160 -> 10560
- the real macro expansions (`_SHIFTL` masks, field order) and the texture rectangle written whole in each arm (m2c
  hoisted its first word past the join): 7705, and the loop's four hoisted constants (set-tile word, tile word,
  load-sync, load-tile) instead of six
- the rectangle's operands as locals before the hi-res branch (`a * 4`, `b * 4`, `s * 4`, `t * 4`), so the loop pass
  does not reduce `s * 4` to a register of its own: 7705 -> 1565
- the four clamps in the target's order (x0, x1, y0, y1, each clamp right after its operand): 1565 -> 735
- `b = b * 4` and `a = a * 4` in place (the target overwrites `$v1`/`$a0`, a fresh local took `$a1`): 735 -> 550
- `t = arg15` in the outer `for` header (it moves where the scheduler puts the float truncation): 550 -> 490
- the format field passed as the argument itself, not as a masked local (the macro masks it): 490 -> 0

Why the hoisted constants matter: `loop.c` moves a constant load only while `threshold * savings * lifetime >=
insn_count`, and each move lowers the threshold by 3 (move_movables); six hoisted constants took the saved registers
the target keeps for `lastX`/`lastY`, four did not.

Failed experiments that would otherwise be repeated:

- `Gfx *g` with `g->w0 = ..; g->w1 = ..; g++` (no copy): one pointer, offsets 0/4, 21849.
- `_g->w1` before `_g->w0` in the macro: 18178.
- Natural `if (x0 < 0) x0 = 0;` clamps on the plain m2c draft made it worse (13135 -> 14071): the clamps only pay off
  together with the statement order, the signed `x1`/`y1` and the in-place rectangle operands.
- A search on the raw two-pointer form (the saved best): 340 attempts, never below 10891.

What is controlled evidence and what is one function:

One function, verified byte-identical by the app's judge. The two library families that came out of it are
`gbi_loop_cursor` (the cursor and macro step, with a second child that also writes back the hoisted invariants) and
`hoisted_invariant_inline` (decomp-repair): on the m2c draft 30766 -> 16710 -> 13135. The four smaller steps after the
macros (clamp order, in-place operands, the `for` header, signed `x1`/`y1`) have no family yet.
