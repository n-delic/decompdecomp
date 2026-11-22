---
title: a row-major table is `D[row][column]`, not a word and a pointer walk (both parallel tables, one child)
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

A 374-instruction state-machine function (a switch on a state, three table accesses by the same (row, column) pair of
bytes read from a record) sat at 35 in the app's judge: seven register rows and no other difference. The target's three
accesses add the *scaled column* to `row * 28 + base` (`addu $v1,$v1,$v0`, result in the column's register); the
candidate's added the other way round and kept a register of its own (`addu $a0,$v0,$v1`).

| edit | score |
| --- | --- |
| saved best (count table `extern s32 D;` walked as `p = (&D) + row * 7; p += col;`, a scaffolded block copy of it, the byte-declared parallel table read as `*(s32 *)(col * 4 + (temp * 4 + base))`) | 35 |
| m2c's walk without the scaffolding | 645 |
| `p = &D[row][col];` kept, tables declared `s32 [][7]` | 2215 |
| both tables declared `extern s32 D[][7];`, every use the subscript `D[row][col]`, pointer and base locals gone | 0 |

No scaffolding in the match: two `s32` tables with seven columns and `D[a][b]`, which is what the game wrote. A
decompiler cannot see it: the count table is one word to m2c (every access goes through `&D`), the other table was
inferred as bytes and then read as words.

Why (gcc 2.7.2 cse/combine, not confirmed pass by pass): `base[i][j]` is `(i * 28 + base) + j * 4` with the scaled
column as the first operand of the final add, which is what the target has; the pointer walk `p += j` adds the other
way. Keeping the address in a pointer (`p = &D[row][col]`) hoists the whole address computation above unrelated work, so
each use has to be the subscript itself, in the statement that reads it.

decomprepair `row_major_table` offers every access of both tables as one child, then one child per table; the child from
m2c's walk is the exact source. Controlled by the table above on one function. The families that existed
(`row_subscript`, `linear_row_address`, `staged_row_address`) need the row product in a temporary and a byte base, and
never change a table's declaration.
