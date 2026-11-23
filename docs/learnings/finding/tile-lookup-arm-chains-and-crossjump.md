---
title: tile lookup arm chains and crossjump
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 190-instruction tile/collision lookup (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, judge: the app's) with ten arms,
each computing `bank + row * K2 + index * K1` as a pointer and loading one or two bytes: five arms over a row-selected
bank (`row * 0x860`, `index * 0x86`, a table symbol) and five over one table (`index * 0x86` + symbol). Two arms (the
tests `(a + 0x80) & 0xFF < 2` and `(a + 0x7C) & 0xFF < 2`) have identical chains that the target does NOT merge. The
saved best was 335 (scaffolded with union views, identical-arm guards and phantom locals); m2c's draft 6175. Every row
of the saved best's diff was a register or an instruction order, none a different instruction.

The source change, and the measured result before and after (scores, the judge used):

The arms re-written one named edit at a time (each worse or equal alone; the numbers are the judge's score after the
edit joined the earlier ones): bank address in a local, row product in a one-shot scope, index term as the pointer operand
of the sum (`(u8 *)(i * K1) + (int)bank`) 6175 -> 2005; the later copy of the masked parameter reusing the dead
parameter's name (`temp_a2` for the second `& 0xFF` local) -> 1140; a `u8` copy of the parameter for the masks
(`cmd = arg0; t = cmd & 0xFF`, a QImode move cse does not propagate) -> 905; the small term's add of the one table arm in
a one-shot scope -> 670; the arms' shared tail copied into each arm, `||` arm with locals of its own -> 295; both loads
before the shift (`a = p[0]; b = p[1]; a = a << 7;`) -> 260; the last arm's `return *p << 7;` written in the arm with its
own pointer instead of `goto` to a label the sibling arm also reaches -> 0.

What decides each, read from the 2.7.2 source and checked on micro-functions with `cc1 -dl -dg`:

- local-alloc.c (`qty_compare`): quantities of a basic block take registers in order of `floor_log2(refs) * refs * size /
  (death - birth)`, each the lowest free; a `do { } while (0);` adds loop depth to every reference inside (flow.c), so it
  moves a chain up that order at no instruction cost. `combine_regs` ties a sum's output to the *first* operand that
  dies, in the insn's operand order, so `(u8 *)(i * K1) + (int)bank` (pointer operand first) puts the result in the index
  term's register and `bank + i * K1` in the bank's.
- A pseudo set in more than one arm (or live across a label) is global: local-alloc skips it and global.c colours it
  after every arm's quantities, so a pointer shared by two arms (`goto L; L: return *p << 7;`) swaps the other quantities'
  registers (micro-test: local pointer -> the index value takes `$v1`, shared pointer -> `$v0`).
- jump.c's cross-jumping (the final `jump_optimize (insns, 1, 1, 0)`, after sched2) merges two jumps to one label that
  are preceded by identical insns, the whole run back to the conditional jump (`rtx_renumbered_equal_p`: a commutative
  operation matches in either operand order, registers by hard register, modes must agree) *unless the jump's label was
  created during that pass*: `jump_chain` partners are only searched when `INSN_UID (label) < max_uid`. Identical arms
  therefore stay separate when each arm carries its own copy of the shared tail: the first cross-jump (tail with the code
  before the label, which needs only one matching insn) makes a new label inside the sibling's tail, the arms' jumps are
  redirected to it, and the arms' chains are never compared. An arm whose chain differs only in the *form* of one insn
  (`(u8) x` zero_extend against `x & 0xFF` and) is merged from the first insn that matches onward, a partial merge.
- A second insn kept on purpose: `tb = x & 0xFF; if (c1 || (tb = (u8) x, c2))` keeps both assignments (cse knows
  `(and x 255)` equals the first, not `(zero_extend (subreg:QI x))`), which is how the target has `andi v0,a1,0xff` in the
  slot of both tests.
- reorg.c (`fill_slots_from_thread`): the delay slot of `if (C) return 0;` takes `li $v0,0` from the target thread
  unannulled (`bnez`) when `$v0` is dead on the fall-through path and with annulling (`bnel`) when `mark_target_live_regs`
  has it live; the original form with a private pointer gave `bnel`, the inverted form (`if (!C) { ...; return x; }
  return 0;`) and a pointer shared with another arm gave `bne`.

Failed experiments that would otherwise be repeated:

`if (arg1 || garbage) { A } else { A }` and `(b * 2) * 67` (fold-const reduces it) as ways of keeping identical arms
apart: the first only works through a dead read, the second compiles to the merged chain; `(u8)` on one arm's index term
keeps the head of the chain apart but the identical suffix still merges. Wrapping the operand-order-sensitive add in a
scope without making the index term the pointer operand moves nothing. Many-way annealing over spellings stalls at the
last arm until the return is private; the local-alloc priority formula alone could not explain which quantity took `$v0`
(a global destination removes the final add from the chain's quantity: micro-tests `t1`/`t3`).

What is controlled evidence and what is one function:

One function, with each edit measured in order by the judge; the micro-function readings (a local pointer against a
shared one, `bnel` against `bne`) were controlled with `cc1`. decomp-repair `tile_arm_chains` (bank_index_first,
byte_entry_copy, load_shift_split, zero_arm_inversion, shared_return_split, label_run_copy, dead_local_reuse) offers the
steps; `prove_chain` with only those families took m2c's draft from 6175 to 1460, the rest of the route needs the existing
weight and web-split families too.
