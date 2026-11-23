---
title: a frame-submit loop over six views: a byte flag, a clamped float and an indexed table took 555 to 85
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-23
---

The function: a 511-instruction N64 frame-submit routine, an m2c draft whose loop visits six views through a walked
pointer (`entry = table; ... entry += 1`) with a loop flag, a clamped float read, a short-lived view pointer, three flag
bytes read through a base register and a post-loop byte count that reused the flag's name. Saved best 555 (scaffolding
from earlier searches), isolated judge (`fast_judge`, the app's own). `prove_chain --families all` found no child of
any family that improved on it (945 children). The steps, each scored in the isolated judge; one function, an
observation, not a controlled result:

| edit | alone | combined |
| --- | --- | --- |
| split the post-loop web of the flag's name into its own local (`local_web_split` offers it) | 555 -> 590 | |
| retype the loop flag `register s32` -> `register s8` | 555 -> 590 (the later wide store truncates) | |
| both | | 555 -> 390 |
| `do { flag = 1; } while (0)` (one more weighted reference) and the third flag-byte read in its own `do { } while (0)` | 390 -> 360 / 395 | 390 -> 320 |
| the clamp `(v > 0.0f) ? v : 0.0f` as `v = rec->f; if (v < (zero = 0.0f)) v = zero;` with `f32 zero;` at the top | 857 alone (the zero is hoisted into `$f24`) | 320 -> 315 |
| remove the walked pointer, index the table by the counter everywhere | 315 -> 85 | |

What each one changes in the compiler:

* A narrow flag (`s8`) is a promoted-subreg pseudo: its test is read through a `move` into a temporary (`move $v0,$s7;
  bnez $v0`), which is what the target has and what fills the branch's delay slot above it. As a word the test reads the
  register itself. The type alone is no change while the later reuse still stores a wide number into the byte, and the
  split alone only moves the allocation priority (`floor_log2(refs) * refs / live length`: 7 -> 5 weighted references
  puts the flag below the 255 constant and the table pointer above it). One child: `flag_web_narrow`.
* The saved-register order the target has is table pointer > hoisted `-1` > flag > hoisted `255`; at 9 references and
  350 live insns the pointer lost to the `-1` by 6 insns, at 5 references the flag lost to the constant. One `do { }
  while (0)` around one assignment of the flag and one around one read of the pointer is the smallest pair
  (`statement_scope_weight` wraps whole `if`s only; the pair needs `--combine`).
* The zero of an `if (v < 0.0f) v = 0.0f` clamp is a loop movable (savings 1, lifetime 4 insns: loop.c `move_movables`
  hoists it, costing an `sdc1 $f24` and a frame word); a function-level float assigned at the compare (`zero = 0.0f`)
  has references in one block and is left alone (`float_constant_loop_pin`'s finding). The compare direction decides the
  float registers (`c.lt.s zero,v` against `c.lt.s v,zero`). One child for both: `float_clamp_flip_pin`.
* A table written `table[i]` is strength-reduced by loop.c into a giv of the counter whose initial `la` is emitted with
  the other loop invariants after the hoisted constants; a pointer initialised before the loop is its own pseudo and its
  `lui/addiu` comes first. `table_cursor_index` did this but refused every body with a `goto`/label; a forward `goto` to a
  label before both steps steps both, so it is accepted now.

Still different at 85, for whoever continues: (a) the flag-byte base `addiu $s5,$v1,-0x21` is after the `f20` constant in
the target (a loop.c-hoisted movable) while the explicit pre-loop statement comes first; set inside the loop after the
clamp's join, cse folds it into the byte reads, set at the loop's top it hoists ahead of the `-1`; (b) the view pointer is
in `$v0` in the target with `move $a0,$v0` in the float compare's delay slot and the alpha store through `$a0`: a copy
that both the store and the call argument use, the view dead after it. In ours the view is in `$a0` and the store goes
through a copy in `$a1`. global.c `expand_preferences` merges the preferences of two pseudos when one dies in the insn
that sets the other and they do not conflict, so the view inherits the argument register; every spelling tried
(identity inline for the store and the argument, a user copy, `register`, a split of the later webs of the name) either
kept the copy for the store alone or let the allocator coalesce the two into one register.

Failed experiments: moving the flag-byte base into the loop at each statement boundary (it is folded into the reads or
hoists in the wrong place; 85 -> 740..1097), indexing the table by `entry = table + i` per pass (1045), a literal `-1`
instead of the hoisted short local (390 -> 445), the unpinned `if` clamp (857), reading `*entry` for the view (985).

Controlled and not: the 555 -> 85 chain is one function. The families built from it carry unit tests on small sources
and were checked on this function's saved best (390 for the flag child, 315 for the clamp child, 85 for the table
child).
