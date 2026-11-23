---
title: Statement-macro vertex loops, a hand-built frame and a stepped cursor in a display-list builder (3370 -> 0)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like: a 559-instruction N64 display-list builder (KMC GCC 2.7.2). One loop over records of a
byte-coded model: four vertex loops (one, two, three bytes per component, and a 16-bit pair for each of x, y, z) that
call a private vertex writer, then close the command with a packed header word; two triangle loops. The saved best (3370,
a search's) wrote the four vertex loops as one statement macro, kept a cursor `q` stepped beside the counter, held two
parameters in a hand-built stack struct (a `volatile` word copy of one, a byte copy of the other, 7 bytes of padding and a
32-byte phantom array), used function-wide `u8 x, y, z` and a function-wide `GfxPair *gfx`.

The source change, and the measured result (the app's isolated judge, each step on the previous one):

| step | score | what the permuter needs |
|---|---|---|
| macros written out (identical program) | 3370 | `statement_macro_expand` |
| triangle loops: the second pointer `q` was loop.c's own giv; plain `p[1]`, `p[2]`, one `p += 3` | 3370 -> 3070 | `cursor_lockstep_fold`, `local_web_split` reach it |
| `gfx = gGfxPtr++` as `gfx = gGfxPtr; gGfxPtr = gfx + 1;` and `gfx` local to each arm | 3070 -> 2510 | `local_web_split` |
| `n` split per tail | 2510 -> 2375 | `region_local_split` |
| vertex cursor `q` fixed, indexed `q[i * 3 + k]` (loop.c then makes `move $s0,$v1` itself) | 2375 -> 1970 | `cursor_base_index_loop` (new) |
| hand-built frame replaced by plain locals: the parameter read directly, a `u8` local for the byte, no padding or phantom array | 1970 -> 996 | `frame_struct_unwrap` (new) |
| `i = 0; if (p[1]) do {} while (i < p[1])` as `for (i = 0; i < p[1]; i++)` (the copied entry test leaves the frame its 0x20 bytes: the frame size and every spill slot matched with no phantom) | 996 -> 950 | `cursor_base_index_for` (new) |
| `u8 x, y, z` declared in the two arms that set them first | 950 -> 805 | `arm_scratch_block_locals` (new; two single-arm children compose) |
| the header word as `0x01000000 \| (n << 12) \| ((n & 127) << 1)` (constant first; the four identical tails are one step) | 805 -> 365 | `or_constant_lead` (new) |
| the 16-bit pair of the 2-byte arm as `hi`/`lo` locals and the 1-byte arm as one `u32` read through `((u8 *) q)[i]` | 365 -> 55 | `volatile_read_subset_unpin`, `shifted_byte_temp_narrow` (new) |
| `for (...; p += 3, i++)` (loop.c steps the giv right after its biv) | 55 -> 35 | `for_step_order` (new) |
| `u16` for the high byte held in a word local and shifted in place | 35 -> 20 | `shifted_byte_temp_narrow` (new) |
| the low byte of each pair through a plain cast (odd offsets), the high byte left volatile | 20 -> 0 | `volatile_read_subset_unpin` (new) |

The mechanisms (read in the compiler's source, `cc1 -dS -dR -dl -dg` and the toolchains' `cse.c`, `combine.c`, `optabs.c`,
`loop.c`, `reload1.c`; each is one function's evidence, the rules are hypotheses until another function shows them):

- A stepped cursor is a basic induction variable and stays the loop's address register; an invariant base indexed by
  `i * S + k` is a derived address, which loop.c gives a register of its own started from the base with a `move` and steps by
  `S` at the loop's end. The target's `addiu $v1,$s1,2` / `move $s0,$v1` pair is that.
- A parameter in a hand-built struct is a frame-pointer-relative `volatile` read for the first scheduler, which cannot
  order it against the outgoing-argument stores (stack-pointer based): the reload stays behind them. A spilled parameter
  is reloaded after the first scheduler and the second orders it freely (`lw $a3,0x2c($sp)` first in the target). The
  spill slots (`0x2c`, and `0x37` for a byte spilled in a word slot, big-endian) land where the target has them with no
  phantom: reload gives a slot to every pseudo it still counts, and each `for` loop's copied entry test (jump.c) leaves
  stale pseudos that take slots, which is what filled the missing 0x20 bytes.
- The operand order of a commutative add of two registers: `expand_binop` puts the destination's own operand first, and
  with a `volatile` byte pair both byte loads stay `(zero_extend (reg:QI))` of volatile loads, which comes out with the low
  byte first whichever way the C is written; one ordinary load is enough to get the high byte first.
- `x = y = z = q[i]` through a QImode chain gives one `andi 0xff` per copy; a `u32` local read once from a non-volatile
  byte is a single `lbu` with three uses.

Failed experiments that would otherwise be repeated: random orders of the 12 statements of the pair loop (2000 tried,
none better than the hand order); declaration order and the spelling order of `hi + lo` (no effect: the volatile pair
decides); `volatile` removed from the whole cursor (575, other loops lose their load order); typing the byte pairs
`s16`/`u8`/`s8` (worse); `u32` instead of `u16` for the shifted high byte of the first and third pair (no change).

What is controlled evidence and what is one function: every score above is the isolated judge on this function, and the
last step was confirmed in the app (`compile_and_diff` byte-identical, linked bytes verified). The families were written
from the steps and each reproduces its step on the source it starts from (library PRs 1380 and 1385).
