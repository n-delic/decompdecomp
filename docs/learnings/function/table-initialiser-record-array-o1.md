---
title: a table initialiser that is a record array built at -O1
kind: function
compiler: gcc-2.7.2-kmc -O1
evidence: controlled
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 53-instruction N64 initialiser: two 0x28-byte records filled with halfword constants through one register that holds
the table's address, then a `u8` loop zeroing the first field of the next two records with `lui $at` indexing. The
project builds with gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`; this function is `-O1`.

The source change, and the measured result before and after (scores, the judge used):
The machine draft (casts and offsets off one symbol, a pointer alias) scored 7075 under `-O1` and `-O2`; the saved C,
full of fences, 4155 (`-O2`) and 910 (`-O1`). The record array declared at the table's address, written with plain
member stores (`Row t[]; t[0].a = 1; ...; for (i = 2; i < 4; i++) t[i].a = 0;`) was exact under `-O1` and 7255 under
`-O2`. The app's judge. A padded wrapper record at the lower symbol was exact too; a pointer local (6260) and a cast of
a `char[]` symbol (3680) were not. `s16` and `u16` fields, a `for` and the draft's own `do`/`while` all matched.
decomp-repair `global_offset_records` writes both spellings from the draft; the profile probe judges them under `-O1`.

Failed experiments that would otherwise be repeated:
About 40 attempts on the draft's shape (register locals, fences, other compilers). Under `-O2` GCC writes one `lui $at`
pair per member store, so no source spelling keeps the base register there.

What is controlled evidence and what is one function:
The spelling table above is controlled (one judge, one function). That `-O1` keeps a held address for such a run is one
function.
