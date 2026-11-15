# GameCube and Wii SDK identification

Built target objects are checked automatically during the existing SDK naming task, when a project is
active. This also works for imported community GameCube/Wii projects. Idle saved projects are not scanned.
No rebuild or reimport is required if the target objects already exist.

The first check installs `dtk-signatures`, a public fingerprint pack pinned to decomp-toolkit v1.8.4
with SHA-256 in `toolchains.toml`. Only its `assets/signatures` directory and licences are extracted.
No SDK source, compiler or game data is downloaded. The source archive's checksum binds the corpus even
if an upstream tag changes. Patterns and their licences stay in the external toolchain directory.

## Evidence and limits

A direct identity requires a complete PowerPC ELF function of at least 32 bytes, matching instruction
bits and relocation layout. Very weak patterns, multiple possible names, repeated target fingerprints
and contradictory named relocation targets are rejected. A familiar function name by itself is never
evidence. This is a conservative pattern identity, not proof of original authorship or an exact SDK release.

Function lists show the shared SDK badge. Function details and agent briefs show the pattern hash,
toolkit version, unit, symbol and target SHA-256. Names recovered from a matched pattern's references
are shown as leads, without classifying or renaming their destinations. Existing descriptive names are
preserved. Generated names can use the ordinary guarded rename path, which checks the project build.
Detection never changes a function's match status, score or the byte judge.

Target fingerprints, sizes and file stamps bind saved evidence to the current objects. Changed targets
lose their identity until checked again. Normal build/report synchronization retains evidence for
unchanged functions. Scan results are cached in memory by object stamp and corpus version; metadata
stores evidence without machine code. A pattern's hash is not an SDK version label.

In **Libraries for this game**, Automatic includes the built-in corpus. Manual selection can disable
it, including selecting no libraries. User-selected local SDK archives still use the shared exact
fingerprint index and can extend coverage beyond dtk's bundled patterns. Conflicting names from the
two sources require review.

The corpus covers selected Dolphin/Revolution SDK, runtime, MSL and debugger functions, rather than
every SDK function. Missing patterns, changed compiler output, missing target objects and ambiguous
functions remain unidentified. C prototypes, complete SDK release detection and automatic classification
from reference-only names are not implemented.

## Upstream implementation

- [Pattern generation and masked instruction comparison](https://github.com/encounter/decomp-toolkit/blob/v1.8.4/src/util/signatures.rs)
- [ELF signature generator command](https://github.com/encounter/decomp-toolkit/blob/v1.8.4/src/cmd/elf.rs)
- [Startup/reference-driven detection and post-analysis scans](https://github.com/encounter/decomp-toolkit/blob/v1.8.4/src/analysis/signatures.rs)

The generator reads named ELF functions and emits instruction/mask pairs plus symbol and relocation
metadata. The checked files do not document the original reference ELF or SDK release for every pattern.
Our detector compares the entire target function and relocation layout, rather than inferring an SDK
identity from whichever name dtk happened to assign during splitting.
