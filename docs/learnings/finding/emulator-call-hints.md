---
title: recorded real calls give a function's argument kinds, ranges and field values; they did not improve the drafts
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (an N64 game's own profiles)
evidence: observation
date: 2026-11-22
---

The reversing emulator (mupenRE, `consoles/n64/emulator`) records a function's first calls: its registers and RAM at
the entry (the recorder `compare_behaviour` uses). `arch/mips_call_hints.py` reads them with the function's code
(which argument registers it reads before writing, its loads and stores through them, a mask on an argument) into
hints: how many arguments, integer or float, the values the arguments had (a range, never negative, fits u8 or
u16, a pointer into RAM), and the values found at the offsets the code reads through a pointer argument (a halfword
that stays under 256, a word that is another pointer). `app/call_hints.py` saves them beside the recordings in the
project's build folder (never committed) and offers them in the brief; `get_call_hints` records, and
`compare_behaviour` saves them from its own recording.

Measured live on an N64 game (scratch toolchains folder with a mupenRE built there, the user's ROM read in place, no
saved state touched): every matched function (1,696) given an execution breakpoint in one run of 2,400 retraces
without controller input (37 s), up to 4 calls recorded each, the words the code reads kept, the 4 MB dumps deleted
as they arrived.

| | functions |
|---|---|
| reached in 2,400 retraces at the title and its attract loop | 405 of 1,696 (24 %); 233 with 4 calls |
| integer argument count equals the matched C's | 369 of 404 (91 %); 19 are lower (the matched C declares an argument the code never reads), 16 higher (the code reads a register the matched C does not name) |
| a float argument ($f12/$f14) present exactly when the matched C has a float parameter | 386 of 404 |
| with argument ranges (an integer or pointer argument read) | 262 |
| an argument that is always a pointer into RAM | 174 (the matched C declares 107 of them as pointers: the rest it types `s32`) |
| code that reads through a pointer argument | 124 (94 with values found at the offsets, 35 with a field that is itself a pointer) |
| the code masks an argument to a byte or halfword | 37 |
| ... and every recorded value fits it, with at least two distinct values | 6 |

Draft evidence (the m2c draft with the narrow parameter type the mask and the values agree on, the mask dropped;
and without trailing parameters the code never reads), judged beside the raw draft with the exact isolated judge (no
saved match touched): on the 359 drafts of reached functions the narrowing fired on 5: 0 better, 1 worse (1,976 to
2,076), 3 the same, 1 that did not compile (the project header's prototype for it has the old type). Dropping
trailing parameters fired on none (m2c already declares only the arguments the code reads). So the draft candidates
are off (`draft_call_hints`); the hints stay in the brief, where the pointer-versus-integer and range lines are
the part no static reader gives.

Limits: 24 % reached without input (a scripted controller reaches more: `from_vi`/`input`); a constant or a single
call proves no range (hence the 6); memory the function writes needs the state after the call, which the recorder
does not take (compare_behaviour gets it from the recompiled C); one game, one compiler.
