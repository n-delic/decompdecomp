---
title: Single-use loop index copy
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A MIPS state-machine candidate copied an outer loop index into another local immediately before a nested scan and used the copy only in the inner comparison. The target compared the original saved-register index directly. Removing the copy, its declaration and assignment and reading the original index at the comparison reduced the linked judge score from 655 to 550. The function remained open; the O1 profile was used for diagnostic trials only. This source still contains separately disclosed one-shot allocation scopes.

The generated child from the new family independently scored 550. The family restricts the change to matching unescaped scalar types, rejects intervening writes, incoming labels, possibly skipped assignments and macros that can access either name. Synthetic safety tests are controlled checks of that rewrite, not additional game matches. Earlier in the search, removing the same copy before other control-flow changes scored worse (1370 to 1400); the later context made it useful.
