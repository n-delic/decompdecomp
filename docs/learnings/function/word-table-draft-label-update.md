---
title: a raw draft over word tables, 8555 to 0 with edits that are each no better alone
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-28
---

A 214-instruction N64 label update (SN64 GCC 2.8.1): per player, it formats a name into a text buffer when the selected
item or its count changes, then places the label. A clean rewrite from the target's assembly matched at the first judge.
The machine draft (m2c) scored 8555 with the app's standalone judge, and 46 earlier attempts had not closed it. Symptom:
`sll 4` where the target has `sll 2`, `li -1` where it has `li 0xff` / `ori 0xFFFF`, a `lui` of a table address minus 4,
jumps into the other path's tail.

The edits from the draft, cumulative, with the score after each:

- byte offsets added to word-declared symbols written as bytes, `(u8 *)&D`: 7295 (the existing child left `(s32) *(..)`
  reads at byte width; typed too: 6755)
- `s8` members storing `0xFF` and an `s16` member storing `0xFFFF` made unsigned, `M2C_FIELD(&word, u8 *, 3)` as
  `(u8) word`: 5655
- the `goto`s written as their paths (alone it scores 8495), the fourth argument that repeats the index dropped from the
  string format call and the prototype written `()`: 2780
- the word table with `- 1` in its index declared `extern s32 D[];` and indexed: 3650 (the score rises; without it the
  end result is 1160, and `((s32 *) &D)[i]` is the same as bytes)
- the temporaries written back, the stored count read back from its member: 0

What failed: the indexed form cast at each use (`((s32 *) &D)[i - 1]`) folds the `- 4` into the symbol like the byte form.

Controlled: decomp-repair's `m2c_word_table_draft` (with `word_symbol_array_index`, `word_low_byte_read`,
`repeated_index_argument`, `m2c_value_temporaries` and the extended `member_immediate_signedness`) generates a child of
the saved draft that judges 0. The rest is one function.
