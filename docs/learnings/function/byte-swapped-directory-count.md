---
title: byte swapped directory count
kind: function
compiler: sn-n64-281 -O2 -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 42-instruction N64 directory lookup: read a little-endian entry count, byte-swap it with two 16-bit swaps added
together, then search 0x28-byte entries by name (`while (n-- != 0)`). `sn-n64-281 -G0 -mips3 -O2 -mgas -meb
-mcpu=VR4300 -mhard-float -mfp64`.

The source change, and the measured result before and after (scores, the judge used):

From m2c's saved draft, judged by the app's judge, each step on the one before:

1. the high-half local `temp = word >> 16` declared `u16` (decomp-repair `high_half_halfword`): 1330 -> 990;
2. the shifted first half staged in its own local before that assignment (`temporary_hoist`): 990 -> 795;
3. the counter and the loaded word as one variable (`disjoint_local_merge`): 795 -> 785;
4. m2c's `n = E - 1; if (n != -1) { for (;;) { n -= 1; ... } }` as `n = E; while (n-- != 0)`
   (`sentinel_countdown_postdec`): 785 -> 0.

Failed experiments that would otherwise be repeated:

Step 4 alone on the draft scores 2348 (worse than 1330), and on steps 1-2 without the merge 1778: it pays off only
after the counter reuses the loaded word. With a `u32` high-half local the finished source scores 580. A `static
inline` swap helper with a `u16` parameter scored 1983. The app's saved gcc-2.7.2-kmc -O1 attempt (800) was not
needed: the function's own profile matches.

What is controlled evidence and what is one function:

All of it is one function.
