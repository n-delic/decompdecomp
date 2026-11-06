# The matching loop

Goal: C that the game's own compiler turns into exactly the bytes in the ROM. Not "equivalent": identical.

1. `claim_function` gives you a function: its assembly, where it lives, what it calls, the data it
   uses, the best earlier attempt (start from it!), notes from earlier agents, and an m2c draft.
2. Read `get_context`: the shared header every compile includes (types, structs, prototypes).
3. Write the function, then `compile_and_diff`. Your object is linked at the function's real address
   and compared with the ROM. Score 0 = byte-identical = matched and saved.
4. Iterate. Change one thing at a time and watch the score. Keep the best saved draft, but retain a small alternative when its frame, spill offsets or register mapping improves despite a worse score.
5. Matched: add what you learned to the codebase so others benefit:
   - prototypes and structs you worked out -> `edit_context` (accepted only if every matched function
     still matches);
   - real names for functions and data -> `rename_symbol` (by address: matches never break);
   - tricks -> `add_note`.
6. `compile_and_diff` says when to move on: no better score in N compiles in a row, where N is the function's own number: its brief names it, and every compile_and_diff result shows where you stand, as in "no better in 2/6 compiles (move on at 6)".
   Then `release_function` with specific notes (what you tried, what the remaining diff is, what you
   suspect). The next agent starts from your best attempt.

For an N64 near-match, use the compiler's short checklist: `guide("gcc-kmc")` or
`guide("ido")`. They distinguish context, tail merging/missing logic, lifetimes, declaration
order and layout evidence. Keep compiler-specific experiments separate rather than trying
every compiler's recipes in one search.

## Before a long search

Use this app's `compile_and_diff` and `get_context` for the real project. Do not start a second engine writer
on its database. External coding agents can use the authenticated local API described in
`docs/LIVE_API_WORKFLOW.md`; app-managed agents should keep using their provided tools.

Read the linking diagnostics before treating `i` markers as source errors. An unresolved data name needs
an address established from the target's relocations. Use a real structure return when the source returns
an existing record, rather than manually guessing its hidden pointer ABI. Prefer typed local SDK source,
macros and inline-helper locals when available and authorized; do not invent a shared layout.

Long ROM searches now preflight up to four installed flag profiles, with an input cache and the project's
automatic compiler-change setting. Source/header/target/profile changes invalidate those probes. Imported
projects retain their own build settings. A verified function can still have an export-layout blocker:
constant pools, extra bodies or code before the entry. Preserve the match, resolve that layout separately,
and require the complete image comparison before calling the standalone project verified.

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

Before sending many hand-written variants, use `try_local_repairs` on the current revision. Its bounded
families include `local_widening`, `callee_parameter_width`, `callee_return_width`, `split_integer_lifetime`,
`merge_integer_lifetimes`, `branch_call`, `goto_leaf` and `switch_layout`. They can test lost coordinate bits,
unwanted call conversions, integer register lifetimes and alternative dispatch layouts without model-written
source variants. Candidate-owned prototypes change only in that candidate; confirm a discovered signature
before suggesting a shared header edit. A lower score is useful evidence, not a proof of correctness.

## Register and spill plateaus

Read the compact allocation clues and current-input trial history before requesting another full diff.
A consistent register substitution suggests lifetime work; different stack offsets suggest spill/layout
work; the same displayed instruction multiset suggests scheduling. These are hypotheses, never verdicts.
An explicitly addressed local stack field can use different reload registers from a compiler-spilled scalar.

Use `pointer_argument_alias` to try a separately named pointer for repeated call arguments while leaving
aggregate copies direct. `spill_declaration_order` moves plain uninitialized pointers within mixed
scalar/pointer declaration groups. `byte_fill_constant` tests a representable byte constant in a fill loop.
`allocation_initialization_order` swaps adjacent independent literal/address local assignments.
`allocation_schedule_bridge` combines a lifetime/constant hypothesis with bounded declaration ordering.
The shared integer/pointer split-reuse families handle phase boundaries; unsupported shapes need manual work.
These repairs reach the shared draft/permuter pipeline, including imported projects where candidate C is supported.

Iterative autopilot retains its separate opt-in budget and fair function rotation. It can now follow four
edits and retains different register/spill shapes rather than choosing every parent by numeric score alone.
The normal per-function compile allowance is unchanged. The MIPS ROM iterative phase remains narrower than
the shared local repair pipeline; imported projects use local/deep repairs, not that phase.

Consult the current-parent failed-trial history: unchanged inputs need not repeat hundreds of setup orders
or flag combinations. A changed source lifetime, header, target or compiler profile is a new experiment.
Do not infer original source spelling from an exact generating expression, or count a replay of an already
matched source as a new match. Only the ordinary judge accepts a match.

## After an expensive match

Repair examples can automatically analyze a new captured match with four bounded edit reversions and
supported GCC before/after diagnostics. Results are saved as evidence; matched source stays unchanged.
One analysis runs at a time; busy, unsupported or missing-input cases can use manual replay later.
Use its smaller retained edit set to propose a reusable repair, then test that rule independently.
Different phase dumps, empty conflict sets or deleted pseudos are leads, not proof of causality.
Learned exact sequences are scoped by compiler profile and mismatch shape. Consult per-family efficiency
with its denominators: trial-minute rates use summed compiler-trial elapsed time, not actual CPU minutes.

For shorter repeated-call branches, `branch_pointer_lifetimes` tests conservative shared/local pointer
combinations and terminal-tail distribution, with `branch_pointer_declarations` as a bounded follow-up.
Do not split every variable automatically: preserve mixed-lifetime alternatives and compare instruction
structure before chasing register swaps. These families share existing input caches and compile budgets.

Compact claim receipts and `get_candidate` show pending quality reports only
when their source hash matches the candidate being returned. Treat the report
as an agent hypothesis: inspect `get_function(section="quality")` before spending
trials on the reported revision. An explicitly linked corrected alternative can
be selected with `get_candidate(base="reported_corrected")`, then inspected and
rejudged. A historical lower score does not prove a valid source model. Reports
remain advisory and do not pause work or automatically replace source.
