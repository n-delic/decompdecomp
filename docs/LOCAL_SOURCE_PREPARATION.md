# Free local source preparation and permutation

The permuter can use explicitly mapped local reference source before its usual deterministic and random searches.
This is optional: ROM-only projects keep working without it. Reference code from another version is a useful seed,
not proof that a function matches the retail game.

## Set it up

Open the game's **Settings → Local source preparation**:

1. Select the reference source folder and any SDK/include folders it needs. These folders are read-only inputs;
   the app does not execute reference build scripts or download SDKs.
2. Map each intended destination explicitly, one per line:
   `func_80001000 = update_objects @ objects.c`.
   The path is relative to the reference folder. Ambiguous/duplicate mappings are refused. Matching a name or a
   source location does not establish a match; no source-derived address is added to the destination symbol map.
3. Supply a declarations-only header with the necessary types and prototypes when possible. It must live inside a
   selected folder. Otherwise the app tries the mapped file's includes and declarations, or the current project
   header if there are no includes. Complex conditional includes, compiler extensions or initialized globals may
   need a smaller prepared header. An unresolved include is reported rather than guessed.
4. If retail and debug layouts differ, enter numeric overrides such as `OBJECT_LIMIT=128`. Existing definitions
   are replaced before dependent structs are compiled. Overrides do not infer the correct value or add a missing
   definition. Add any known global aliases separately: `gReferenceObjects = D_80010000`.
5. Enable preparation and **Save source settings**, then **Try preparation pass**. The button runs a background job
   with a progress bar and per-function diagnostics. The permuter also runs a preparation pass before its usual work.

A pass is bounded to eight compiler attempts and 20 seconds between attempts. One compiler invocation can finish
past that deadline, within the compiler's normal timeout. Unchanged trials (including compiler failures) are
cached. Another pass continues with untried mapped functions. Edited reference bodies, headers, mappings, macro
values or destination build inputs give those candidates another chance. The original function body keeps its
line breaks and original line number: IDO can schedule code differently when source line information changes.
Token replacements leave comments and string literals alone. A mapped `static` entry point is made externally
visible for the isolated compile, without shifting its lines. Its original body remains unchanged.

## What gets saved

For ROM projects, a prepared candidate is compiled and linked at the original address using the same judge as
agent-written C. A strictly better candidate gets a private per-function header and becomes the saved seed. Both
are committed together only if the target, current seed, header version, symbols and project config still agree
with the state used for judging. Failed or worse candidates leave the project unchanged. Already-matched functions
and shared headers are never rewritten by preparation. Extra emitted function bodies are refused because they
can collide in a whole-game export.

Imported projects use their existing build and objdiff judge. Their reference bodies must compile against that
build's headers; the app does not inject a reference header into their build setup. An incompatible header needs a
normal reviewed source/header edit in that project. This limitation is tracked in ROADMAP.md.

Local reference paths, mappings and macro overrides stay in machine settings and are not added to portable project
config or saved exports. **Accepted candidate C and its declarations are project work**: they are included when you
save/export that project. Review them before publishing a source-assisted decompilation.

## How the free search uses the result

- Deterministic repairs use measured mismatch evidence to rank applicable families: stack storage/declarations,
  register lifetimes, call signatures, constants or control flow. No family is excluded solely by this heuristic.
- Each random turn first tries up to eight cached local ordering probes: adjacent equal-constant stores and
  shift/add operand order. They share the turn deadline and normal judge, avoiding random drift for these cheap cases.
- Random decomp-permuter search boosts relevant existing compiler weights by three, retaining every other upstream
  weight. Focused batches alternate with upstream defaults so a misleading category cannot dominate the turn.
  The fallback rewrites use the same focus. This changes search order, never the verdict.
- A fresh baseline compile supplies evidence for the exact seed and context. An in-process bounded cache avoids
  compiling that same baseline every pass. Every generated candidate still goes through the normal judge.
- An improved external seed or changed header, effective compiler, target or symbols renews the existing bounded
  random-turn allowance. The permuter's own improvements do not renew it. Existing counters are adopted on first
  observation, and claimed functions wait until released. For imported projects, common build manifests and the
  generated per-function header are tracked too; an explicit new pass/session retries imported preparation trials.

