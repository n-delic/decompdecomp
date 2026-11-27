---
title: a sparse jump-table switch written over x - low with its gaps as empty break arms
kind: function
compiler: KMC GCC 2.7.2 -O1 -g -G0 -mips3
evidence: verified
date: 2026-11-27
---

What the function looked like and the compiler:

A 135-instruction N64 state routine whose switch on a byte had labels 0 (an alias of default), 1, 2, 3, 4 and 11; the draft compiled a compare tree. The target is a jump table: addiu -1, sltiu 11.

The source change and measured result (app judge):

switch (x) became switch (x - 1) with labels 0..10: the old 1 is case 0, 2..4 are 1..3, 11 is 10, the alias case 0 of default is dropped, and the unused values 4..9 are written as empty case N: break; labels at the end. 555 -> 0. The gaps go to the end of the switch, not to the default arm (the draft's default called a function for them). decomp-repair switch_offset_table (0.6.295) proposes it from the target's biased sltiu: 555 -> 0 in prove_chain.

Failed experiments:

Gap labels on the default arm instead (offered as the second child, not needed here); the sparse switch with switch_range labels (no existing family changes the discriminant).

Controlled evidence:

One function; the gap destination (end versus default) comes from the judge, not the asm.
