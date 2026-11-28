---
title: six zero word stores from two registers are three 64-bit globals
kind: function
compiler: SN GCC 2.7.2 sn-n64-272-0004 -G0 -mips2 -O2
evidence: controlled
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 16-instruction N64 leaf that clears six
consecutive words. The target is `addu $v0,$zero,$zero; addu $v1,$zero,$zero`, then `sw` from `$v0` and `$v1` in turn to
`D_x, D_x+4, ... D_x+0x14`, then `jr $ra; nop`. Exact under SN GCC 2.7.2 (`sn-n64-272-0004`, `-G0 -mips2 -O2`).

The source change, and the measured result before and after (scores, the judge used): m2c wrote six `s32` zero stores,
one per address. They are three 64-bit stores: `*(s64 *) &D_x = 0;` (or three `unsigned long long` globals), the two
registers being the halves of one 64-bit zero. App judge, from the saved draft's child: 390 -> 0 under the SN profile,
390 -> 160 under the function's former KMC GCC 2.7.2 `-O1` (the last store filled the `jr` delay slot there). The
library family `word_pair_doubleword_store` now writes this child from the draft and the target's assembly.

Failed experiments that would otherwise be repeated: earlier attempts spelled the zeros as 32-bit locals, chains and a
struct pair (best 230, never exact). Under KMC the delay slot stayed filled with a `volatile` last global. KMC
`-O1 -g` and `-O2 -g` are also exact; the SN profile was applied because it needs no debug flag.

What is controlled evidence and what is one function: the scores above are controlled judge runs on one function.
Which of the exact profiles built it is not established.
