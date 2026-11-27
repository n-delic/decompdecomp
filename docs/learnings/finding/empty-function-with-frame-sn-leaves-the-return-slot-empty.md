---
title: empty function with a frame, SN leaves the return slot empty
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64; gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 4-instruction N64 leaf that only opens
and closes a 0x800-byte frame: `addiu $sp,$sp,-0x800; addiu $sp,$sp,0x800; jr $ra; nop`. The draft was an empty
`void f(void) {}` (score 200).

The source change, and the measured result before and after (scores, the judge used): one unused `char buf[0x800];` in
the empty body. Under SN GCC 2.8.1 (the flags above) the app's judge gave 0 (matched). The same C under KMC GCC 2.7.2
scores 301: KMC puts the frame release in the `jr $ra` delay slot (`addiu -0x800; jr $ra; addiu 0x800`). So a frame
released before `jr $ra; nop` in a leaf like this points to the SN build, not to different C.

Failed experiments that would otherwise be repeated: none needed by hand. The library's `unused_frame_local`
(`leaf_frame_repairs`) reaches 0 from the empty draft in one step under SN, so no new repair is needed. The saved
trials of earlier automatic searches had tried that family under KMC and kept its 301 child as a miss; the same
source had to be judged under SN. A random-rewrite search on the empty body cannot get there.

What is controlled evidence and what is one function: one function, one compile each under the two compilers.
