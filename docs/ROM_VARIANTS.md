# ROM variants

One visible game can contain several local ROM targets. Open **Variants**, choose an
existing project, and give both targets stable IDs such as `pal` and `us-rev1`.
The variant selector changes the target used by progress, ROM Explorer, functions,
claims, compiler tools and agents. Attaching never moves or rewrites project data.
The sidebar lists the primary project once and counts workers across its variants.
Existing direct project links continue to work.

## Logical functions and shared C

A logical function has a persistent ID independent of names and addresses. Link
function names in each variant. Several instances may implement one logical
function; one instance may implement several logical functions. Record absent and
inlined functions without inventing standalone functions. These are identity links,
not matching evidence. Regional similarity remains a suggestion rather than proof.

Each instance still owns its target bytes, addresses, segment/overlay information,
compiler settings, claims, attempts and match result. A rename does not replace its
identity. If a reimport replaces an instance, update the identity's bindings before
publishing more source. Do not guess a new instance from an address alone.

The shared-source editor stores immutable revisions, separate from target source.
It verifies every bound standalone instance with that instance's current header,
compiler settings, relocation/link judge and original bytes. A failure or Stop
publishes nothing and changes no existing target matches. Shared header additions
are local to the shared implementation; they do not replace target headers.

Use defines for small conditional differences. Use a separate implementation in a
variant's binding for substantial differences. Inlined instances without a standalone
function are descriptive links and do not receive a standalone compilation verdict.
If several logical functions offer source for the same concrete function, resolve
the choice before export. Source is never chosen by similarity or silently merged.

Shared revisions use optimistic revision checks and a database lock across the
game. A slow compiler does not expire the lock. An exited process's lock can be
recovered on the next edit. Membership cannot change during verification/export.
Agents retain their explicit internal target slug and see the variant scope in
`project_info`; ordinary target edits cannot change the shared revision store.

## Export

**Export all variants** writes one tree under the primary target's export folder.
The normal Export action does the same for a grouped game.

```
python3 build.py --variant us-rev1
python3 build.py --all-variants
python3 build.py --variant pal check update_player
python3 build.py --variant pal targets
```

`variants.json` selects independent `variants/<id>/` trees. Each owns its source
wrappers, headers, compiler ranges/function settings, symbols, layout, private
assets, expected image hashes, and build output. Identical implementation text is
stored once under `shared/`. Function metadata retains the C symbol separately
from old splitter filenames; `check` accepts either name. Each build stages its shared includes inside that
variant's ignored include cache so sandboxed and Windows compilers can read them.
Original assembly remains the fallback for unmatched or no-longer-exact C.
If a saved function's compiler or optional runner is unavailable, its assembly is
retained and its export README lists the setup reason. Its saved C, compiler profile
and match history remain in the project. Prepare that compiler and export again to
include its independently checked C.

Export rechecks shared C against current target inputs and verifies every complete
image after producing the final shared wrappers. A saved proof never overrides the
judge. `--all-variants` tries every variant and fails if any build fails. Run it after
editing shared source or headers outside the app. Original game inputs and generated
assembly/assets remain private and ignored by Git.

Verification and export have server-owned job IDs, Stop, measured instance/variant
progress, and saved progress/results/errors. Function checks show the selected
variant, current function and completed/total count. Image rebuilds use an
indeterminate bar until their byte comparison finishes. Returning to Variants or
opening a new window discovers running work before loading target statistics. After the app exits,
an incomplete job is shown as interrupted and can be safely rerun; it does not claim
completion or publish partial source.

## Adapter boundaries

The catalog grouping and many-to-many identity schema do not depend on ROM layout.
Initial UI admission supports local ROM targets of the same console. PROJECT mode
needs adapters for object-instance identity, per-variant build commands, independent
objdiff verification, shared source publication and export; it is explicitly rejected
until those adapters exist. Team-room membership is also not yet supported.

A ROM export adapter must already provide exact rebuilds. Overlapping overlay-bank
export remains unsupported by the existing ROM exporter and reports an actionable
error; grouping does not weaken that guard. Targets retain all bank information.
Grouped portable Save/Open manifests and automatic restoration of identity links
after reimports that replace function IDs are planned. Ordinary target saves and
history remain independent. Automatic regional pairing can suggest links, but this
first version requires explicit logical identity bindings.

Validation: the homemade N64 demo has been independently judged, grouped, exported,
rebuilt for both targets, rebuilt after deleting generated files, and checked through
`--variant`. Scratch copies of two existing N64 regional releases (12 MiB and
8 MiB, with different expected hashes) also rebuild independently and through the
final shared repository’s `--all-variants` command, byte for byte. Six selected
instance checks also pass through `--variant`, including renamed C symbols.
Attaching preserves their function/source fingerprints and attempt, note, event and
header histories. Six independently judged instances exercise shared C at different
addresses, conditional differences and separate implementations; invalid shared
header edits leave the saved revisions intact. Selected maps, explicit agent scope,
conflicting edit exclusion, job reattachment and Stop are checked in the scratch home.
Other ROM export adapters still need their own real-game validation.
