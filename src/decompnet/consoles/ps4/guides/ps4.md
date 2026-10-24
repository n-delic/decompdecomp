# PlayStation 4: initial ELF support

Open a decrypted x86-64 Orbis executable (`eboot.elf`). Encrypted SELF files,
PKG packages and PRX libraries are not supported. Supply your own executable;
no game files or Sony SDK are downloaded.

Install the pinned public Clang 5.0.2 in Compilers & tools. The initial compiler
is a starting point, not proof that every game used that version. This build
currently runs on Linux x86-64; Windows users can run the Linux app in WSL 2.

## Import and build

An adjacent `eboot.functions.jsonl` or `functions.jsonl` can supply functions.
Each line has `a` and `end`: hexadecimal offsets relative to the first executable
segment, with `end` exclusive. Optional `ranges` lists body intervals as numeric
offsets relative to that segment. `n` is an optional original name. Body address
counts are not sizes: old maps with only `s` fall back to Ghidra.

Without an explicit map, install Ghidra and its required JDK in Setup. Analysis
supplies function extents and body ranges. Check suspicious boundaries before
spending turns matching them. Shared tails remain external references.

The generated project contains `orig/`, `config/<version>/functions.json`,
`include/common.h`, `src/auto/`, `tools/`, `build.ninja` and `objdiff.json`.
Run its configure command with the pinned compiler path, then `ninja prepare`.
The app regenerates tool paths when preparing a copied project. Paths with spaces
are supported. Build temporary experiments outside `src/`, where ninja will
otherwise compile them as part of the project.

## Compiler and linker behavior

The public compiler uses `-target x86_64-scei-ps4 -ffreestanding`. This selects the
PS4 ABI and global stack guard without a locally patched compiler. The project
keeps its starting flags in `tools/compiler_flags.py`. Headers for proprietary
platform APIs must be reconstructed from the user's code; none are supplied.

GOT address loads for internal symbols are relaxed by the build to their linked
LEA form. Default-visibility extern declarations can preserve a separate address
load when the target has LEA followed by a load at an offset. Hidden visibility
can fold a reference directly into a RIP-relative memory operand. Choose by the
target instructions, not a blanket visibility rule.

Imported globals stay unresolved. If analysis independently identifies a fixed
stack-guard GOT slot, the project owner can set `stack_guard_got` in
`config/<version>/functions.json` to its address (integer or `0x` string). The
build binds only that explicitly supplied address. Never borrow a slot address
from another game. Without it, canary-bearing functions may remain unmatched.

## Matching

Write C/C++ with the `func_<address>` and `D_<address>` names in the target.
Keep relocation identities: a different callee or global is a real mismatch.
Mapped BSS addresses are valid references but have no bytes to read from the
executable. Data inspection reports only file-backed contents.

For scheduling-only differences, a per-function target attribute can help, but
it can also affect features, inlining and register allocation. Compile and compare
before keeping it. A branch-local `__atomic_signal_fence(__ATOMIC_ACQ_REL)` after
a pointer read can preserve separate branch reads without a hardware fence;
placing it before the read does not impose the same constraint. Neither is a
global default. Intrinsics can reconstruct vector helpers without the original
inline source. A local `target("no-sse")` can change large integer-only by-value
copies; it affects the whole function and is unsuitable for floating-point code.

## Current limits

This is beta, with strict per-function objdiff matching. It does not relink a
complete runnable game or export a verified rebuild. Compiler switching, source
splits, joining function pieces, automatic compiler surveys and class/structure
recovery need further work. Symbol renaming, data inspection, drafts, twins,
permuting and shared project workflows use the normal console interfaces.
