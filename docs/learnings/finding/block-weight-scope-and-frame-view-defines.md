---
title: block weight scopes, frame view defines and the struct/scalar alias rule
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags: an 874-instruction N64 object spawner (the
first arm of a switch allocates a record, stores its fields through a record pointer, copies a 0x4C-byte record at
the end; a second arm does the same through a table pointer), KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`, saved
best at 3786 with 111 register rows, 41 of them the parameter's copy and the record pointer in each other's saved
registers (`$s4`/`$s6`). The saved best named its stack slots through nine function-local defines over a local struct
(`#define sp30 locals.sp30`).

The source change, and the measured result before and after (scores, the judge used; the app's exact judge, one source
at a time):
- The allocation table (`-dl -dg`, `passes.py`) said the record pointer (23 weighted references over 311 live
  instructions, priority 0.30) ranked below the parameter copy (49 over 495, 0.495), so global.c gave the parameter the
  register the target gives the pointer. A reference weighs one more per loop it sits in, and `do { ... } while (0);`
  is one loop more at no instruction cost. Wrapping the `if (...) { ... }` block that holds the pointer's stores:
  3786 -> 3711 (pointer 23 -> 38 references, parameter 49 -> 56); wrapping the run of allocation and field stores
  inside it: 3711 -> 3521 (pointer 47 references, priority 0.76, above the parameter's 0.57). Three float stores that
  read one constant, wrapped: 3521 -> 3501 (the constant's priority 0.074 -> 0.158, ahead of the 1.0f flag in the float
  registers). One call wrapped alone: 3576 -> 3436 (another pointer's copy ahead of a neighbour).
- The library family `run_weight_scope` (decomp-repair #1457) offers a compound statement, a run of up to ten
  statements or one statement as one wrapped child. `prove_chain.py --families run_weight_scope --children 800` from the
  saved best: 3786 -> 3616 -> 3421 -> 3361 -> 3301, four steps, stops there.
- None of `call_weight_ladder`, `loop_weight_set`, `local_weight_ladder` or `statement_scope_weight` produced any of
  these children: they wrap calls, loop steps and single statements that name one local.
- A saved best with a `#` line inside the function sends `draft_repairs._spelled` down its macro-masking route, which was
  cut off after 120 s here; `prove_chain --list` offered 16 families' children from the saved best and 40 or more from
  the same text with the nine defines written out (the judge scores both alike: 3786). decomp-repair #1453 makes
  `m2c_spelling.plain` write such defines out (the base must be a struct the text declares, the name defined once).
  Before that, every gap check of a family against a source that kept the defines said "no family finds it".

- The struct/scalar alias rule of sched.c (`true_dependence`/`anti_dependence`: "a MEM_IN_STRUCT reference at a non-QImode
  varying address can never conflict with a non-MEM_IN_STRUCT reference at a fixed address"). The saved best named its
  frame slots as members of one local struct (`locals.sp40`, a MEM_IN_STRUCT reference at a fixed address) and its
  pointer accesses as `*(s32 *)((char *)p + 0x34)` (plain, varying): the one pairing that always conflicts. The sched dump
  printed `insn_list 635` (a true dependence on the pointer store) on the slot's load, so the load stayed behind the store
  and the call's result register stayed live across the slot's test: the test's temporaries took `$v0` where the target
  has `$v1`, and the arms' locals the registers after them. Reading the slots as `*(T *)((char *)&locals + OFFSET)` and
  storing through the pointers as record members (`((__repair_rec_52_s32 *) p)->f = v;`, `char pad[52]; s32 f;`) at once:
  3301 -> 3021 (each half alone 3301); then the slot write `locals.sp30 = 1;` as a plain deref beside the pointer load before
  it written as a record member: 3021 -> 2756 (each half alone 3021). Families: `frame_slot_alias_split` (#1459) and
  `frame_write_alias_split` (#1460). Typing every pointer load and every slot write at once did not repeat it (3021): the
  pair has to be the neighbours that were being ordered.

- Two staging steps, each missing from the library (the exact judge, each against the app's own `compile_and_diff`):
  one local for every arm's leading load (`len = *(u16 *)((char *)p + 0x40); a0 = len * 6;` in all five arms, `s32 len`
  declared once) 2756 -> 2386, any one arm alone 2756 and a name per arm 2281: the shared name is one pseudo of the whole
  function where a name per arm is five block-local ones (`shared_operand_stage`, #1463); and one use of a local that
  held a load (`temp = *(u16 *)((char *)arg1 + 0)`) written as the load again in a call's arguments, after the frame
  stores and a call that came between: 2386 -> 1971 (the record address `table + id * 0x4C` recomputed from `lhu` as the
  target does, instead of the register that held it; `stale_load_reread`, #1470). `frame_write_alias_split` then found
  1971 -> 1911 on the new base. The whole run, 3786 -> 1551 in the app, took four hand edits and the families above; the
  function is not matched (50 register rows left, the arms' local-alloc order of `$v0`/`$v1`/`$a0`).

- After the families above typed the pointer stores as record members, `adjacent_cast_store_swap` no longer saw its
  pairs (it reads `*(T *)(...)` casts only): the swap of two halfword stores of one record, `sh 0x40` and `sh 0x42`,
  was the whole of 1911 -> 1691 (the family reads the member form since decomp-repair #1476). A family that rewrites
  the spelling of a statement hides it from every family that reads the old spelling: after adding one, run the
  neighbouring ones on its output. Two stores of different bases swapped, `*arg0 = rec; ((R *) rec)->f = v;`: 1691 -> 1551
  (`independent_statement_swap`, #1477; 44 children, the edit is the fifth).

Failed experiments that would otherwise be repeated: permuting the three statements of an arm (`var_a1`, `var_a0`,
`var_v0`) gave the same code in all 36 orders (the scheduler's tie-break is not the source order there); writing the
arm's length load into a local first made it worse (3486 -> 4741). A block-move struct assignment
(`*(Rec *)dst = *(Rec *)src`, a 19-word struct) in place of the explicit 16-byte loop and word tail put the loop pointers
in `$a2`/`$a3`/`$t0` in the wrong order (dst first, not src, end, dst) and scored 3701 against 3436 with the loop; a
spelling that changes the order of the three block-move pseudos (502 dst, 503 src, 504 end) is not known.

What is controlled evidence and what is one function: one function. The weighted-reference arithmetic comes from the
`-dl -dg` table and matched it to the digit here; the compiler's source was not read for it (hypothesis). The scopes are
scaffolding no developer wrote; polishing removes them once the function is recorded as matched.
