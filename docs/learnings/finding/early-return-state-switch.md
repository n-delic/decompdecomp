---
title: early return state switch
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

An N64 state handler begins with three adjacent values: the middle value continues, while the values on either side
run their own bodies and return. Its machine draft spells this as a nested compare tree with early returns.

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, replacing the tree with a three-case switch scored 12,172 to
10,402 in the local linked judge. The app's own profile is still `-O2`; this was an alternate-profile diagnostic, not
a match or a compiler-profile change. Eight of twelve nearby matched functions used `-O1`.

The saved candidate scored 20,754 under its `-O2` profile and 15,057 under an app `-O1` diagnostic. An earlier
`-O1` source scored 12,172; removing its unused pointer initialization retained that score. A search across the
case orders tried for this tree found 10,402 as its best score.

This is one observation, not a universal rule or a verified match. A separate compiled synthetic control checks the
tree and switch on negative, adjacent and extreme integer values. The game source and data remain private.
