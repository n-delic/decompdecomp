---
title: A boundary-adjusted function lost its data relocations: literals could not link, so agents wrote address casts
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

Six open functions of an N64 game (SN GCC 2.8.1), all trimmed or split by the automatic boundary fixes, kept
"one schedule pair" or "three rows" from exact for hours: a debug printer comparing five fields with `1e-05f`, a
camera follower with nine pool floats, and a setup routine storing `293.0f`/`-567.0f`. Their stored targets had only
jump relocations, and their assembly was rabbitizer's bare text (`lwc1 $f20, -0x7D8C($at)`). Matched functions read the
same pool words as `%lo(D_80008274)($at)`.

The source change, and the measured result before and after (scores, the judge used):

A boundary decision rebuilt the changed extent from raw words, and so dropped every data reference of the split's
annotated assembly. That had three effects:
- The linker places a candidate's own `.rdata` (an SN `li.s` literal's pool word, a string) only where the target
  refers to it, so the right C (`o->x100 > 1e-05f`) could not link: 2205, only those rows.
- Agents wrote `*(f32 *)0x80008274` or an `extern f32`. Those compile to an ordinary load the scheduler places
  differently from the `li.s` macro, so a schedule row or a register row stayed.
- `float_literal_global` never fired, because it reads its words from the assembly's `%lo(S)($at)` loads.

decompdecomp #3608 and #3610 now carry the original rows' data references into rebuilt targets and their assembly,
and restore them on older decisions at the next check. With the relocations restored and the `-mfp32` profile, each
result was measured with `_judge_with`:
- debug printer: 2205 -> 0 with the literal at each compare;
- camera follower: 4565 -> 0 with one literal per use, plus a missed second `+ 300.0f`;
- a 346-instruction camera push-out and the 568-instruction state machine: written fresh from the split's assembly
  (m2c drafts them once the jump-table labels are put back), exact after one or three edits;
- setup routine: 1240 -> 180.

decomp-repair 0.6.43 `float_literal_global` now also rewrites the address-cast form and a local that only holds the
word, so the saved bests reach the literal by themselves (500 -> 0 on the printer).

Failed experiments that would otherwise be repeated: every spelling of the constant through a symbol or an address on
the old target (cast, `extern`, a local, `volatile`). The schedule comes from how the value is loaded, so no spelling
of the global closes it.

What is controlled evidence and what is one function: the reloc loss is controlled (23 of 35 boundary-adjusted
functions of the game, read from the database). The matches are four functions of one game, each judged attach-only
with the relocations rebuilt in memory, because the running app keeps the old targets until it runs the new code.
