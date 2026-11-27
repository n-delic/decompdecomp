---
title: consecutive pointer globals load above member stores
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 184-instruction N64 effect set-up (an SDK-shaped routine reading a parameter list) copies two pointer globals in
consecutive words into the members of a structure, between stores to other members:

    f->length = param[j++];
    f->delay = D_800FD900;
    f->base = D_800FD904;

KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`. The target stores `length`, then loads `D_800FD900`, then reads
`length` again for the loop test.

Measured with the app's judge, from the SDK shape written by hand:

- plain `extern T *D_800FD900; extern s16 *D_800FD904;`: 430 (the first global's load sits above the `length` store);
- `D_800FD904[]` alone, or `D_800FD900[]` alone (a one-member struct for it too): worse (855 for the first);
- both declared `[]` and read as `[0]`: 0. One struct holding all the consecutive pointers, or one `void *` array
  across them, was also 0.

Reading (a hypothesis, `sched.c` not traced): a plain scalar at a fixed address cannot alias a store to a structure
member through a pointer, so its load moves above the store; an array element or record member can.

decomprepair's `unsized_extern_array` took only scalar globals; it now takes pointer globals and offers consecutive
words as one child (decomp-repair pull request 2160). `prove_chain --families unsized_extern_array` goes 430 -> 0 in
one step from the hand-written shape.

One function; the library test is the controlled part.
