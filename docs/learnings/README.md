# Per-entry evidence

One file per entry: `docs/learnings/<kind>/<name>.md`, where the kind is

- `function/`: one function's match. The compiler and flags, the source change, the measured score before and after
  and which judge gave it, what failed on the way.
- `finding/`: a compiler behaviour that other functions will meet, stated as the rule (what pass decides it, which
  input it keys on), with the controlled evidence behind it.
- `experiment/`: a failed or negative result that would otherwise be repeated.

Make one with `python tools/learnings.py new function one-shot-scope-before-loop --compiler "KMC GCC 2.7.2 -O2"`,
fill it in, and commit it with the change it records. `python tools/learnings.py index [words]` lists and searches the
entries, `check` validates them (tests/test_learnings.py runs it), `render` joins them into one document.

Why files and not a section of `docs/COMPILER_MATCHING_LEARNINGS.md`: every matching agent appended to that one file,
so every pull request that recorded evidence conflicted with the others. Separate files never touch the same lines, and
no index is committed (it would conflict the same way; `index` reads the files). The older entries stay in
COMPILER_MATCHING_LEARNINGS.md because guides, code comments and the skill cite its sections by title; add to it no more.

The levels, as that file defines them: `verified` (the judge accepted exact bytes; one case is not a universal rule),
`controlled` (undo one aspect of a known solution and a generated repair recovers it), `observation` (the output
changed, the mechanism isn't isolated), `hypothesis` (worth trying, no controlled test yet). Keep game data private:
describe a function by its kind ("a 34-instruction object placer"), never by the game.
