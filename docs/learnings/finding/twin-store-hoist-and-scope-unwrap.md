---
title: A second store of one value beside its first, with the one-shot scopes dropped (60 -> 0)
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

A 142-instruction object-setup function (a record's fields stored through one pointer, stack parameters reloaded
between the stores). The saved best scored 60: the target stores `sh 0x4E`, `sh 0x50` from one `andi` result back to
back, the best had the second halfword three stores later (after `sb 0xC0`, `sb 0x52`).

Source change and measured result (isolated judge, one function): the `0x50` store moved right after the `0x4E` store
alone scored 120; the three `do { } while (0)` scopes (two outer ones and a one-shot around one store) dropped alone
scored 60; together 0, confirmed by the app. Grids of every order of the later stores (720 and 720 orders) never went
below 60 with the scopes in place.

Why (read in the second scheduler's `-dR` dump; hypothesis, the judge decides): sched2 runs backward. The stack-argument
reload `lbu $t0, N($sp)` is placed just before its use, and a store after it in the first scheduler's order gets an
anti-dependence on it (a store before it is a true dependence of the load). A store written after the use of the reload
(`sb 0x52`) can therefore never be placed before the load, which gives it priority 5 where the store written earlier has
3. Writing the twin early keeps it before the load, as in the target. That changes the priority of the `li -1` that
follows (it has an anti-dependence on the `0x50` store): 5 with the store late, 3 with it early, and with 3 it ties with the
`sll` of the packed word and the two swap. Dropping the scopes was the other half; why it flips that tie was not traced.

Failed: statement-order grids over the later stores with the scopes in place; the packed word inline instead of a
local (836).

Library: decomp-repair `twin_store_hoist_unwrap` (the hoist alone is `twin_store_hoist`). One function; the
dependence reading is from one dump.
