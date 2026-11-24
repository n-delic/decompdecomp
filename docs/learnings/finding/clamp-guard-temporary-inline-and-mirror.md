---
title: A clamp guard whose temporary is stored too is inlined, and only the guards that named it first are mirrored
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-24
---

A 44-instruction N64 clamp function (`gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2`): six guards that raise or lower
`a[k]` to `b[k]` for six word offsets. m2c's draft holds each `b[k]` in a temporary (`t = b[k]; if (t < a[k]) { a[k] = t; }`
for the first three, `if (a[k] < t)` for the last three). The saved best (the Deep Permuter had taken 60 -> 30 by
writing the last three temporaries into their guards) was 30: six register rows, the loads of `a[k]` and `b[k]` in the
opposite order and registers.

gcc 2.7.2 loads a comparison's operands in the order the C names them, so a temporary assigned before the `if` loads the
right-hand operand first. The target loads `a[k]` first for the first three guards and `b[k]` first for the last three:
the first three are `if (a[k] > b[k]) a[k] = b[k];` and the last three `if (a[k] < b[k]) a[k] = b[k];`, no temporaries.

Measured (isolated judge, same source otherwise):
- first three guards written `if (b[k] < a[k])` (temporary inlined, comparison not mirrored): 30, the same six rows;
- temporary kept, comparison mirrored (`a[k] > t`): 30; a local copy `cur = a[k]` before each compare: 865;
- inlined and mirrored in the first three guards only: 0. Mirroring all six or none is a different program.

Library: `compared_load_inline` (decomp-repair 0.5.1009, pinned 0.5.1010) already inlined a compared temporary and mirrored
every comparison, but declined a temporary that is also stored (`a[k] = t;` is not a comparison) and never mirrored a
subset. It now takes the store's read as an inline-only site and offers the mirrored set as none, all, each temporary
alone, and every prefix and suffix (at most 24 children). `prove_chain --families all` from the saved best: before, none
of 912 children improved 30; now `compared_load_inline` reaches 0 in one step. Run without judging over a project's 1,006
matched sources it fires on 4 (one child each; none before), and over its 1,650 m2c drafts on 45 (124 children, at most 16
for one function; 28 and 42 before).

What is controlled and what is one function: the 30 -> 0 is one function (verified exact); the rule (operand order of the
comparison decides the load order, so the temporary must be inlined for a mirror to matter) is the same one the family's
earlier bounding-box case showed, on floats.
