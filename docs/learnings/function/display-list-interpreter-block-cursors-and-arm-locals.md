---
title: display list interpreter block cursors and arm locals
kind: function
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mfp32
evidence: controlled
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 618-instruction N64 model loader and display-list interpreter: header relocation, vertex bounds with a `sqrtf`
radius, parts and texture tables relocated, three header commands, then `for (;;) switch (*cmd)` over eight command
kinds (matrix, vertex load, two modify-vertex, one and two triangles, texture, end). SN64 GCC 2.8.1
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32` (the target saves only `$f20`), isolated app judge-worker.

The source change, and the measured result before and after (scores, the judge used):

From the saved chain result (9751 under `-mfp32`), one named edit at a time:

| edit | score | library |
|---|---|---|
| `if (0) { debug print }` as `if (debug)` on `int debug = 0;` (the target tests a register holding 0) | 9166 | `zero_guard_local` |
| `f(gfx, c ? 1 : 0)` as one call per arm, `cmd += 2;` above the `if` (op[1] read through `op`) | 8756 | `ternary_call_arms` |
| modify-vertex words: `b = cmd[1] << 1; c = <w1>; w0 = b \| K; w1 = c;` in both cases (the tails cross-jump at the `or`) | 7051 | (by hand) |
| texture relocation through one reused local, the `kind > 0` arm out of line, `func_80024324(s32)` | 5566 | (by hand) |
| every command through a block-scoped `Gfx *_g = gfx++;` (the Gbi macro expansion) | 4241 | `gbi_block_cursor` |
| vertex-load loop counted with `i` (the target's counter shares `$s2` with the init loops) | 4226 | (by hand) |
| gSPVertex word with its opcode first (`0x01000000 \| (n << 12) \| ...`) | 4001 | `gbi_opcode_first` |
| one pointer local per command arm (`op` split into three: the target has them in `$s4`, `$a3`, `$s2`) | 2871 | `local_web_split` (exists; cut off by `--children 350`) |

Then `prove_chain --families all --fp-flip --children 2400` from 2871: independent_statement_swap 2651,
switch_variable_split 2456, store_run_order 2261, statement_slide 2066, local_web_split 2011 (the triangle `dl` and
`k` per arm), loop_role_rename 1996, reassociate 1991 and 1986 (integer `|` chains only). Each step took about ten
minutes at 2400 children; with `--children 350` (the earlier run) the op split and these steps were cut off. The app
records 2036 under the function's own (fp64) profile; under `-mfp32` it is 1986, not exact, so the profile is unchanged.

Left (1986): the spill register reload picks (`$t3` in the target, `$t2` ours, 49 rows: the target's triangle arms use
`$t0`-`$t2` for their values), the modify-vertex cases' `n` in `$s0`, and the triangle arms' copy of the packed index
word (`move $v0, $a1` before the `sra` that overwrites it).

Failed experiments that would otherwise be repeated:

- The `?:` call split alone (10446) or with `cmd += 2` after it (10446): the update must move above the `if`.
- Modify-vertex words with `n = cmd[1]` (the target's `$s0`): 8116-4301, every saved register shifts.
- Renaming the vertex-bounds loop counter (`g`) to any other local: 6500-6971.
- `k = 0;` before the vertex-load call (the target's `move $s2, $zero` in the `jal` slot): 5141-6473; the loop's
  counter is `i`, and reorg fills the slot from after the call.
- Matrix command orders (`cmd += 3` first, staged w1, `* 64`): invariant or worse.
- Triangle words with the opcode first: 4206-6111; triangle statement orders: invariant.

What is controlled evidence and what is one function:

Each row is measured from the row above in the isolated judge (controlled); the allocation explanations (block-scoped
pointers and per-arm locals lowering global.c's long-lived pseudos) are hypotheses from `-dl -dg` dumps of this one
function.
