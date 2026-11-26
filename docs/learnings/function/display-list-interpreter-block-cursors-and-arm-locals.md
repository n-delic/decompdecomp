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

## Continued: 1986 -> 1430 (`-mfp32`; the app records 2036 -> 1480 under the function's `-mfp64` profile)

| edit | score | library |
|---|---|---|
| the two sentinel groups written `min[0..2]` before `max[0..2]` (the target stores `max` first, `0x7fffffff` in `$v0`) | 1940 | `local_store_group_swap` (decomp-repair 0.6.60) |
| the parts pass's empty inner loop given a counter of its own (`{ s32 k_1; for (k_1 = 0; ...) }`); renaming it to another free local stayed 1940 | 1880 | `loop_counter_block_scope` (0.6.60) |
| modify-vertex words `(c2 << 24) \| (c3 << 16) \| (c4 << 8) \| c5` left-associated (the target's `or` chain) | 1890 | (by hand; kept for the next rows) |
| modify-vertex `n = cmd[1]; w0 = (n << 1) \| K` (the target's `$s0`), `texs = hdr->texs;` in a `do { } while (0)` (lifts `hdr` over `cmd`: 0.585 against 0.588 decided `$s4`/`$s5`) | 3405 | (by hand; scaffolding) |
| triangle index `k` written as the parts loop's counter (one function-level local, its `++` keeps the `& 0xFF` masks), the triangle word decoded in place (`b = word; r = b & 0x1F; k = ((u32)b >> 5) & 0x1F; b = (b >> 10) & 0x1F;`), the second word likewise | 2130 | (by hand) |
| `prove_chain` from there: `run_weight_scope`, `explicit_loop_backedge` | 1800 | existing families |
| the first field read after the in-place overwrite through a block-scoped copy, both words (the target's `move $v0, $a1`) | 1685 | `inplace_field_copy` (0.6.66) |
| `prove_chain`: `dead_local_stage` (`n = (u32)b >> 5; kt = n & 0x1F;`) | 1430 | existing |

Compiler facts read from the dumps (SN64 GCC 2.8.1; no source, so hypotheses):

- **The reload register follows the hard registers pseudos use.** The target reloads every spilled value (`index`,
  `skip`) into `$t3` and never uses `$t2` except as the second triangle command's cursor; whenever our case 5 cursor
  landed in `$t1`, every reload moved to `$t2` (40+ rows), and with it back in `$t2` they returned to `$t3`. This fits
  reload1.c's `order_regs_for_reload` (registers no pseudo uses are tried first): the spill register is a consequence of
  the triangle arm's allocation, not of the spill.
- **Masks the target keeps need a variable whose bits combine cannot bound.** `((k << 1) & 0xFF) << 16` keeps its `andi`
  only when `k` is a variable with an unbounded set somewhere (the parts loop's `k++`); a fresh `k = x & 0x1F` lets
  combine fold the masks and the `sllv` by the register holding 1 turns into `sll`. Same for the third index: the word
  decoded in place (`b = word; ... b = (b >> 10) & 0x1F;`) is a 16-bit value in one of its sets.
- passes.py's first-scheduler order was empty for SN builds until #3649 (the dump's `;;` notes filled the 60,000-character
  excerpt), and the dumps were of the `-mfp64` profile until #3646 (`--flags`).

Still open: the parts loop's pointer and counter registers (`$a1`/`$v1` in the target), the modify-vertex loads'
registers and cursor (`$a0`), and the hand steps that rely on scaffolding (`do { texs = hdr->texs; } while (0)`, the
search's `if (0) goto` back edge and one more `do { } while (0)` in case 5).
