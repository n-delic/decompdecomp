# Compiler-tested starting drafts

In a project's overview, open **Regenerate machine drafts** and choose **Regenerate drafts**.
The app refreshes open functions and tests their starting C locally, with no model requests.
**Stop** keeps completed work. Claims and unresolved function boundaries are skipped; release a
claim or review the boundary, then run another pass to revisit those functions.

This keeps matched source, saved best attempts, shared headers and attempt history. A fresh exact
judge result can add a match. A compiling or approximately matching draft is still a starting point.
The app's ordinary score/quality rules decide whether a new attempt becomes the saved best.

The refresh uses two shared background workers at a time. A large game can take minutes. Fresh
background drafts are also compiled automatically when no saved baseline exists. The brief and
compiler reuse one valid-syntax m2c run instead of generating the same function twice.

Open a function's **Starting C diagnostics** to see the latest generation, compiler and linking
failures. Agents receive a cached summary even in lightweight claim receipts. A source that compiles
but cannot be linked is retained separately; it has no match score. A completed input receipt avoids
repeating unchanged failures. An explicit refresh starts a new generation, so identical C is tested
again without deleting old attempts. Imported projects also watch their own source, headers, target
object and common build configuration files; their ordinary build/objdiff remains authoritative.

The operator API is `POST /api/drafts` with `{"slug":"your-project"}`. It returns a normal job ID;
read/cancel that job through the existing jobs API. `GET /api/drafts?slug=your-project` summarizes the
latest cached receipts. Add `&function=your-function` to inspect one receipt. These are the app's
usual authenticated operator routes. Run a joined project's refresh on its room host.

## Bounded recovery

- MIPS word loads reaching an indirect call can support private array-of-callables declarations for
  otherwise unknown callback tables. Explicit shared declarations remain authoritative.
- An unconditional first direct call can recover omitted leading arguments only when the untouched
  ABI registers, existing named parameters and plain callee prototype agree. Variadic, floating-point,
  wide-value and ambiguous control-flow cases remain excluded.
- Fixed MIPS frames can represent named stack views in one union. An undeclared view needs an original
  load, a known width/alignment and an escaped known frame view. Overlapping word/halfword accesses
  share storage; no zero initializer or independent fabricated local is inserted.
- Known-size original tables, constants and terminated strings come from the selected function's
  overlay reader. Exact symbolic names keep overlays sharing RAM addresses separate. Limits are sixteen
  symbols, 4 KiB per symbol and 16 KiB in total. Unknown table extents are not guessed. Float/double
  names can supply their fixed four/eight-byte extent. Outgoing case targets remain numeric.
- When duplicate irregular cases prevent compilation, one `--no-switches` m2c alternative can combine
  with frame reconstruction. The initial recovery adds at most two m2c runs (eight seconds each) and
  six candidate compiles; normal existing autopilot candidates retain their own behavior.

All recovered source uses the ordinary compiler and linked/object judge. These are private type and
storage hypotheses, not recovered original source or general semantic-equivalence proofs. PowerPC,
ARM and x86 callback/frame readers, variable frames and more complex ABI cases remain planned in
ROADMAP.md. Existing draft/diagnostic paths still run where the console has a draft generator.

## Validation

Portable regressions cover inference refusals, owner-specific data, receipt invalidation, cancellation,
source/history preservation, API scope and GUI progress/error cleanup. A native C execution test
checks overlapping frame storage. With pinned m2c and the period compiler installed, a homemade
callback target runs through autopilot's real compiler and judge. Run:

```sh
uv run pytest -q tests/test_initial_drafts.py tests/test_overlay_drafts.py tests/test_drafts.py
uv run ruff check src tests projects
```

Private-game probes stay outside the repository. Compilation yield and exact matches are reported
separately; no private game source, data or extracted assembly belongs in a regression fixture.
