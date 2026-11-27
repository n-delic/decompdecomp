---
title: Split globals past a held base are members of one record; a joined argument local is a call per arm
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 73-instruction N64 screen-mode setter,
SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Its machine draft scored 3145. The target takes
one symbol's address in a register, adds constants too big for a 16-bit offset (`lui; addu`, `lui; ori; addu`) and
stores through the sums; the draft had four separate globals. It also picked two call arguments into locals and a 0/1
argument into a flag.

The source change, and the measured result before and after (scores, the judge used): all scores are the app's judge
(`fast_judge.py`), and the final source is recorded as matched (0).

- The four globals as members of one record at the held symbol. Reverting only this from the exact source: 2285; applied
  alone to the draft: 3145 -> 1095. decomp-repair `far_record_members`.
- `if (c) { f(1); } else { f(0); }` for `v = 0; if (c) v = 1; f(v);` (existing `selected_argument_call_split`). Reverting
  only this: 835.
- `if (c) { f(a); } else { f(b); }` for a local both arms set before `f(v)`. Reverting the first: 20, the second: 240.
  The second also needs the condition's polarity: `==` with the arms exchanged is 240, `!=` is 0. decomp-repair
  `joined_call_arm_split` (decomp-repair PR 2139).

The three families in that order take the machine draft to 0.

Failed experiments that would otherwise be repeated:

- Casted byte offsets off the held symbol (`*(s16 *)(base + 0x27148)`): 2335, the compiler folds them into one relocation.
- A `?:` argument (`f(c ? a : b)`) in place of the call per arm: 1270 with the flag local, no better than the local.
- `prove_chain.py --families all` from the draft with the earlier library stopped at 2525 after 150 judged variants.

What is controlled evidence and what is one function: one function. The record and cast comparison is a controlled pair
on it. That the compiler keeps a base register for a member past 32 KB and folds a cast is an observation from these
compiles, not confirmed in the compiler source.
