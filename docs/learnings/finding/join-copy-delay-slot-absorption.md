---
title: a join copy followed by a dispatch: cse folds it away, then reorg absorbs it into the branch's delay slot
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

**Function and compiler.** A 92-instruction N64 script-op handler (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`): two
copies of one "read an operand" block (a flag word selects a call, otherwise a 16-entry register file or a global
table), then `switch (op)` over two cases. Saved best 345, never reached 0 in this session; the reading below is of the
rows that were left, with the compiler's own dumps (`cc1 -dr -dj -dc -ds -dt -dl -dg -dS -dR -dJ -dd`).

**What the target does.** Each operand block ends in a copy into the operand's own register (`lh $v0,..; move $a0,$v0`,
`jal; move $a0,$v0`), the three paths meet at the dispatch (`andi $a2,$s4,0xff; beqz $a2,case0`), and the copy sits in
the delay slot of each path's `j` (the arm that falls through has it before the label). The `beqz`'s own slot holds the
`li $v0,1` of the next compare.

**Rules read in the compiler's source (cse.c, local-alloc.c, combine.c, jump.c, reorg.c), each reproduced with a dump.**

1. *The operand's later uses vanish into the temporary.* A multi-set temporary `value` set in the arms and copied at the
   join (`b = value;`) is not a quantity local-alloc ties, so the copy survives allocation only if cse leaves it. cse's
   `make_regs_eqv` makes the new register (`b`) the canonical one of its quantity only when it lives beyond the extended
   block or first appears before it; otherwise the older `value` stays canonical and every use of `b` in the dispatch's
   case arms (the extended block follows the taken branches whose labels have one use) is rewritten to `value`. `b` then
   dies, the copy is deleted, and `value` takes `$a0` directly (`lh $a0,..`). The first cse pass rewrites the last case
   first, so a second pass (cse2 ignores NOTE_INSN_LOOP_END) finishes the earlier one. Any reference to `b` before the
   join (an initialisation, a use in an earlier arm) keeps `b` canonical and the copy.
2. *Combine merges a copy kept inside an arm.* `value = s->regs[v]; b = value;` in one block is `lh $a0` after combine
   (the load's destination dies at the copy), so per-arm copies of a freshly loaded value cannot reproduce the target's
   `lh $v0; move $a0,$v0`. A copy of a value the arm does not just load (a pointer, a parameter) survives; so does one
   across a call (combine's `INSN_CUID (insn) < last_call_cuid` test), which is why the other operand's copy at its join
   is never merged.
3. *The join copy is absorbed by the dispatch branch.* reorg's from-above scan (`fill_simple_delay_slots`) walks back
   from `beqz $a2` over the `andi` (it sets `$a2`, which the branch needs) to the copy, which conflicts with nothing, and
   puts the copy in the branch's slot. The paths' `j`s are filled afterwards from the target thread, so they take the
   `andi` instead of the copy and the `beqz` has no slot left for the `li`. That is the whole remaining difference: three
   rows, `andi`/`move` positions and one branch offset. The scan stops at a CODE_LABEL, a jump, a SEQUENCE or an `asm`, and
   anything it passes is marked: a volatile access anywhere between the copy and the branch makes every candidate
   conflict (a volatile store `frame[0] = 0;` there scored 100 = the target plus that one `sw`, everything else
   byte-identical; not a faithful program, so not used).
4. *A label between the copy and the dispatch would stop the scan, but jump2 removes it.* With the copy in the arms and
   the paths jumping to a `disp:` label after it, the cross-jumping pass that runs after sched2 (`jump_optimize (insns,
   1, 1, 0)`) redirects each jump to the label before the identical preceding copy (a simple jump to a label needs one
   matching insn), `disp` loses its uses and is deleted, and the copy is absorbed as in 3. Whatever the original source
   did, a jump to the dispatch label must survive cross-jumping, i.e. its preceding insn differs from the insn before
   the label.

**What was measured (judge-worker scores on that function; lower is better, saved best 345).**

| source | score | what it shows |
| --- | --- | --- |
| join copy, operand not referenced earlier | 345 | rule 1: no copy in the output |
| same plus `s32 b = 0;` at the top | 485 | the copy exists (rows: only 3 and the first block) |
| the two blocks as one `static __inline__ s32 getv(Seq *, u8 sel, u8 idx)` called twice (`a = getv(..); b = getv(..);`) | 890 | the first block's `lw`/`srl` order and registers match the target with no `code`/`u8` scaffolding; the copies are gone again (the inline's return value is the destination itself) |
| the helper with a local `value` and `return value;`, plus `b = 0;` | 1300 | copies exist again, registers off |
| that, then `local_weight_ladder` twice (`prove_chain.py --families all`) | 460 | every row right except rule 3 |
| `s16 a, s16 b` with the helper returning `s32` | 1085 | the first operand's copy is kept (HImode destination, no cse fold) |

**Not a match, and what would be.** Nothing here reaches 0: the target needs the copy in front of a surviving label (rule
4) without a volatile access, which no source tried here produced (per-arm copies, shared tails with `goto`, a `disp:`
label with `&&disp`, helper returns of every width, `s16` operands, volatile reads). The next agent should look for a
construct that leaves a jump to the dispatch label after cross-jumping, or an insn between the copy and the branch that
sets `$v0` (the `li $v0,1` of the second compare placed before the first `beqz` would do: copy conflicts, `li` fills the
slot, exactly the target).

**Evidence level.** One function; rules 1 to 4 read from the compiler's source and reproduced with its dumps. The score
table is controlled on this function only.
