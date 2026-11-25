---
title: a global pointer read at two sites, through one local assigned at each
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-25
---

What the function looked like: a 231-instruction N64 level loader (SN64 GCC 2.8.1, `sn-n64-281 -G0 -mips3 -O2 -mgas
-meb -mcpu=VR4300 -mhard-float -mfp32`). A record pointer global is read twice: `G->C0 = 2;` between two calls before a
search loop, and `G->D8 = G->9C;` inside the loop. The copy came out `lw $v1, 0x9c($v0); sw $v1, 0xd8($v0)` (pointer
`$v0`, value `$v1`); the target has `lw $v0, 0x9c($v1); sw $v0, 0xd8($v1)`. The float compare needs the `-mfp32`
per-function profile (`$f2`, not `$f1`): 7520 -> 7510 with the profile alone.

The source change and its measured results (local judge, `-mfp32`, then the app's compiler trial):
- Each read straight from the global is a block-local pseudo; local-alloc gives the pointer (more references, three
  against two) the first free register. One local `u8 *p` assigned at both sites (`p = G;` before each) is set in two
  blocks, so global.c allocates it after the block-local value: the value takes `$v0` and the pointer `$v1`. Alone it
  scored 7580: the shared local ranked 3.429 (8 refs, 7 live) under the loop's record pointer (3.538, 23 refs, 26 live),
  which then took `$v1` and moved the record pointer from `$a3`.
- `do { call(); p = G; p->C0 = 2; lvl = call2(arg0); } while (0);` around the first site and the calls on either side
  of it adds one weighted reference (9 refs, 3.857) and nothing else: 0 (trial EXACT).

Failed experiments (each with the shared local):
- the scope around the loop site's `p = G;` (4 refs on the loop-hoisted `%hi(G)`, which then outranked another
  hoisted `%hi` and swapped `$s4`/`$s5`), around the loop copy (moves the `li $a1, 1` argument), around the first
  site's two statements only, or the store only (the `move $a0, $s1` argument copy moves after the store: a scope is a
  scheduling boundary), or the first site plus the next call only (`lui` and `move` swap);
- a separate local for the first lookup's result instead of the shared record pointer (7675).

Controlled vs one function: one function, exact under the app's trial. decomp-repair `global_pointer_site_local`
reproduces the exact child from the saved best (child 11 of 14). The exact profile could not be applied at first:
the archived stale entry inside the function counted as an overlap (fixed in decompdecomp #3609).
