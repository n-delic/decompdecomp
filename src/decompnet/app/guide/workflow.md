# The matching loop

Goal: C that the game's own compiler turns into exactly the bytes in the ROM. Not "equivalent": identical.

1. `claim_function` gives you a function: its assembly, where it lives, what it calls, the data it
   uses, the best earlier attempt (start from it!), notes from earlier agents, and an m2c draft.
2. Read `get_context`: the shared header every compile includes (types, structs, prototypes).
3. Write the function, then `compile_and_diff`. Your object is linked at the function's real address
   and compared with the ROM. Score 0 = byte-identical = matched and saved.
4. Iterate. Change one thing at a time and watch the score; retain the lowest score, but keep a separate semantically justified hypothesis even when its score is worse.
5. Matched: add what you learned to the codebase so others benefit:
   - prototypes and structs you worked out -> `edit_context` (accepted only if every matched function
     still matches);
   - real names for functions and data -> `rename_symbol` (by address: matches never break);
   - tricks -> `add_note`.
6. `compile_and_diff` says when to move on: no better score in N compiles in a row, where N is the function's own number: its brief names it, and every compile_and_diff result shows where you stand, as in "no better in 2/6 compiles (move on at 6)".
   Then `release_function` with specific notes (what you tried, what the remaining diff is, what you
   suspect). The next agent starts from your best attempt.

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


## A stalled call-argument mismatch
First map each target argument register to its value and check the callee's parameter use. A lower score
can come from reversed arguments; a corrected call can initially score worse because its loads move too.

Try a small, named experiment: move an existing adjacent increment into a static inline helper that returns
one literal argument, then use that helper in the call. For example, `p->count++; send(p, 2.5f, 9.25f);`
can be tested as `send(p, 2.5f, next_value(p));`, where `next_value` increments the count and returns 9.25f.
Test each literal position independently. Other arguments must not read the modified field or have side
effects whose ordering changes. This may alter compiler scheduling even when ordinary local temporaries
do not. No assembly or pragma is needed. In project mode, insert helpers with `edit_file`, not in the
single-function submission. The ROM permuter tries a bounded subset of these shapes automatically.

Compile the complete candidate and use the strict judge. Do not replace it with an empty body to satisfy a
claim budget. Record the source shape, compiler, result and remaining diff; failed variants establish only
that those variants failed, not that C cannot match or that the compiler backend needs changing.
