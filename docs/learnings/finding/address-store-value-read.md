---
title: address store value read
kind: finding
compiler: KMC GCC 2.7.2 (sn-n64-272-0001) -G0 -mips3 -O1
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 30-instruction free-list allocator of an
N64 game, `sn-n64-272-0001 -G0 -mips3 -O1 -mgas -meb -mhard-float`. It pops a node from a list head word, pushes the
node on a second list, and stores the second list's head into the node's `next` field:
`*(s32 *)(D_802028E0 + off) = D_800B2FE0;` with `extern s32 D_800B2FE0;`. A draft that spells the value
`(s32)&D_800B2FE0` (the form m2c and the `global_value_address` repair use for an address) builds `lui; addiu %lo(...)`
where the target has `lui; lw %lo(D_800B2FE0)`: one opcode differs, score 200.

The source change, and the measured result before and after (scores, the judge used): `(s32)&D_800B2FE0` ->
`D_800B2FE0`, 200 -> 0 (fast_judge, the app's judge). The decomp-repair family `stored_address_value_read`
(`address_store_value_read.py`, offered for `LV = &S;` and `LV = (T)&S;` where `S` is declared a word scalar) does it:
`prove_chain.py --families stored_address_value_read` reaches 0 in one step from the broken spelling, and with
`--families all --children 40` the same child is the one picked. A saved best that already had the right spelling was
not available (the app had kept the matched C), so the broken source was made by reversing the match: a controlled
test, not a rediscovery.

Failed experiments that would otherwise be repeated: none.

What is controlled evidence and what is one function: the reverse edit of one match on one function; the family only
proposes, and the judge decides. It never touches arrays, pointers or byte and halfword globals, whose address is the
value.
