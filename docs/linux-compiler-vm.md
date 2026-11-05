# PS2 compilers on macOS

All seven listed PS2 compiler packs retain their original compiler binaries and
use the ordinary linked-byte judge. Sony EE-GCC 2.9 (991111), 2.96 and 3.2 run
through an optional local Linux runner. SN Systems EE-GCC 2.95.2/2.95.3 and
CodeWarrior PS2 2.3.3/3.0.1 use pinned native Mac wibo and need no VM. CodeWarrior
still requires explicit proprietary license acceptance in Setup.

## Set up Sony EE-GCC

1. Install Colima and the Docker CLI: `brew install colima docker`.
2. In **Compilers & tools**, choose **Prepare Linux runner**. This starts the
   dedicated `decompdecomp` Colima profile and downloads a digest-pinned Linux
   runtime image. Apple Silicon uses Colima's Rosetta/i386 translation. The first
   setup can take several minutes; Colima/VZ needs a supported recent macOS release.
3. Install the EE-GCC version the project uses. For an existing EE-GCC 2.96
   project, install 2.96 and keep its compiler selection.

After a reboot, choose **Prepare Linux runner** again if the VM is stopped.
Readiness checks never start the VM or download anything. The app leaves the
active Docker context and default Colima profile alone. It connects only to its
named profile's local Unix socket, ignoring ambient remote Docker settings.
Colima/Docker are user-managed infrastructure; compilers and the runtime image
are checksum/digest pinned by the app.

## What a compile does

The host keeps the verified archives intact. An immutable guest cache, keyed by
install manifests and worker contents, extracts the compiler and pinned i386
runtime on Linux. Sources and headers are copied into a fresh container work
folder, including the parents of selected nested output paths. Only selected
object files are returned to the host linker and byte judge.

Compiler jobs have no network or host-folder mounts. They run as an unprivileged
user, with read-only compiler files, private scratch folders, no capabilities,
and process, memory, file and time limits. Cancellation removes the container
and its compiler children. Each job gets its own container; cached tool snapshots
remain available to concurrent jobs.

The VM defaults to 2 CPUs and 3 GiB RAM. Jobs have a 1 GiB physical-memory cap,
a 2 GiB virtual-address cap, a 30-second CPU cap and a 64 MiB output-file limit.
Input/output transfers are bounded to 128 MiB in total.

The runner is shared by `CompilerConfig`, function compilation and `run_in`, so
ROM diagnostics and exported build helpers follow the same path. Imported projects
retain their own build scripts: arbitrary imported Make/Ninja execution in the VM
remains planned, as do native builds of the original Sony compiler packs.

## Verification

Opt-in tests use hand-encoded MIPS targets to check exact C and reject wrong C
with all seven compilers, and exercise C++ fallback and nested relative outputs.
The homemade PS2 demo exercises function matching, C++ methods, calls/data/float
relocations, whole-image exports and mixed EE-GCC/CodeWarrior address ranges.
These are controlled compatibility checks, not new retail-game matches. Physical
Intel Mac and retail-game verification remain planned.

Use a scratch HOME, toolchain root and a dedicated VM profile for development.
Install packs explicitly first; tests never download compilers or accept licenses.
Then run, with the same scratch HOME and Python environment:

```sh
DECOMPNET_TOOLCHAINS=/path/to/scratch/toolchains \
DECOMP_LINUX_VM_PROFILE=decomp-compiler-tests \
DECOMP_PS2_MAC_TESTS=1 DECOMP_LINUX_VM_TESTS=1 \
python -m pytest tests/test_ps2_compilers_macos.py tests/test_linuxvm.py -q
```

Run the demo-based PS2 tests after building `projects/demo-ps2/build.py`. Stop only
the scratch VM when finished: `colima stop decomp-compiler-tests`.
