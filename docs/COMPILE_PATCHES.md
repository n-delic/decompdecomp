# Compact edits and recipe previews

These tools work in source-tree projects through both MCP and the built-in API
helper. They use the project's existing compiler and objdiff settings. Start a
missing definition with `compile_and_diff`; patches require an existing definition.

`function_brief(function="work")` returns the current definition, recorded score,
and a revision. Assembly and notes remain available through `get_function`.

Send only a small edit with `compile_patch`:

```json
{
  "function": "work",
  "revision": "<revision from function_brief>",
  "edits": [{"old": "return x + 1;", "new": "return 1 + x;"}]
}
```

Each old string must occur exactly once. There are at most 16 replacements, and
the resulting definition must fit in 64 KiB of UTF-8. Only that complete function
may change. Shared declarations, includes and preprocessing belong in `edit_file`.
The patch goes through the ordinary judge, including claim checks, protected
matches and linked-game verification. The response includes the new revision.
Long kept-attempt diffs remain available through `last_diff`.

`try_recipes` tests up to eight distinct explicit edit sets from the same source:

```json
{
  "function": "work",
  "revision": "<revision from function_brief>",
  "recipes": [
    {"name": "commute addition", "edits": [{"old": "x + 1", "new": "1 + x"}]},
    {"name": "subtract negative", "edits": [{"old": "x + 1", "new": "x - (-1)"}]}
  ],
  "apply": false
}
```

Recipe names must be unique. This first version accepts explicit edits; it does
not choose architecture-specific flags or presets. The baseline and trials build
and compare with the project's ordinary tools. Every previously matched neighbor,
and an already matching function itself, must remain matched. An exact candidate
in a completed, linked unit must also pass whole-game verification.

A preview restores the original source bytes and rebuilds its output in `finally`,
even for an exact match, failed compile or comparison exception. It does not record
attempts, alter claims, update match status, commit or share a result with a room.
An unsuccessful restoration build is reported explicitly. Trial diffs are returned
in the response; `last_diff` continues to describe the last ordinary compile.

With `apply=true`, the strictly better winner is submitted again through the normal
keeping attempt. An exact preview is therefore not an accepted match until this
fresh attempt succeeds. Equal scores leave the source alone. Normal project-build
memory/time limits apply to each build; a trial also rebuilds the original unit.

Revisions compare content, including the entire unit, local headers, tools/config
and harness inputs, build configuration and the target object. Only platform, version, compiler and compiler-range settings enter the revision;
background surveys and UI bookkeeping do not. Python caches and ignored
build/original directories are excluded. Symlinked headers/tools are skipped: their
external contents are outside this revision, but fresh builds and ordinary judging
still run. The scan is capped at 4,096
entries and 32 MiB of input content; larger projects can use `compile_and_diff`.
Generated headers and external compiler files are not a complete revision cache:
previews force fresh candidate builds, and applying rejudges normally. No preview
verdict is reused as an acceptance decision.

Trials use a shared tree lock and hold their unit lock. Unrelated units can build
concurrently; linked-game trials use the exclusive tree lock. Briefs take only read
locks. A batch scans inputs at its start and end, releases its locks, then applies
through a fresh revision-checked patch. It cannot
coordinate an external editor or build running outside the app; avoid changing
project/compiler inputs externally while trials run. A changed revision refuses
application and asks for a new brief. Candidate outputs must be regular generated
`.o`/`.obj` files inside the project, separate from targets and originals.

The shared project engine serves all source-tree consoles. End-to-end checks in
this contribution use the synthetic PS4 demo with pinned Clang/objdiff. The GC, PC,
Xbox and DS demo tests use the same dispatch calls and skip without their pinned
tools (Wii shares GC's project engine). ROM-mode transactions and recipes for
absent definitions remain planned in ROADMAP.md.
