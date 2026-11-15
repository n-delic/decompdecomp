# Linked-image progress export

Use this adapter when the project's compressed game or runtime images are separately linked and cannot appear
as ordinary objects in the cartridge ELF. The app does not infer a whole-game percentage from a source-lock count.

After the project's own source/provenance, relocation, complete-image and compression gates have passed, call
`forks.linkedimages.export(root, populations, object_writer=...)`. Merge its returned units into `objdiff.json`,
retaining unrelated units. The MIPS writer is `arch.mips_image.linked_object`.

Each population contains:

- `id`: unique simple identifier; `label`: display name;
- `image`: project-relative original decompressed reference; `sha256`: pinned digest; `base`: RAM address;
- `functions`: non-overlapping `{name, address, size}` records covering the known functions, including unmatched ones;
- for source-owned functions only, `body`: the compiled and relocated bytes, and `source_checksums`: a mapping
  of project-relative source/header/build-input paths to their SHA-256 values. Include all whole-program group
  members and context files. Optional `source` records the original source for provenance, without enabling edits.

Use original image extents, never source counts, as the denominator. Do not include padding, jump tables or data
as functions. Account for any unknown executable regions separately when describing completeness. Images have
separate namespaces: an overlay at the same RAM address as another overlay is still distinct. Conversely, do not
export an image function a second time among static units.

The helper creates private ELF objects under `.decomp-wtf/linked-images/`. Targets are immutable image-derived
bytes. Candidate objects are written only for exact source-backed bodies; passthrough assembly receives no
candidate. objdiff still measures every function. Missing or changed guarded inputs remove the candidate from
both full reports and individual diffs, rather than preserving its former 100% score.

The project's normal build must refresh this export after compilation. Preserve its gates: if compilation,
linking, provenance or image verification fails, do not publish candidates. Keep every changed source/build input
in the guards so a failed rebuild cannot leave a stale match. References and extracted data must be ignored by git.

These units expose progress and disassembly. They deliberately omit `metadata.source_path`: automated edits,
polishing and drafts require an adapter for the project's specific group/compiler/promotion workflow. Updating
source locks is not an agent operation. The roadmap tracks this gap.
