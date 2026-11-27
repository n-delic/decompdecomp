---
title: A twin's later else-if arms compare with zero where the donor's compare with a constant
kind: finding
compiler: gcc-2.7.2-kmc
evidence: observation
date: 2026-11-27
---

KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O1`. An open 150-instruction function had four matched look-alikes beside it
(all `-O1`; the function's own profile was the project's `-O2`). The donor's C with this function's one differing
symbol was still 5900 under `-O2` and 1200 under `-O1`. The rows left: the target reloads a byte and `beqz`es where the
candidate loads 1 and `beq`s, in two chains.

The change: in `if (v == 4) .. else if (v == 1) ..` the second arm is `v == 0` in this function, in two chains at once;
1200 -> 0 on the app's judge. A third chain, which already tests 0 and then 1, stays.

The instruction counts differ, so the literal search over aligned immediates (`mips_immediates`) never offered it. The
decomprepair family `else_chain_compare_zero` does, all arms together first, and the look-alike trial
(`app/lookalike_profiles.py`) tries it after its literal search.

Measured on one function; the other matched twins use 1 in these arms, so it is a difference between twins, not a
compiler rule.
