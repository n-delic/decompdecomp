# The matching loop

Goal: C that the game's own compiler turns into exactly the bytes in the ROM. Not "equivalent": identical.

1. `claim_function` gives you a function: its assembly, where it lives, what it calls, the data it
   uses, the best earlier attempt (start from it!), notes from earlier agents, and an m2c draft.
2. Read `get_context`: the shared header every compile includes (types, structs, prototypes).
3. Write the function, then `compile_and_diff`. Your object is linked at the function's real address
   and compared with the ROM. Score 0 = byte-identical = matched and saved.
4. Iterate. Change one thing at a time and read the changed instructions as well as the score.
   A lower score can still store the wrong value. Keep a promising scheduling candidate even if its scalar score
   is worse; only the exact judge decides a match.
5. Matched: add what you learned to the codebase so others benefit:
   - prototypes and structs you worked out -> `edit_context` (accepted only if every matched function
     still matches);
   - real names for functions and data -> `rename_symbol` (by address: matches never break);
   - tricks -> `add_note`.
6. `compile_and_diff` says when to move on: no better score in N compiles in a row, where N is the function's own number: its brief names it, and every compile_and_diff result shows where you stand, as in "no better in 2/6 compiles (move on at 6)".
   Then `release_function` with specific notes (what you tried, what the remaining diff is, what you
   suspect). The next agent starts from your best attempt.

## When several agents stall

The brief counts attempts across agents, not just your claim. Recompile the saved best first. If its score does
not reproduce, compare its recorded compiler with the active compiler and check header changes. Unknown compiler
provenance is not evidence that the active compiler is right. Use `try_compiler` to test a hypothesis before
suggesting a narrow compiler range; do not change the whole project's compiler to fix one function.

When the right loads, stores and arithmetic appear in the wrong order, test the types that control alias analysis
before another batch of statement permutations. An integer array may stand for a struct with mixed pointer and
integer fields. Preserve offsets and widths. Use real declarations and the normal byte judge, not pragmas or asm.
The permuter tries at most sixteen such layouts for simple 32-bit ROM initializers, within its existing time budget.

After an exhausted hypothesis, record the compiler/flags, starting source, exact remaining differences and what the
experiment ruled out. A stronger reasoning model should choose the next discriminating experiment from this
evidence. Restarting the same mutations with another agent does not create new evidence.

## Reading the diff
- `<` / `>`: an instruction is missing / extra. Control flow or expression structure differs.
- `r`: same instruction, different registers. Statement order, temporaries, signedness, variable
  lifetimes. Usually the last thing to fix.
- `i`: an operand differs: wrong constant, struct offset, type size, or symbol.
- `o`: the right instruction in the wrong place: statement order or scheduling.
- `s`: a stack offset differs: number, order or types of locals.
- "when linking your code at the function's address": a name the ROM doesn't know, or data you
  defined that differs from the ROM data. Fix these first.

## Priorities
Get the instruction count and control flow right first, then operands, then register allocation.
Plausible original code beats contortions: games were written by people, quickly.
