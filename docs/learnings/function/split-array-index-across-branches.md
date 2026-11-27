---
title: an array index the draft built in stages across branches, and a cached address read in an arm that never set it
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 61-instruction N64 input handler that picks
one of several 0x68-byte records by channel, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.
The first shift of the index multiply sits in the delay slots of the branches before the join, so the m2c draft kept
`channel * 2` and `channel * 3` in locals of their own across its goto ladder. The draft also set `__base_SYM = &SYM;`
in one `if` arm and read it in the sibling arm.

The source change, and the measured result before and after (scores, the judge used): app judge, saved draft 870.
Folding the staged locals into `channel * 0x68` alone: 200. Assigning the address local again at the head of the
sibling arm alone: 670. Both: 0. A clean rewrite (`&records[channel]`, nested `if`s, a plain byte array) is also 0.
decomp-repair `split_multiple_rejoin` (`split_multiple_rejoin_base_set` is its first child).

Failed experiments that would otherwise be repeated: replacing the address local by the symbol itself (`&SYM` at every
use) scored 1670: the target keeps the address in a register and loads it once per arm. `prove_chain.py --families all`
from the draft improved nothing in its first 120 children before this family.

What is controlled evidence and what is one function: scores are the app's judge on one function; the family's
refusals (a multiple read after its value changed, or not set on every path) are unit tests in the library.
