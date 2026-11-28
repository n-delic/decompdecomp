---
title: Keep two coordinate addresses across an O1 scope
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A large MIPS drawing routine read both unsigned-halfword coordinates from a
byte-indexed record and passed them to a draw call. The 750-point candidate
made one address for both loads, while the target computed the same base and
offset twice into different registers. GCC's intermediate code initially
contained two sums; its first common-subexpression pass merged them.

In a single draw arm, naming the byte offset before the table base, assigning
one real coordinate pointer inside a one-shot scope, and computing the second
pointer after that scope restored both target additions. The isolated linked
judge improved from 750 to 655. Both pointers serve the original coordinate
reads; the one-shot scope is compiler-matching scaffolding. The address form
uses 32-bit integer pointer arithmetic on the named MIPS ABI.

Assigning the table base before the offset scored 1050: GCC loaded the table
base too early. Several ordinary pointer spellings, casts and paired locals
kept one address or worsened other rows. This is one linked function, not a
controlled proof that the scope is the sole cause of the change. The compiler
source says its first CSE pass treats a loop-end note as an extended-block
boundary; that observation is consistent with the result.
