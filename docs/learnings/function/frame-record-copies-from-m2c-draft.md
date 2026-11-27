---
title: two tables copied to the frame, from the raw m2c draft to exact
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

An N64 function (164 instructions) copies two sound id tables (0x90 and 0x78 bytes) to its frame, overrides some ids,
then stores one page of one table into a queue and plays each id. The raw m2c draft scored 7788 after 49 attempts:
one `u8 sp[296]` with a `#define` per slot, both compiler copy loops written out, a word counter with a masked copy,
and the volume named as its own symbol (`D_address`) although the target reads it at offset 2 of the global before.

A matched sibling's C adapted by hand was exact at once. The library steps below also reach 0 from the draft
(app's judge, one function): `scratch_frame_records` (3698), `guarded_masked_counter_for`, `asm_member_view`,
`target_call_arity` twice, `element_pointer_direct_index`, `m2c_field_record_view`, `independent_statement_swap`
twice, `staged_store_rise` (0).

Controlled measurements on this function:

- The volume as its own symbol and as `*(u16 *)((u8 *)&gVolumes + 2)` both scored 1293 in an otherwise exact source;
  `((View *) &gVolumes)->b` was exact.
- `(&record.data[0])[i]` and element pointer locals kept a record's address in a saved register (a frame 8 bytes too
  large); `record.data[i]` computes it at each use, as the target does.
- The store pair was exact with the volume read into a `u16` local first, then the id stored, then the volume.
- One call after the `if`/`else` fed by a local in each arm was exact: the call does not need to be in each arm.

Not found by search: the steps were picked by shape. Several do not improve the score alone, so a greedy chain is not
shown to take this path.
