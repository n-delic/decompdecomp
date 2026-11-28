---
title: an unread three-word local copied from read-only data beside a counter increment (460 -> 0)
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mfp64
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:
A 15-instruction leaf under SN GCC 2.8.1 (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`): it
loads a global counter, copies three words of read-only data to the stack through one address register and three
temporaries, stores the counter plus one, and never reads the stack words. The machine draft copied the words with three
separate stores and scored 460.

The source change, and the measured result before and after (scores, the judge used):
The three words are one aggregate copied into an unused local (`Triple v = D_X;` with a three-member struct), followed by
`counter++;`: 0 in the app's judge. Nothing had to be written for the permuter: `symbol_stack_block_copy`
(decomp-repair, `__builtin_memcpy` of the same bytes) reaches 0 in one step from the saved draft with the library the app
pins (`prove_chain.py --families symbol_stack_block_copy,...`, judged with the app's judge-worker). The function had
simply not had its repairs run again since that family was released.

Failed experiments that would otherwise be repeated:
None; the first aggregate source was exact.

What is controlled evidence and what is one function:
One function. When a leaf's only unexplained instructions are loads and stores of stack words nothing reads, run the
block-copy families on the draft before editing by hand.
