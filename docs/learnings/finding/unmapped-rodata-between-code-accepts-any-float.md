---
title: .rodata between two code segments was unread, so the judge accepted any float literal
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 22-instruction N64 setup routine that stores two float constants after a call. The target loads them
`lui $at; lwc1 $f0, %lo(D_800042C0)($at)`, GCC's literal-pool load (`li.s`), while its integer stores build their
addresses in `$v0`. The draft read them as `extern f32` globals (SN GCC: `lui $v0; lwc1 ...($v0)`).

The constants sat in 32 KB of `.rodata` filed as a raw `bin` between two code segments with one ROM-to-RAM offset.
The first segment had no switch of its own, so `split.trailing_data` didn't map the run. Consequences, all measured:

- the judge scored the source 0 with `1.5f` and `2.5f` written for the two constants (the ROM holds 55.0 and 190.0):
  with no bytes to compare, a literal's value is not checked;
- `float_literal_words` gave `float_literal_global` no words, so no literal was ever proposed (13 tries by an
  earlier agent stopped at 20, the two `$at` rows);
- `read_data` answered "not in a loaded part of the ROM".

Two app fixes: raw bytes with code at the same offset on both sides are mapped when any code's switches prove them,
and `float_literal_words` takes an automatic name's address from the name when no symbol exists. After both, the
judge scores the wrong constants 1, and the existing families reach 0 from the saved draft by themselves
(`prove_chain.py --families all`: `copy_sink_after_call` 1090 -> 140, `assignment_run_move` 20,
`float_literal_global` 0). No new library family was needed.

Re-judging the 46 matched functions of that game that read the newly mapped range, 44 stayed exact and two
audio-library parameter handlers scored 1: their `switch` case labels do not agree with the ROM's jump tables. They
had been accepted unchecked.

Controlled: the regression tests (`tests/test_judge_data.py`, `tests/test_float_literal_words.py`). One game: the
counts above.
