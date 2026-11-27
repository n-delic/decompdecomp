---
title: A menu list: byte-by-byte colour record and two stepped table pointers
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

A 51-instruction N64 routine drawing eight list lines, SN GCC 2.8.1 `-G0 -mips3 -O2` (per-function profile). Saved best
(m2c draft, written for another compiler): 1560 in the app's isolated judge. Symptoms: a frame and entry block unlike the
target's because three colour bytes are loaded and stored one at a time, and `move $s0,$v0` / `addiu $s1,$v0,4` in the
preheader coming out in another order and in other registers.

The source change, each measured on the saved best:

- m2c's `sp20 = byte; sp21 = byte; sp22 = byte;` from one address, with `&sp20` passed on, written as one copy of a
  three-byte record: 1560 -> 250. The same score as `s8 color[3] = {...};`, without reading the data
  (decomp-repair `byte_run_struct_copy`).
- The two `s32 *` cursors (started through a local holding the table's address, stepped by 17 words beside the counter)
  written as `((s32 *) START + i * 17)`: 250 -> 0 (`stride_cursor_index`). The `y` coordinate stays a counter of its own.

Failed experiments that would otherwise be repeated:

- `y = 0x3C + i * 0xF` computed from the counter: 1075 with the rest written by hand; `y += 0xF` was exact.
- Turning the `double` read from an extern into a literal changed nothing, nor did the `for (;;)` shape.
- Swapping the two cursor initialisations: 1105.
- `prove_chain` with the older families found nothing below 250 in 40 judged children.

What is controlled evidence and what is one function: the scores above are one function. `counter_companion` wrote a
`T *` companion through `(char *)` although its step counts elements of `T`; that is a source error independent of it.
