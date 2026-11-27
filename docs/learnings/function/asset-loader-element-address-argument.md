---
title: An element pointer handed to the next call, written as the element (500 to 0)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

An N64 loader of 229 instructions: it reads an asset directory from the cartridge by DMA, appends an extension to a
file name, decompresses the entry into a heap and registers it in a table of 0x18-byte records. A matched sibling loader
in the same program (77% similar) gave most of the C: the first adapted candidate scored 500, against 10552 for the
machine draft. Scores are the app's judge's.

The last step. `obj = &table[count]; obj->data = dst; if (load(obj, 0) != 0)` scored 500: the base's `lui`/`addiu` and
the counter's `lui` came in another order and registers. `table[count].data = dst; if (load(&table[count], 0) != 0)` is
0. Either one alone through the pointer (`obj->data` with `load(&table[count], 0)`, or the reverse) stayed at 500.
`element_pointer_direct_index` took member uses only; it now also takes a pointer passed to the first call after it is
set.

The other edits, each reverted alone from the exact source (so the score is what that edit is worth):

| exact source's form | reverted form | score |
| --- | --- | --- |
| `*p++ = 'P'; p[0] = 'A'; p[1] = 'K';` | `p[0]`, `p[1]`, `p[2]` stores | 710 |
| `while (*p) if (*p++ == '.') goto found; p++; found:` | m2c's goto loop | 530 |
| `u8 name[0x20];` | `u8 name;` and `&name` | 132 |
| `n = *w; n = SWAP32(n); n = ALIGN16(n);` | one nested expression | 125 |
| `n += 0xF; n >>= 4; n <<= 4;` | `n = ALIGN16(n);` | 15 |

`store_run_post_increment`, `stack_object_extent` (byte scalars also read) and `round_up_steps` cover the first, third
and last rows. The nested byte swap is `endian_word_load`'s ground. No family writes the scan loop: m2c's draft of it
also read the byte after the increment where the target compares the one before, so it was rewritten by hand.

Failed experiments: `(obj = &table[count])->data = dst;` and `obj = table + count;` both stayed at 500.
Dropping the sibling's extra copies of the two buffer pointers and the second (unused) parameter both stayed at 0.

One function: the reason GCC 2.8.1 wants these shapes is not read from the compiler, so it is a hypothesis.
