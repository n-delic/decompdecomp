---
title: SN GCC 2.8.1 mesh walker, a zeroed count result and dead-store guards
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-28
---

A 209-instruction N64 function that looks up an optional model by an object's id, walks its triangle groups and a
second list, writes screen triangles and returns how many it wrote. The project's range compiler was KMC GCC 2.7.2
(saved best 3530); the SN profile above, which the matched neighbours use, scored 835 on the same C.

Steps, in the exact judge:

- Loops as `for (;;)` with the exit test first. `for` loops duplicate the test (5740).
- `if (model != NULL) { ...; return count; } return 0;` instead of the early return: 835 -> 800.
- Second loop: `out[count].x0 = verts[table[i0]].x` instead of a triangle pointer and cast arithmetic: 680.
- A named `offset` for the header word, in both loops: 660.
- The corner cursor copied back to the group cursor after the inner loop, not at its head: 350
  (`loop_exit_assign_sink`; alone on the 835 seed: 560).
- The header read through one `hdr` pointer in both loops: 345.
- The count returned through a `u32` result: it first holds the table address (`result = id;
  result = result * 4 + (u32) table; model = *(void **) result;`), then `if (model == NULL) { i = 1; }`, then
  `result = 0; if (model != NULL) { if (clip != NULL) { i = 1; } count = 0; ... result = count; } return result;`:
  345 -> 0 (`zero_result_dead_guard`; alone on the 835 seed: 450).

The target's shape the last step answers: `lw $a0` for the model, `move $v0, $zero` in the delay slot of
`beqz $a0` to the epilogue, and counters set with `move $t4, $zero`.

Both dead stores are scaffolding (`i` is set to 0 before any read). Without the first, the zero is scheduled ahead of
the table load and the result gets `$v1`; without the second, reload copies `$v0` into the counters
(`move $t4, $v0`). That the empty branches work as labels during scheduling and reload's constant reuse is a reading
of GCC 2.8.1's `sched.c`, `reload1.c` and the dumps, not proved for SN's build.

Failed experiments: a plain result variable (result in `$v1`, 775 to 1035); `do { } while (0)` fences round the zero
(120 to 130 at best, the clip load and the output pointer's copy swapped); `u16` return type (an extra `andi`);
KMC 2.7.2 and SN 2.7.2 builds for the final forms; `-fno-schedule-insns` (1945).
