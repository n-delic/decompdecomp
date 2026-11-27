---
title: A statement run written out twice was one static inline helper
kind: finding
compiler: gcc-2.7.2-kmc
evidence: observation
date: 2026-11-27
---

Case: a 269-instruction controller-pak read and write routine of an N64 game's libultra, which steps along a file's page
chain in two loops (load the page table when the bank changes, take the next entry, return an error code when it is out
of range). `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32`, `-O2` and `-O3`. Judged with the app's standalone judge.

Result: the step written out in both loops scored 14865 at both levels (frame 8 bytes larger, other registers). The same
lines as one `static __inline__` (or, at `-O3`, plain `static`) function, called as
`if ((ret = step(pfs, &bank, &inode, &cur_page)) != 0) return ret;`, were byte-identical. The order of the addresses
passed decides the locals' stack slots: another order scored 20 (stack rows only). The app's machine draft (10364 best
after 63 attempts, `u8 sp[352]` with offset macros) was not the seed: the match was a rewrite in the library's usual
shape. The function's own profile had been another compiler's; the project default compiler at `-O3` was exact.

Repair: decomp-repair `repeated_run_helper` writes a run found at two or more places as one helper, with the locals'
address arguments in each order; two of its seven children of the written-out source scored 0, the rest 20.

Failed: `-O2` with a plain `static` function was not tried; `sn-n64-281` on the helper form scored 9977 at `-O3` and
15087 at `-O2 -mfp64`.

Controlled: written-out against helper, same source otherwise. One function. That integrate.c's separate compilation of
the body is the reason is a hypothesis.
