---
title: Under -g, gas fills a then-arm jump with its load and pads a multiply, keyed on line entries
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3
evidence: observation
date: 2026-11-22
families: debug_assembler_layout, debug_else_jump_layout, debug_operand_line
---

What the function (or case) looked like and the exact compiler and flags:

A 2,103-instruction N64 vehicle update (KMC GCC 2.7.2, `-O2 -g3`), 870 in the judge with only 23 differing rows,
all from two places: (1) after `if (x > 0) c = k; else c = -k;` the target has `blez; sra; j L; lw $a0, k`, ours
`blez; sra; lw $a0, k; j L+4; li $v0, 10` (reorg copied the join's first instruction into the slot and jumped past
it); (2) the target has `mflo; nop; nop; sra; nop; mult`, ours had no nop before the `mult`. The rest were branch
offsets shifted by the first.

The source change, and the measured result before and after (scores, the judge used):

Read from `mips.md`, `reorg.c` and the pinned `as`:
- `mips.md` gives every `load` `dslot = yes`, so reorg never puts a load in a delay slot. A `j` with a `lw` in its
  slot was left unfilled by cc1 (in `.set reorder`) and filled by gas. Gas swaps the previous instruction in only
  when no label sits between them (each `-g` line entry is a label) and the instruction before that was not in a
  `.set noreorder` block: so a line entry precedes the load, and the load and `} else {` share a physical line.
- A section switch (each line entry is `.section .line ... .previous`) makes gas emit the HI/LO hazard nops it owes
  and forget the history; a plain label does not. `mflo; nop; nop; sra; nop; mult` is a line entry after the
  `mflo` and another between the `sra` and the `mult` (checked by assembling the sequences with the pinned `as`).

Edits on the saved best, each scored with the app's judge (`fast_judge.py`):
- M: the pure assignment `a = v >> 6;` written before the `if` moved after the `else` block (the join then no
  longer gives reorg an instruction to steal);
- L: `c = k; } else {` on one line;
- S: `s32 d = (c >> 10) * s;` as `s32 t = c >> 10;` and `s32 d = t * s;` on two lines.

| edits | score |
|---|---|
| none | 870 |
| L | 870 |
| M | 730 |
| M+L | 2900 (the first place matches; every later branch is one instruction off) |
| S, S+L, S+M | 3570 |
| M+L+S | 0 (the app: byte-identical) |

decomp-repair `debug_assembler_layout` offers M+L+S as one child first; generated from the saved best it scored 0.

Failed experiments that would otherwise be repeated:

A ternary for the arms (4515), the arms inverted or the then-value assigned before an `if` (4215), the clamp
written out instead of the inline helper (3720) or as a ternary (2065), all on one line (870), and the multiply
split across two lines inside one statement (2900: a line entry needs a new statement, not a new line).

What is controlled evidence and what is one function:

The gas behaviours (swap blocked by a label or a noreorder predecessor; nops at a section switch) are controlled
experiments with the pinned assembler. The need for M is one function: why moving the assignment stopped reorg's
steal was not traced to the pass.
