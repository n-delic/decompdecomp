---
title: start word is alignment padding
kind: finding
compiler: SN GCC 2.8.1 (N64, MIPS)
evidence: observation
date: 2026-11-24
---

What the function looked like and the exact compiler and flags: MIPS functions built with SN GCC 2.8.1 (N64, -O2) whose
first word is not theirs: the assembler fills the gap before the next entry with leftover bytes and the disassembler
folds that word into the function that follows. Seen shapes: a constant load (`addiu $a1,$zero,0x4F`) ahead of
`addiu $sp,$sp,-0x18`; an `addiu $sp,$sp,-0x20` ahead of a `-0x18` frame whose epilogue gives back 0x18; a `beqz`
whose delay slot is the real `addiu $sp`; a `lw $ra,0x28($sp)` ahead of a frameless leaf. In one game 19 open
functions (of 966) had it, most one word, a few two or three (a callback pointer into `func + 0x8` / `+ 0xC`).

The source change, and the measured result before and after: none; the correct C scored 100 (or 270) with only the
leading word different, everything from the real entry byte-identical. Agents spent hours on these. The app now says
"possible start boundary problem: suggested start +4" (app/start_boundaries.py) in the brief and in Review function
boundaries, from the bytes alone: other code pointing at `func + 4k` (a %hi/%lo pair or a `jal`) and nothing at `func`
is the strongest evidence; this game's functions are only 56% 8-aligned, so "starts 4 past an 8-byte boundary" counts
only where nearly all functions are aligned; a frame the epilogue does not return, a stack reload with no frame and a
register set again before it is read are the dead-word checks. A leading word that something enters or branches back to is never flagged.

Failed experiments that would otherwise be repeated: assuming 8-byte alignment (this game puts 962 of 2206 functions
at 4 mod 8, so a start at 4 mod 8 alone proves nothing); treating `$a0`-`$a3` set before a call as dead (the callee may
take them: only a write that is overwritten before the next call counts).

What is controlled evidence and what is one function: a read-only run over a copy of one project's stored functions
(2206 entries, 966 open) flagged 24 entries (19 open: 3 strong, 16 tentative); all five hand-found examples were flagged
(one only by the stack-reload evidence, tentative). No match was produced or judged: the suggestion is
evidence for a reviewer, and no tool applies it yet. Unit tests use fake instruction lists (tests/test_start_boundaries.py).
