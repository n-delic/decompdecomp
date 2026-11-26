---
title: tail store sched1 priority and a0 pointer
kind: finding
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

Two N64 near-misses that need the per-function `-mfp32` profile, continued from
`colour-row-pointer-inline-and-tail-pointer-a0.md`.
1. A 1585-instruction collision walk, 10 under `-mfp32`, one register: an arm ending
   `func(arg0, y, 1); G->vy = 3000.0f; func2(arg0, 5, 0x100); return 1;` where the target loads the global object
   pointer into `$a0` (`lui $v0,%hi(G); lw $a0,%lo(G)($v0); ... swc1 $f0,0x50($a0); move $a0,$s2`), ours `$v0`.
2. A 378-instruction colour interpolator (three vertex colours in stack arrays, `w`/`u` weights, a tint row),
   2756 under `-mfp32` (7616 under its own `-mfp64` profile).

The source change, and the measured result before and after (scores, the judge used):

Isolated app judge-worker with `-mfp32` added (fast_judge.py --flags=-mfp32).
- Why the collision walk needs a `do { } while (0)` around the store (475 without, 10 with): `passes.py
  --sched-verbose` shows sched1's priorities in that block (priority = longest latency path from the block's start,
  sched.c `priority`): the store is 5 (its pointer load costs 2 and depends on the earlier call), the three argument
  moves 4, so the backward list scheduler places the store last, below `move $a0,$s2`, and local-alloc can never give the
  pointer `$a0`. Ties are broken by insn order (the earlier insn ends up earlier), so the target's order needs the store
  at priority 4 or less, a dependence between the store and `$a0`, or a scheduling barrier (the loop notes of a
  `do { } while (0)`). decomp-repair `run_weight_scope` already writes that fence (475 -> 10 in one step with
  `prove_chain --families run_weight_scope`; a `prove_chain --families all --children 120` run cut it off).
- The colour interpolator's 9846 in the app was a stale historical record (scores keep the header and settings they were
  judged with): `setup.py try` of the unchanged saved best re-judged it 7616 and the app now records 7616.

Failed experiments that would otherwise be repeated:

- Collision walk tail pointer (each fp32; 10 unless noted): the pointer as a function-scope variable read at function
  entry, at the arm's head, or at its declaration, with `extern Obj *const G` (1178-1623: a global pseudo that crosses
  calls is spilled to the stack and reloaded into `$t0`, not rematerialised from the const global, because
  update_equiv_regs only gives block-local pseudos a memory equivalence); the pointer read before the first call
  (`$s0`); `volatile` on the global or the field; `G` as `Obj *G[]` or a `u8 []` with a cast macro; static inline
  setters (`setvy(G, 3000.0f)` 10 fenced / 475, `setvy(3000.0f)` 670, the whole tail as an inline 475); every
  combination of a fence around the first call, the store, the store with a local pointer (set inside or outside the
  fence) and the last call (24 sources, 10 or 475); the pointer held in a function-wide `s32`/pointer local of another
  arm (unfaithful probes: it takes that local's register, `$v1`, `$a1`, `$a2`, `$s0`, `$s4`, `$fp`, never `$a0`); flag
  probes `-fno-caller-saves`, `-fforce-mem`, `-fforce-addr`, `-fno-thread-jumps`, `-fno-peephole`,
  `-fno-function-cse`, `-fomit-frame-pointer` (all 10/475), `-fno-schedule-insns(2)`, `-fno-strength-reduce`,
  `-fno-force-mem`, `-fno-cse-follow-jumps`, `-fno-rerun-cse-after-loop`, `-fno-expensive-optimizations` (worse);
  `compiler_experiments` sn-n64-272-0004 2380, sn-n64-272-970404 6696, sn-n64-272-0001 does not compile.
- Colour interpolator, a from-scratch colour block (natural c0, c1, c2 loads, `t`/`w`/`u`, three channels), with a
  `#define` or `static inline` lerp helper (identical RTL), the tint read inline, through `u8 *` or an `Rgb` struct
  pointer, colours as `f32 [3]` or a `{ f32 r, g, b; }` struct: 3275-5710 (natural load order), 2775 with the saved
  best's load order. Vertex colours read through a pointer reassigned per vertex: 9780-10385.
  `compiler_experiments`: sn-n64-281 -mips2 2756, sn-n64-272-0004/0006 3726, 970404 5136. prove_chain --fp-flip
  --children 60 from the struct form: no child improves.

What is controlled evidence and what is one function:

The sched1 priorities (5 against 4) are read from the compiler's own `-dS` dump of one function; the rule (priority from
predecessors, ties by insn order) is in gcc-2.8.1 sched.c `priority` and the ready-list ranking. That local-alloc gives
the block-local pointer the lowest free register and has no suggestion for `$a0` is from the lreg dump and local-alloc.c
(`combine_regs`, `find_free_reg`); what put the target's pointer in `$a0` is still unknown. Everything else is one
function each.