This makes more work automatic without paid model calls. It cannot guarantee all remaining functions: version
changes, incorrect mappings, unknown data layouts, missing source, assembly and undiscovered compiler settings
still need investigation. An isolated function match is also not a replacement for a verified whole-image rebuild.

## Let the CPU recover real compiler context

Mapped MIPS ROM preparation now tries actual address-taking callback owners as
well as caller/callee groups and predecessors. An assignment such as
`boss->update = UpdateFunction` is useful even though it is not a direct call.
A matched owner can come from the selected source file or from saved exact C in
another project unit. The baseline and these callback hypotheses get early slots
in the existing bounded pass; results are cached with their source/build inputs.
No model calls are involved.

Only real bodies are used. Public auxiliary bodies must already be matched and
are independently rejudged, including referenced data, at their known ROM slots.
The unit limit remains four bodies and 16 KiB of compiled code. There is no guessed
padding or target-instruction copying. Private compiler-unit metadata and export
checks remain required where applicable. Only the requested entry is published;
context bodies are not counted again. This compiler-unit reader is currently MIPS
ROM only; imported projects keep their own build rules.

Repository sync renames the entry in both the C and its private header, preserving
comments and strings. This includes a callback's forward declaration. The normal
standalone compile and full-image gates still run before publishing changes.

## Verify supplied assembly separately

In **Settings → Local source preparation → Verify supplied assembly**:

1. Select the source folder and any SDK/include roots, then save the settings.
2. **Find source assembly** finds known `LEAF`, `NESTED`, `.globl`, `.global` or
   `.ent` labels. These are unverified candidates, not proof of historical origin.
3. Add chosen entries to **Assembly mappings**, for example
   `func_80001000 = OriginalRoutine @ os/routine.S`. Add aliases for known calls/data
   and the correct numeric SDK/layout overrides. An optional **Assembly prelude**
   supplies register and SDK macros from a header inside an authorized root.
4. Save, then **Verify mapped assembly**. An ordinary preparation pass or resumable
   source queue also tries configured assembly before C mutations. A queue's
   assembly-reconsideration checkbox is still required for entries already set aside.

Unchanged successful proofs and failed trials are cached by source, prelude, target
and profile. **Verify mapped assembly** explicitly retries failed trials; changed
inputs also make them eligible again.

The installed, pinned compiler driver preprocesses and assembles each supplied
file under its current function profile and the usual resource limits. If the
profile uses `-O2` or `-O3`, one private `-O1` assembly alternative is also checked,
within the same attempt budget. Its winning profile is recorded without changing
C compiler settings. C-style
includes are flattened from authorized roots; assembler file/binary injection
such as `.include` and `.incbin` is refused, including directives generated by
macros. Source inputs have the ordinary 64 KiB submission limit after flattening.
Archived SDKs may require a compatible prelude or explicitly prepared assembly;
macros, register aliases, layout and flags are not guessed from the game name.
PS1 compiler definitions supply explicit preprocessing and maspsx/GNU-as stages;
the app does not reinterpret a shell wrapper as a plain compiler command.
No SDK or compiler is downloaded by this pass.

Only an exact linked code **and referenced-data** verdict records a proof and
classifies the entry as assembly. Failure leaves its status unchanged. The proof
records source/prelude/object hashes and the compiler profile. It adds no C
coverage, does not publish the original source to the app repository, and does not
replace the standalone build's assembly fallback. "Verified source assembly"
means the supplied revision was verified, not that the app establishes authorship.

The overview separates **exact C**, **verified source assembly**, **other assembly**
and **open** counts. It also reports the C fraction of all code bytes, including
assembly in that denominator; the existing eligible-code progress remains separate.
Zero open functions therefore does not mean 100% C. Source proofs are scoped to the
ROM, target extent/bytes and compiler profile. Saves retain provenance, but restored
proofs require fresh local verification before counting as verified. Imported-build
assembly verification is planned; those projects retain their own assembly workflow.
