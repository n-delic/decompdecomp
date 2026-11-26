---
title: a glyph printer: one local per shared sum, the mask in place, the byte copy where its source is reused (4122 -> 0)
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 398-instruction N64 glyph printer (default `sn-n64-281` profile). It draws a texture rectangle for a character
and, for an accented character (`ch >= 0xC0`), a second one. The saved best stalled at 4122 after five sessions. Its
131 rows were all saved-register pressure: the target spills the five texture words, keeps `0xE1000000` in `$fp`, and
rematerialises the font address into `$t7` at both texture blocks.

The source change, and the measured result before and after (scores, the judge used):

Each step was judged with fast_judge (the app's judge) and every improvement was recorded with `compile_and_diff`.
1. `special` (the special-glyph flag) also held the quotient `64 / D_800B28C0` (an earlier `merge_integer_lifetimes`
   step). It is split back into `step` (`reused_temp_split`; 4497 alone). The flag then takes `$v1` and the quotient
   `$a2`, as in the target.
2. `x4 + step` and the accent block's `x4 + 64` are written into the dead parameter `c`: `c = x4 + step;` before the
   first rectangle, and `c = x4 + 64;` at the head of the accent block (4122; a new local gives 4137). The target keeps
   both sums in `$s0`. decomp-repair `base_sum_local` (0.6.81).
3. `temporary_hoist`: `temp = (y * 4 + 0x34) & 0xFFF;` (3847).
4. Both texture addresses go into one `u8 *` local, `tex = font + off;`, before each GFX word: 2672. A local for only
   one of them, or one local each, gives 3117. `base_sum_local` with a pointer base (0.6.83). The chain then gave
   `tex = D_800AE210; tex += off;` (`single_use_inline`, `sum_shape`, `statement_slide`: 1971).
5. The mask goes into the local before the word, `tex &= ~7; GFX(.., tex);`, in both blocks: 330. Masking only the
   first block gives 340. The target ANDs into the address's own register, while an operand mask is a new pseudo in
   `$v0`. decomp-repair `mask_in_place`.
6. `ch = c;` (the spilled byte copy) moves from the top to just before `c = D_800B2904[c];`. Its `sb` then sits where
   the target has it: 0, recorded. decomp-repair `copy_sink_to_clobber`.
`prove_chain --families mask_in_place,copy_sink_to_clobber` goes from 1971 to 0. From 4122, base_sum_local,
temporary_hoist and reused_temp_split give 3847 (`--combine 8`). The matched source still has m2c's
`D_80107650 = idx;` before `idx` is set: the target stores the uninitialised `$s2` there.

Failed experiments that would otherwise be repeated:

- Shapes that compile identically, or score worse: `font` assigned at the block heads, `(u32)font + off`,
  `&font[off]`, `off + font`, a `s32 off` local (2917), `y` written into `first` (5372), `xr` hoisted with the flag
  and quotient still merged (4347).
- `prove_chain --families all` over every child (about 900 per step) stalled at 4122, 3847 and 1971 until these
  families existed.

What is controlled evidence and what is one function:

Every step is one function measured with the app's judge. The register readings (the dead parameter's saved
register, the operand mask's new pseudo, where the spill store goes) are hypotheses read from the `-dl -dg` dumps.
