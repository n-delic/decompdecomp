# Supplemental original overlay data

A code-only overlay import may omit strings and jump tables stored before or after the code. Multiple overlays
can occupy the same RAM address, so the ROM judge and machine-draft jump-table reader now use the function's
physical image offset to select its owning segment before reading another overlay at that address.

An operator can supply independently recovered original bytes in a private project-local payload. Place
`memory_regions.json` next to the project's analysis image:

```json
{
  "format": 1,
  "image_sha1": "the project game fingerprint from its configuration",
  "file": "overlay-data.bin",
  "sha256": "SHA-256 of the complete payload file",
  "regions": [
    {"owner": "overlay_segment_name", "offset": 0, "size": 4096, "vram": 2148264448}
  ]
}
```

The fingerprint uses the console reader's normalized game identity, which may differ from a raw file hash.
`owner` is the segment name from the import configuration. Offsets and sizes refer to the local payload file;
`vram` is the address at which that region is loaded. The file must remain inside the project directory.
Different owners may map distinct original bytes to the same address. The loader rejects a changed payload
checksum, a different game fingerprint or an out-of-bounds region. It never substitutes candidate-generated
bytes for original data: obtain the payload from the user's original image and verify the archive boundaries,
load address and preserved code before enabling a region.

The judge still checks linked code and candidate-defined strings, constants and jump tables byte for byte.
A supplemental region changes which original bytes are read, not what qualifies as a match.

## Current limits

This is an explicit recovery input, not an automatic archive recognizer. Save/restore and rebuildable export
do not yet package or regenerate it. Keep the inputs with the private working project and retain the recovery
recipe; do not commit extracted game bytes. Generic reads without a function context retain their previous
behavior. Automatic archive-member retention and complete input/export integration are tracked in ROADMAP.md.
