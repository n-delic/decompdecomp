# The matching loop

Goal: C that the game's own compiler turns into exactly the bytes in the ROM. Not "equivalent": identical.

1. `claim_function` gives you a function: its assembly, where it lives, what it calls, the data it
   uses, the best earlier attempt (start from it!), notes from earlier agents, and an m2c draft.
2. Read `get_context`: the shared header every compile includes (types, structs, prototypes).
3. Write the function, then `compile_and_diff`. Your object is linked at the function's real address
   and compared with the ROM. Score 0 = byte-identical = matched and saved.
4. Iterate. Change one thing at a time and watch the score; keep what lowers it.
5. Matched: add what you learned to the codebase so others benefit:
   - prototypes and structs you worked out -> `edit_context` (accepted only if every matched function
     still matches);
   - real names for functions and data -> `rename_symbol` (by address: matches never break);
   - tricks -> `add_note`.
6. `compile_and_diff` says when to move on: no better score in N compiles in a row, where N is the function's own number: its brief names it, and every compile_and_diff result shows where you stand, as in "no better in 2/6 compiles (move on at 6)".
   Then `release_function` with specific notes (what you tried, what the remaining diff is, what you
   suspect). The next agent starts from your best attempt.

## Over-split functions
Sometimes a symbol is not a whole function but a piece the analysis cut off the one before it: it has no
prologue and uses registers or a stack frame set up before it; it is an epilogue, or a tail that never
returns and runs straight into the next symbol; or a switch's cases sit in the next symbol. Such a piece can
never match on its own — don't body it, stub it or compile it alone. Call `join_functions(the piece, the
symbol before it, why)` to make them one function again. This works across overlays too: if the two are
adjacent in the ROM they join even when their names or units differ (a source-file name on one, an overlay
label on the other). A join takes effect once agents of two models agree on the same pair, so if a note
already calls a function a piece of the one before it, cast your vote with join_functions instead of
re-noting it or retrying a standalone match. When it lands, the earlier symbol is the whole function: claim
it and match that.

A function can be cut the other way too: it begins one or more instructions *late*, because the symbol before
it was drawn too long and swallowed its first instruction(s). The tell is an epilogue that tears down a stack
frame (`addiu $sp, +N` before `jr $ra`) with no matching allocation (`addiu $sp, -N`) at the top — it can't
match, its prologue is missing. Give the instruction(s) back: `split_function` the symbol before it at this
function's true start, then `join_functions` the peeled piece onto this one. (Overlay games carry the same code
in several ROM copies; if one copy is already matched, re-bounding this one makes it that copy's twin and it
matches for free. If you can't re-bound it, set it aside with release_function(..., not_c=true) rather than burning compiles on a match it can't reach.)

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
