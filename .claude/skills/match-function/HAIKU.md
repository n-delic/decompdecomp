# Match one function (small models)

One run is one function. Do not change the repository, open pull requests, or touch the app. ROM projects only.

## Commands

Scripts: if you were not given a path, extract them first (never pull or edit the primary checkout):

    git -C REPO fetch -q origin && mkdir -p /tmp/dd-skill && git -C REPO archive origin/main .claude/skills/match-function/scripts | tar -x -C /tmp/dd-skill
    H=/tmp/dd-skill/.claude/skills/match-function/scripts/haiku.py

1. `python3 $H next SLUG --repo REPO` (add `FUNC` after SLUG if you were given one). It claims a function and prints
   everything: assembly, saved C, draft, any near-miss an earlier worker left, scores per profile, the matched twin's C,
   header declarations, neighbours. Note the holder name.
2. Write the whole function into `/tmp/dd-HOLDER/work.c` (callee prototypes and externs the header lacks go on top).
3. `python3 $H try HOLDER /tmp/dd-HOLDER/work.c`. Read the scores and rows, change one thing, try again.
4. Repeat about 15 times. Do not stop after 2 or 3. Stop early only on MATCHED.
5. `python3 $H done HOLDER` (add `--note "what you tried, what is left"`). ALWAYS run it, matched or not. It submits
   your best C, runs the repair library on a close one under another profile (a few minutes; it may match it),
   keeps your best under another profile for the next worker, writes the note, releases the claim and
   removes the session. Never delete the folder yourself.

Everything else (profiles, submitting, saving a match under another profile, claim token, release) is automatic.
If a command says the app is busy, run the same command again. If it says the function is no longer yours, run `done`.
Do not search the data home or project folders with `grep`/`find`: `next` already prints the code worth reading.

## What has worked

- **Matched twin shown?** Copy its C and change the names and constants; judge under its profile (marked `(twin)`).
- **Near-miss shown?** Start from it (`start/near.c`), under its profile.
- Otherwise **start from the `Closest start` file** (`cp` it to `work.c` as printed) and change one thing at a time,
  even when it looks messy: a rewrite from scratch usually scores far worse. Rewrite only when it stops improving.
- **Judge under `-O1` too**, not only `(own)`: lots of loads and stores to the stack and unfilled delay slots mean it.
  Then aim for 0 in THAT row; `try` saves it for this function.
- **Get global types right**: `lb`/`lbu` = s8/u8, `lh`/`lhu` = s16/u16, `lw` = s32 or pointer, `lwc1` = f32.
  `slt` = signed, `sltu` = unsigned. A `lui`+`lw` of the same base at several offsets is one struct.
- **Reorder independent stores and statements** to match the order of the `sw`s in the target.
- **Display-list code**: one block-scoped pointer per command
  (`{ Gfx *g = gfxHead++; g->words.w0 = ...; g->words.w1 = ...; }`). When the target copies the pointer's register,
  write the copy too (`h = g;`).
- Repeat `arg & 0xFFFF` at each use instead of keeping it in a local.
- A global used in one block: give it a block-scoped pointer there, one per global.
- Loops: each loop its own counter; declare a loop's pointers inside the loop.
- A jump table: write every case the table has, also those that do nothing but break.
- Stack offsets: one shared offset local OR none, whichever mirrors the target.
- Copy the habits of the neighbours: their prototypes, struct names and loop shapes.
- Check each callee prototype's parameter count and types, and the return type: a result left in `$v0` means the
  function returns it.
- Try `while`, `for` and `do/while`; `if/else` against early `return`; a temporary against the repeated expression.

## Rules

- Never add fences (`do {} while (0)`), `volatile`, padding, `__repair_` names, or arrays that exist only to pad the
  stack. `try` refuses what you add. What the C you started from (saved C, near-miss, draft, twin) already has may stay
  while you improve it and in an exact match; say in your report that the match keeps it. Never add dead
  code or wrong stores to lower a score.
- A lower score is closer; 0 is a match. Only the app saying MATCHED counts.
- Never print `connection.json`. Do not run other judges in parallel, the Deep Permuter or `scratch_run.py`.
- Do not tidy after a match.

## Report back (short)

Function, MATCHED or the final score and profile, tries, and the change that helped most (C before and after).
