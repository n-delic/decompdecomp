---
title: o1 very large state machine and cell loop from the assembly
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

Three open N64 functions of 1950, 2419 and 3756 instructions whose project default is `-O2` and whose machine drafts
scored 79000 or worse under either level. All were written from the assembly by hand or with a small generator.

**1950-instruction menu state machine: exact.** Plain full C under `-O2` scored 90145; the same C under `-O1` 3115.
The tells: symbol addresses passed as call arguments are rebuilt at each call, and no saved register holds a constant.

- the index byte is one `s32` reused for a `(u16)` loop counter, with `(u8)` casts where the target has `andi 0xff`
  (the target shows `sll` without a mask where the plain variable indexes);
- a `s8` global read twice, and a `u8` global `|=`, each go through a pointer local in their own block
  (`lui; addiu` into one register, then `0x0($reg)` twice): 2580 -> 0, now `block_held_pointer`;
- a callee reached with an unset `$a0` and `nop` in its delay slot takes no argument.

**2419-instruction loop that builds one quad per cell: exact on the first compile (97535 -> 0).** Six key ranges, each
an `if / else if` chain on the cell type with eight `(f32)(v + 0 or 1) * 400.0f + 400.0f - 200.0f` stores. A short
interpreter over the assembly read the eight offsets of each of the 16 arms, so none was transcribed by eye. The loop
counter, the next index and the key are `u8`; edge normals are written as whole expressions, repeated.

**3756-instruction grid navigation routine: not exact (142572 -> 10515 under -O1).**
- a guard that jumps over a state must be `if (a == 0xFF || b == 0xFF) { return 0; }`: written as a nesting `if`, cse
  carried the loaded byte into a call argument and moved one key variable to another register (15080 -> 12825);
- `cond ? 3 : 1` becomes store-flag code in the target too, but `return 1` / `return 0` arms are branches there: an
  arm that falls through to the outer `return 0` kept them as branches in several places (12825 -> 9545).

Left there: repeated two-flag result blocks are cross-jumped together by our source and kept apart by the target.
