---
title: jump table spelled as symbol plus offset, and a switch that only filters values
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 48-instruction N64 function, SN GCC 2.8.1
(`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`). A flag lets only some ids through a
`switch` (`break` for those, `default: return;`) before code both paths run. The table sat inside the symbol before it,
so the assembly said `%lo(func_X + 0x20)` and the machine draft cut the switch ("jump table is not provided"); the
saved draft judged 1815.

The source change, and the measured result before and after (scores, the judge used): exact from a matched neighbour's
C. For the draft path: (1) the app reads a table spelled `SYMBOL + 0xN` before a `jr` and keeps it only when its words
are the function's own addresses (`draft_data.offset_tables`); (2) m2c then writes `if (g) { switch (x); } else {...}`
and its compilable form drops the case labels (2070); decomp-repair `filter_switch_cases` writes the filter from the
table (510), `symbol_address_local` follows (280). A hand-compiled version of the fixed draft (typed record, filter
switch) went 425 -> 10 (`symbol_address_local`) -> 0 (`guard_temp_sink`) with existing families.

Failed experiments that would otherwise be repeated: a pointer local to the table's record (`p = &table[id]; p->x`)
stayed at 415 whatever its type; the record indexed at each access (`table[id].x`) was exact. Passing the id on to a
callee that takes one argument cost 270.

What is controlled evidence and what is one function: the table reading and the family have regression tests; the scores
are one function. The compilable draft's byte-offset arithmetic on an `M2C_UNK` array still isn't repaired.
