---
title: SN64 0001's cppn64.exe and the reconstructed 970404 cpp preprocess identically (CRLF aside)
kind: finding
compiler: SN64 GCC 2.7.2 build 0001 (cppn64.exe, sha256 fbf093ae…) vs reconstructed sn-n64-272-970404 cpp (native arm64)
evidence: verified
date: 2026-11-26
---

What the case looked like and the exact compiler and flags: the SN64 driver (consoles/n64/sn64.py) preprocesses with
`cppn64.exe -P -undef -nostdinc -D_LANGUAGE_C -D__GNUC__=2 -I<include>` under wibo. On an Apple Silicon Mac, wibo
is an x86-64 program running under Rosetta, and each Windows program costs about 0.065 s of processor just to start
(an empty file: cpp 0.07 s, cc1 0.075 s, asn 0.06 s; a small function's cc1 0.13 s). cppn64's whole run is that start.

The source change, and the measured result: the native `cpp` of the reconstructed SN64 GCC (decompals/SN64-gcc
2.7.2-970404, built from source by decompdecomp) with the same arguments, its LF line ends made CRLF. Compared on
3,290 unique C sources of an N64 game (matched, saved best, seeds and the latest 3,000 attempts) with the game's
include folder: every output identical to cppn64.exe's after removing CR. Then all 739 matched functions compiled
both ways with the project's context header: identical code, data, relocations and symbols (the converter's
source-file symbol differs between any two runs, so it is left out of the comparison). The driver now uses the
native cpp on a Mac when it is installed and the given cppn64.exe has build 0001's checksum (NATIVE_CPP).

Failed experiments that would otherwise be repeated: the 970404 reconstruction's cc1 is not a substitute for
0001's or 0004's cc1n64.exe (its scores differ on the same source, e.g. 6696 against 0004's 2380 in
tail-store-sched1-priority-and-a0-pointer). Builds 0004, 0006 and 2.8.1 ship other cppn64.exe files (0004 and 2.8.1
share one, 0006 has its own): not compared yet, so they keep theirs.

What is controlled evidence and what is one function: controlled, output-for-output comparison on one game's
corpus; LF input to cc1n64 also gave identical assembly on two hand-written files, but the driver converts to CRLF
so cc1 reads exactly the bytes it read before.
