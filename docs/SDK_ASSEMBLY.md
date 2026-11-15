# SDK assembly identities

Index a local SDK `.a` archive or `.o` object through the existing library settings.
Active projects can identify their assembly functions without an AI agent. No SDK is
downloaded and no library code is copied into the repository.

## What the statuses mean

- **SDK library**: an unambiguous signature identity; not proof of matching C.
- **SDK assembly · verified**: the indexed object linked to exactly the target's code
  and referenced data. It remains assembly and contributes to the separate SDK assembly
  count. Agents, drafting and the C permuter do not receive set-aside assembly automatically.
- **Set aside · assessment**: assembly classification without current exact SDK/source proof.

The function drawer shows the library, symbol, original-object hash, target hash and
verification evidence. The actual source symbol remains visible beside its display name.
Use **Use SDK name in source…** only to request a separate source/header rename. The normal
regression check must preserve existing matches. The first rename scan indexes source identifiers; later scans inflate only changed sources
and fetch full rows only for affected matches. A rejected rename leaves the display identity
and existing code intact; the error and verification progress are shown in that drawer.

## How verification works

Relocation-normalized signatures locate candidates. A ROM may label a fixed hardware address
as a relocation even when the SDK object encodes it literally. A bounded same-size fallback
therefore uses the full linker rather than treating missing signature equality as failure.
External relocation bindings must be consistent; internal SDK data must match the ROM.
Multiple successful names are accepted only when they are aliases of the same section, offset
and size in the same SDK object. The UI shows every alias without guessing the original
spelling. Separate identical implementations, unsupported relocations and truncated candidate
sets receive no new verified identity. Index references retain the selected file path and checksum and refuse
changed files. Proofs are bound to current target/address/profile inputs, not a familiar name.
Background discovery uses finite pages, a persistent rotation cursor and cooperative checkpoints.

Current fresh linking support is MIPS local-ROM mode. Imported builds and other processors can
show signature identities but require additional judge adapters for this verified status.

## Verification performed

Homemade objects exercise privileged instructions, external relocations, owned data, changed
library files, disabled corpora, stale targets and C-work exclusion. On an isolated V-Rally
copy, the existing local `osSetIntMask` object reproduced all 160 bytes and its referenced table.
No game source, ROM or SDK object is committed. The live app was not updated or restarted.


Verified SDK assembly briefs explicitly tell agents no C matching is required. Saved C
attempts remain available as evidence, with a warning that they are unverified approximations;
a full-word C store, for example, need not preserve a partial unaligned assembly store.
This status requires an existing assembly classification: arbitrary SDK objects may themselves
have been compiled from C, so byte equality alone must not turn open C work into assembly.
Automatic discovery runs through the selected local SDK maintenance pass for active projects.


## Names recognised by the compiler

A proper library name can enable a compiler builtin and change callers' bytes.
ROM renames still check every affected matched function first. If a known builtin
name breaks a GNU GCC caller, the app tests `-fno-builtin-NAME`, then `-fno-builtin`
for that one function. Period GCC can accept the per-name option without disabling
the optimization, so acceptance of a flag is never sufficient: linked bytes decide.
Only byte-exact settings are saved, as per-function compiler profiles, in the same
transaction as the rename. Project/range defaults and unrelated functions stay intact.
Any remaining failure rejects the entire rename, including proposed settings.
These profiles survive saves and rebuildable exports through existing function compiler support.
Imported builds, custom commands and other compiler families retain their existing guarded
rename behavior; they need compiler-specific adapters before automatic flag retries.


## Automatic detection at import

N64 ROM imports now carry a small built-in fingerprint pack for the independently
reproduced, self-contained libultra `bcmp` and `bzero` assembly bodies. Both verified
`bcmp` assembler expansions (`move` as `addu` or `or`) are covered. No SDK, compiler,
model call or network download is required. This pack stores SHA-256 hashes and
provenance, not SDK instructions. The full body, exact length and big-endian encoding
must agree, with no external relocations; partial or normalized similarity never
excludes a function from C work.

Classification occurs inside import before the claim queue is populated. A unique
entry gets its canonical SDK source name when the generated name has no collision.
Duplicate entries retain unique source symbols and receive the proper SDK display name.
SDK assembly is counted separately from C. Existing active ROM projects receive the
same classification/display-name backfill through SDK maintenance, preserving live
claims, matched C, deliberate names and explicit Reopen decisions. Existing source
renames continue to require the guarded rename workflow.

Known digest proofs are rechecked against the built-in pack and current target inputs,
so they work across saves without depending on a private SDK path. The pack does not
identify an exact SDK release or prove which synonymous original label was used.
Imported project mode and other consoles need their own exact-body packs/adapters;
unrecognized functions retain their current workflow and local-object SDK support.
