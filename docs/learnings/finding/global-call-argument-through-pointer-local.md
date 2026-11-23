---
title: global call argument through pointer local
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
families: call_global_pointer_arg
---

What the function (or case) looked like and the exact compiler and flags:

A 459-instruction probe handler (KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) at 60 after
`probe-handler-webs-staged-args-and-const-global.md`: two reordered rows at an 11-argument display call after its
loop, `show(0, gMessage, buffer, -1, -1, 1, 0, 0, 6, 0, 60)`. Target `move $a0,$zero; lui $a1; lw $a1,gMessage;
move $a2,$s4`; ours had `move $a2,$s4` before the `lui/lw`.

The source change, and the measured result before and after (scores, the judge used):

`{ const s32 *state = &gMessage; show(0, *state, buffer, ...); }` at that call only: 60 -> 0 (app judge; recorded
matched). A matched sibling function in the same file already spelled its call this way. decomp-repair
`call_global_pointer_arg` emits it (child for that call 0, other calls' children 60, all calls 0).

Why, from the cc1 dumps (`-dr -dc -dS`): `calls.c` copies the argument `(mem/u (symbol_ref))` into a pseudo before
the stack arguments' stores. Outside a loop nothing folds it back: combine refuses a MEM source across stores
(`use_crosses_set_p`, `mem_last_set`), loop.c only folds it inside a loop for a `const` object, and local-alloc's
`update_equiv_regs` only replaces a once-used equivalence that spans blocks. The first scheduler then places the
pseudo's load (blocked behind the stack stores on the memory unit) after the `$a2` move. Through a pointer local the
`.rtl` shows `(set (reg a1) (mem (reg p)))` emitted as the argument move itself, in argument order, and combine
folds the constant address in (a constant source may cross stores). Why calls.c skips the copy there is read from
the dump, not from the cost code: hypothesis.

Failed experiments that would otherwise be repeated:

`gg = G;` staged in an earlier block (before the inner `if`, before the outer `if`): `move $a1,$s1`, worse.
`*(volatile s32 *)&G`: 285. `*(s32 *)&G` (cast dropping const): 0 too, but the pointer local is the sibling's spelling.
The earlier entry's ~20 loop wrappers and the 144-combination grid did not touch the argument's expansion.

What is controlled evidence and what is one function:

One function (one call); the RTL difference is read from the dumps of that source.
