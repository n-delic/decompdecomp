---
title: directory search: m2c's goto compare runs and rotated outer loop
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-27
---

A 104-instruction controller-pak directory search (SDK library code in an N64 game, KMC GCC 2.7.2): a record read per
slot into a 32-byte stack record, two id tests, a 16-byte name compare and a 4-byte extension compare.

The source change, and the measured result (the app's judge): plain SDK-shaped C was exact on the first compile under
this profile: `for` loops, `break` out of each compare with a mismatch flag, one `int i` for both compares, one result
variable for the checks, early `return`s, the record as one struct. The function's former profile (SN64 GCC 2.8.1) gave
4336 for the same C, and the saved best under it had stalled at 3360.

What each shape costs when it alone is left as m2c writes it (controlled variants of the exact source):

- one compare as a `goto` loop: 3190; `goto_backward_loop` alone reached 560; `compare_run_for_break` gives 0;
- the outer loop as `if (n > 0) { label: ... goto }`: 2819; `goto_backward_loop` gives 24, then `counted_for_loop`
  (now for a signed counter under a `> 0` guard) gives 0;
- a second counter for the second compare: 1040 (`compare_run_shared_counter` for the goto form);
- a separate local for the read's result: 110;
- the `if (status & 1) { ... }` guard instead of an early return: 200, closed by `guard_tail_return_hoist`;
- a `s32` company-code parameter masked in the test instead of `u16`: 475.

Failed experiments that would otherwise be repeated: rewriting only the compare loops of the saved best made it worse
(3360 -> 4310 under this profile's rules): that best reused its counters for the result and was not the same program.
`ret = 0` at the declaration, assignment inside the `if` and `&` between the two flag tests made no difference.

Open: from the raw machine draft the library does not reach 0. m2c splits the stack record into `sp10`/`sp14`/`sp1C`/
`sp20` scalars with the wrong load widths, and the outer loop leaves through shared labels. With the record written by
hand, the library chain stopped at 1780.
