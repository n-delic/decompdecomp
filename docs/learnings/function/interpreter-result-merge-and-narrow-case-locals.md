---
title: interpreter result merge and narrow case locals
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 1315-instruction N64 sound-sequence interpreter of interpreter-arms-inline-helpers-and-tails.md and
interpreter-voice-scan-clamp-and-tail-steps.md (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2` with the app's `-G8`, the
app's judge), continued from its saved best 2510. Not matched: 2510 -> 1735 in this run, every step a library child
(decomp-repair family in brackets).

The source change, and the measured result before and after (scores, the judge used):

1. The two pitch commands, `{ s16 pitch = *(s16 *) &G; if (pitch >= 0) v = conv(pitch); else v = -conv(-pitch); }`,
   with the result variable holding the pitch too (`v = *(s16 *) &G; if (v >= 0) v = conv(v); else v = -conv(-v);`):
   the block's own pseudo had only the call's argument-register copy preference (`lh $a0; bltz $a0; jal; nop`); as the
   result it also takes the calls' `$v0`, so the load is in `$v0` and the argument is the delay-slot `move $a0, $v0`.
   One site 2290, both 2070 [`arm_input_result_merge`]. The earlier note "every spelling compiles the same" was right
   for spellings of the arms; the cause was which variable the pitch lived in.
2. The indirect-variable write (`x = W >> 16; if ((W >> 8) & 0xFF) f(v, x & 0xFF, d); else { x &= 0x1F; o = x * 2;
   ...stores at o... }`): the tested byte read first into a `u8 flag` (the index's shift then ends the word's life and
   ties to its register), a `u8` copy of the index before the `if` for the call (the delay-slot `move $a1, $v1`), and the
   two stores indexed by `x` instead of the offset computed before the inner `if` (a pseudo of both arms that took
   `$a0` from the stored value). Only together: 2070 -> 1910; alone or in pairs 2000..2170 [`byte_test_index_split`].
3. m2c's function-level word locals that one case reads only as a byte or halfword, declared in that case's block at
   that width: the slot command's `(t & 0xFF) * 0xC` as a `u8` (its `srl 8` no longer ends the word's life: `srl $s0,
   $v0, 8; srl $v0, $v0, 16`), 1910 -> 1880; the group-link clamp's sum as a `u16` (a separate mode, so the sum is made in
   `$v0` and copied to the call's `$a1`), 1880 -> 1770 [`case_local_narrow`].
4. `prove_chain --families all` from 1770: `arm_copy_chain` copies case 0x30's sum through two locals (1735); that is
   search scaffolding, not the game's source, and is only a lead that 0x30's sum wants another pseudo.

Findings about the remaining layout (release code after case 6, `default:` last), read from jump.c (2.7.2):

- `jump_optimize` cross-jumps an unconditional jump first against the insns before its own label (the fall-through,
  minimum 1 insn), and only if that fails against the other jumps to that label (`jump_chain`, latest jump first,
  minimum 2, or 1 when the jumping side reaches a label), and only for labels older than the pass
  (`INSN_UID (label) < max_uid`). A label made by `do_cross_jump` (`get_label_before`) is new, so arms redirected to
  it are never compared with each other; an arm redirected to an old label (a case label) is compared again in the
  next sweep. With `default: r = 0;` last, every `r = 0; break;` arm meets the default's `a0 = 0` by the first rule and is
  redirected to the old default label; once the marker arms' skip labels are threaded away, their whole bodies match
  and merge (24380 from 2510). The target's marker arms jump to a new label in case 0xB's tail instead, so in the
  target the first rule failed for them: at the time they were processed the insn before the loop test was not the
  default's `a0 = 0`. Which source puts something there that is gone after the pass is not known.

Failed experiments that would otherwise be repeated:

- The command fetch with the global declared larger (`extern u32 G[];` with a macro for the scalar): the fetch's store
  still folds to `%lo` and loop.c hoists the arms' `&G` sets into `$s5` (6961).
- Case 0xB's sum, operand orders and staged locals (`hi + lo`, `lo + hi`, the difference in a local, `+=`): 1915 or
  worse; the rotation is local-alloc's order in that block.
- The voice-slot helper's `x`/`y` declaration order, compare order, and assignment orders: 1770 or worse.
- The pan clamp's `andi` (sum read back, `F = sum = E`, `sum = F = E`, an `s16` reread): 1770 or worse.
- Case 0x39 as `if (t <= K) v = t << 15; else v = C;` with one value variable for 0x30 and 0x39: 1795, the value in
  `$a0`, and 0x30's `sll` still merged into 0x39's.
- The group-link sum's operands swapped (`G + F` instead of `F + G`): 1920.

What is controlled evidence and what is one function:

One function; each step measured in order with the app's judge (fast_judge, each new best confirmed with
compile_and_diff), and each family's child reproduces the step's score from the state before it. The jump.c reading is
from the source; the register explanations are hypotheses read from the output.
