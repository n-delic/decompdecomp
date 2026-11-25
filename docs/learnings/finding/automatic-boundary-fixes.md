---
title: automatic boundary fixes
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

An N64 game built with SN GCC 2.8.1 (`sn-n64-281`, -O2) imported with 28 functions whose extent was wrong, each of
which cost agents hours of C that could never match (the right source scored 100 with only a leading word
different). Two shapes:

- start: the function before returns (`jr $ra` and its delay slot), then one to three words that are not the next
  function's: a copy of the return's delay filler, `addiu $sp,+0x20; addiu $sp,-0x20`, a `break 7` from the
  previous function's divide check, a load into a register nobody reads, `j self; nop` (a `for (;;)` tail), a
  `lui`/`addiu` pair building another callback's address. The real entry is the next frame opener (or a frameless
  leaf); other code stores it as a callback `func + 4/8/0xC` or calls it.
- end: a function ends in a loop that never returns (`j` back and its delay slot, `osViBlack(1); for (;;);`) and
  the next function, which other code calls or points at, opens its frame on the next word, inside the extent.

The rule (app/entry_fixes.py): read as the start of a compiled function, the stray words contradict themselves. GCC
never begins a function by reading a temporary nobody set, using a callee-saved register's value, giving back a
frame it never took, opening a second frame, trapping, loading `$ra`, calling before saving `$ra`, jumping to
itself or over its own frame, or storing into the caller's frame above the argument slots. A start is fixed
automatically when such a contradiction (or a pointer to the real entry from other code) agrees with an entry's shape
at the real entry, the code before returned, and nothing enters the stray words. Register-read contradictions also
fit a switch case reached through a table, so alone they count only when the real entry opens a frame.

The source change, and the measured result before and after (scores, the judge used):

No source change: the importer's extents. Over that game, 25 of the 28 are fixed automatically (20 starts trimmed, one
whole leftover piece archived as padding, 4 swallowed functions split); the other 3 have no evidence in the bytes
(one start word that only loads from an argument, one entry many functions call, one end with no inner entry).
Applied through the app's own path on a copy of that project, 30 fixes went in (25 of the 28, four more of the
same shapes the list had missed, and in a second round a function the first round's trim had exposed as holding
two), 97 matches that refer into the changed extents were rebuilt first and all still matched, and a second run
changed nothing. Five of the 23 trimmed functions matched at once with the C agents had already written for the
old extent, renamed: the boundary was all that stood in the way.
Negative control: every matched function's extent is proven by the judge, so a fix proposed for one is a false
positive. Run as if unmatched over 30,339 matched functions of 36 N64 games (built with several compilers) and the
homemade N64 demo's 96 symbol extents: 0 flagged.

Failed experiments that would otherwise be repeated:

- A stored word in the image equal to the entry, counted as a callback: 7 false positives over the 36 games (data
  that happens to equal an address). Stored words now only ever block a fix (they may be a jump table).
- Pointers into addresses that several overlays share (relocatable modules all loaded at 0): 1 false positive; a
  function whose addresses another live entry also covers is never fixed.
- Alignment as evidence: in this game only 56% of functions start on an 8-byte boundary, so off-alignment says
  nothing (start_boundaries' `layout` signal never fires there).

What is controlled evidence and what is one function:

The 0-of-30,339 negative control is measured on real games; the positive cases are one game's 28. Fixtures for each
shape are homemade instruction lists (tests/test_entry_fixes.py), not game code.
