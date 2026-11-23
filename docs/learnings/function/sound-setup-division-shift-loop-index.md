---
title: sound setup division shift loop index
kind: function
compiler: gcc-2.7.2-kmc, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function looked like: a 157-instruction sound-engine setup function (N64, KMC gcc 2.7.2, `-O2 -G0 -mips3`):
a clamp, two calls, a buffer-size computation `((x * ms / 1000) >> 6) / 3 * 0xC0 + 0xC0` with an allocator call through
a global function pointer, a float-to-int rate computation, and a byte copy loop `dst[i] = src[i]` with an `i & 0xFF`
index. The saved best was a long scaffold chain (an identical-arm `if (var_v1)` around the loop body, a masked-index
local, an identity inline, a `do { } while (0)` fence).

The measured steps (the app's judge, scoring version 2; each on the previous step's source):

| step | score | decomp-repair family |
|---|---|---|
| saved best, re-scored | 575 | |
| first quotient staged in a local, shift moved after the `/ 3` | 280 | `quotient_chain_stage` (0.5.698) |
| masked index local inlined, increment moved after the stores | 235 | `loop_index_inline_guard` (0.5.699) |
| the guard's condition a global read instead of `var_v1` | 195 (240 alone) | same module |
| loop counter split from the argument variable and its start in a one-shot scope | 35 | `reused_counter_split_scope` (existing) |

`prove_chain.py --families all` from the 575 source reaches 35 through exactly these three families.

What each step changes (hypotheses unless marked):
- `(a / 1000) / 3` is folded into one division when written in one expression: the staged local keeps two multiplies,
  which is what the target has (`multu` by 0x10624DD3, `srl 6`, `multu` by 0xAAAAAAAB, `srl 7`). Measured: the inline
  `/ 1000 / 3 >> 6` scored 1225, the staged local 280.
- Two index pseudos (`andi` for the load and the store, another for the test) are local-alloc'd; one index local set
  twice is a global and takes `$v1` while the byte loads take `$v0`, the other way round. Observed in the `-dl` tables.
- Observed with `-dS`: the first scheduler puts the store after the test load because it has the longer chain and the
  load-to-branch stall leaves it the only ready insn at T-2. A guard around the body (identical arms) puts the store and
  the test in different blocks, so the target's order (store, then the next index, then the test) comes out. The
  guard's condition has to be a read that stays dead: a local such as the float result would stay live through the loop.
- A load whose destination register is the same as its index register cannot use the compiler's explicit
  `lui/addu/lb` split and comes out as the assembler macro with `$at` (the target's test load); a store never splits.
  That is how the index and the byte value swapping registers shows in the rows (macro vs explicit form).

What did not match yet (35, seven register rows): the target keeps the first quotient in `$a2` (the register of the
argument it came from and of the size passed to the allocator), the product in `$v0` and the call address in `$v1`.
Naming one variable for the argument, the quotient and the size gives those registers (`$a2`, `$v1`, `$v0`) but moves
the call-address load after the size's `addiu` and then gives it `$v0`; keeping separate locals ties the product to the
size's register. Statement order, spelling of the `* 2`, a local function pointer, a volatile pointer and every naming of
the four values (about 1,200 combinations) were scored: none is better than 35.

Failed experiments that would otherwise be repeated: a natural `for` loop with a `u8` index (605-805); `const` on the
source array (no change: no memory dependence between the two symbols); `volatile` arrays (worse); `first = D[0]` removed
(the entry test then comes out as `lbu`).

Evidence: one function; the three steps were each reproduced from the library's family output with the app's judge.
