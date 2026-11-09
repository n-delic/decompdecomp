# Protect a verified project image

A function-level objdiff match can still change the whole image: its replacement
may lose alignment padding, introduce duplicate globals, or move other code.
For projects that already rebuild exactly from their current C and assembly,
add an explicit `verify_image` to `.decomp.wtf.json`:

```json
{
  "game": {
    "path": "baserom.z64",
    "sha1": "the original image's 40 hexadecimal SHA-1 digits"
  },
  "verify_image": {
    "path": "build/game.z64",
    "commands": [["make", "-j3"]]
  }
}
```

Use the project's actual output and exact build arguments. Compressed-code
projects must include their recompression and final cartridge build commands.
The output must be inside the project and separate from the original image.
Every command must succeed, and decompdecomp independently hashes the output
against `game.sha1`; a stale matching output cannot excuse a failed command.

The check protects partially decompiled units too. Function attempts, compiler
and flag searches, source edits and shared-header edits preserve the verified
image. A function-level match that fails the whole-image check is rejected;
the previous source is restored and the previous image rebuilt. Existing
configure.py/ninja projects retain their checksum-target verification.

This is opt-in because unfinished source builds may legitimately fail their
whole-image checksum. Enable it only after verifying the complete build. It
checks the selected image; it does not imply that every function is recovered C
or cover additional executables that the configured output does not contain.

## Importing a verified rebuild

When a Splat layout omits explicit segment addresses, the importer can use the
linked ELF and map instead. It creates reference objects only after converting
the complete ELF back to an image with the original SHA-1. Cartridge FF gap
fill and trailing FF padding are supported; every layout must pass that same
whole-image hash. Assembly-only checkouts need no empty source directory; their
units remain unrecovered. The importer prefers the map with the matching ELF
basename, beside the ELF or at the checkout root, so another linked program's
map cannot accidentally supply its object list.

Recovered function names are paired with generated assembly names only when the
linked symbol address identifies one unique candidate. These mappings change
name pairing, never the required byte comparison. A changed instruction still
fails to match.

Function totals describe detected boundaries in the imported code. Rebuilding
a complete ROM does not prove that every function boundary has been identified.
Matched functions and total functions are separate counts; code percentages
measure bytes. Binary microcode and other unsplit program images are listed in
the project coverage notes rather than presented as recovered C.
