---
title: Voice allocator: records for the pointers, the address-taken pointer zeroed first
kind: function
compiler: KMC GCC 2.7.2 -O2
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 72-instruction libaudio voice allocator (`-G0 -mips3 -mgp32 -mfp32 -O2`): it fills a voice from a parameter block,
takes a physical voice through a pointer on the stack, and queues two update records. The saved best (265) read
everything through byte-offset casts.

The source change, and the measured result before and after (scores, the judge used):
Judged by the app. Pointers typed as records and the stack pointer set in its declaration, 270; the six opening
stores ordered by a grid, 0. From the saved best, the update record's pointer alone typed as a record is 265 -> 50
(member stores let the stack pointer's reload move above the global's field read). `byte_pointer_record_view` offered
nothing because another pointer's address was taken; it now keeps that one's casts and also offers one record at a
time (decomp-repair #1834). All records give 220, which an order of the opening stores takes to 0.

Failed experiments that would otherwise be repeated:
Keeping the global's value in a local before the update's stores (200), setting the stack pointer after the opening
stores (120), and reordering the opening stores with the voice still read through casts (best 60).

What is controlled evidence and what is one function:
Controlled: the scores of the family's children. One function: the store order and that the records are needed for it.
