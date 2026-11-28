---
title: Counter stepped and shifted in its own temporary after the addend's load (270 to 0)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

A 25-instruction N64 effect update: add a step into a phase field, advance a timer, store a halfword height
(`base halfword + (timer >> 2)`), release the entity when the height passes a word limit plus a constant. The
project default (gcc-2.7.2-kmc `-O2`) stayed at 270; the match is a per-function SN 2.8.1 profile.

Matching shape (app judge: exact), with `-mfp64`:

    do { e->phase += e->phaseStep;  t = e->timer; } while (0);
    h = *(u16 *)((char *)e + 0x3A);
    do { do { do { t++;  e->timer = t;  t >>= 2; } while (0); } while (0); } while (0);
    h += t;
    e->height = h;

Measured steps (isolated judge, SN profile): typed C 445; with the old counter read before the step and a fence 180;
the addend loaded into the sum's local first and the shift done in place 105; the steps one fence deep 60 (all rows
registers); three deep 0. The fences are weighting scaffolding. decomp-repair `stepped_counter_addend_split`
writes the whole combination as one child; from the saved m2c-style C its first child compiles to the same
instructions as the matched C (compared by compiling, not judged).

Failed under KMC 2.7.2 (never below 270): statement orders, chained and compound spellings, inline helpers,
`-fno-schedule-insns` (550), reusing one local for the timer and the limit. The limit load was always scheduled
above the shift; hypothesis: sched.c launches a once-set destination early (`birthing_insn_p`).

Observation from one function; that flow.c's loop weighting is what the three fences change is a hypothesis.
