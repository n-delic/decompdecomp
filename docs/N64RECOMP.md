# N64Recomp in decompdecomp: a port before the decomp is done, and a reference for every function

Status: phase 0 is built (Porting → Recompile with N64Recomp; `consoles/n64/recomp.py`, `app/recompile.py`); the rest is a plan. ROADMAP.md, "N64Recomp: a playable game from day one", tracks it.

## Why

decompdecomp's end result stays the same: C that builds the console's exact bytes, and ports built from that C. A
static recompiler adds two things on the way there.

1. **A game that runs before it is decompiled.** N64Recomp translates each of the game's MIPS functions into C that
   does exactly what the instructions do (registers in a context struct, memory through macros). A port can use that
   C for every function the project hasn't matched yet. As functions match, their decompiled C replaces the
   recompiled C one by one. The user plays a native or browser port from day one, and it becomes a real decomp
   gradually, with nothing thrown away.
2. **A third reference beside the emulator and the decompiled C.** Recompiled C *is* the original instructions, so
   when a port behaves differently it splits the question in two.
   - Same result as recomp, different from the emulator: the emulator or the host is wrong.
   - Same result as the emulator, different from the decompiled C in the port: the port is wrong.
   The backwards race start (#1198) was the second kind. The matched C was right on the console, but the port lost
   the `$v0` a void function left for its caller. The recompiled function sets `ctx->r2`, so a per-function
   comparison would have shown the difference on the first call.

## What N64Recomp is

Mr-Wiseguy's N64Recomp (MIT) is a command-line tool that reads a TOML configuration:
- the ROM;
- function symbols, from an ELF or a symbols file;
- the entry point;
- overlays, as sections with their own addresses.

It writes one C function per game function, `void func_80012345(uint8_t* rdram, recomp_context* ctx)`. Memory
accesses go through macros (`MEM_W`, `MEM_H`, `MEM_B`, …) that the project defines in its own `recomp.h`. Calls
between functions are direct C calls, and calls through pointers go through a lookup the project supplies
(`LOOKUP_FUNC`). N64ModernRuntime (ultramodern and librecomp, GPL-3.0) is the runtime its native ports use: libultra
reimplemented, plus the RT64 renderer. Play already lists it as a native runtime (`PortRuntime("n64modernruntime")`).

## How it fits what exists

Most of the plumbing is already in the WebAssembly port, because matched C already behaves like MIPS in places:

| Recomp needs | The port already has |
|---|---|
| memory at console addresses, big-endian | game memory laid out as the console's and read big-endian (`port/wasm/bemem.py`) |
| a function for each console address (`LOOKUP_FUNC`) | the address table for calls through pointers (`fixed.txt`, `bemem.py`) |
| arguments and results in registers | MIPS's argument and result registers as words in memory (`adapt.py`: `REGS`, `V0`) |
| each function's C signature, to bridge the two ABIs | one signature per function (`sigs.py`, `sigs.txt`) |
| a libultra the game calls | the host (`consoles/n64/port_wasm/host.c`), and N64ModernRuntime for native |
| the original's behaviour, to compare against | the reversing emulator (`consoles/n64/emulator`, #1200) |

Because the project writes `recomp.h`, the recompiled C can use the port's own memory model directly: `MEM_W(o, r)`
becomes the port's big-endian load at console address `r + o`. No second copy of memory, and no byte swapping
between the two worlds.

## Phases

### 0. Pin and run it (built)

On a racing game: all 2,125 of the split's functions and 12 it doesn't list, as C in 16 seconds. 14 were set aside as
stubs, each with N64Recomp's reason: the kernel's coprocessor-0 code its runtime replaces, one branch it can't follow,
and guessed low-memory code that is data or RSP microcode. Four libultra functions are left for the runtime, one name
clashed with the C library (renamed), and the rest compiles.

What was built, beyond the plan below:
- **Code the split doesn't list:** a call into code no function covers becomes a function of its own (`discover`):
  the entry point, and the code the split left as data. A call into a guess splits it.
- **Functions between segments:** each gets a section of its own.
- **Refused functions:** N64Recomp stops at the first function it can't translate, so each is set aside as a stub
  and it runs again.
- **The compile check:** the C is checked with this computer's compiler against N64Recomp's own `recomp.h`.


- `[n64recomp]` in `toolchains.toml`: N64Recomp by commit, with the libraries it fetches pinned as parts, built from
  source with the pinned CMake (as Torch and mupenRE are).
- `consoles/n64/recomp.py` writes N64Recomp's configuration from what a ROM project already knows: the split's
  functions, their sizes and names (`symbol_addrs`), the entry point, and overlays with their ROM and RAM ranges. It
  runs N64Recomp into the project's build folder. Generated C is derived from the user's ROM, so it is never saved
  to git, like `extract/`.
- Check: every function the split knows comes out as C that compiles. Report the ones N64Recomp refuses (jump
  tables it can't bound, hand-written assembly, code that modifies itself) as findings on those functions.

### 1. A reference for each function (differential tests)

- Take a function's entry state from the reversing emulator: an execution breakpoint at its address gives the
  registers and RDRAM at that moment.
- Run the same state through the recompiled function and through the port's build of the decompiled C, with the
  same memory model. Compare the registers each returns (`v0`, `v1`, `f0`) and every word of memory each wrote.
- An agent tool and a Port readiness row: "behaves like the original on N recorded calls". It catches what the
  judge can't, because the bytes match and the behaviour differs only in the port: `$v0` lost through a void
  function, a `switch` whose labels disagree with its table, locals reached through the stack layout,
  integer-promotion differences between IDO and clang.
- The emulator records calls once; the comparison then runs natively, thousands of calls a second.

### 2. The WebAssembly port with recomp where the decomp isn't done

- The port's build takes, for each function, the matched C if there is one, else the recompiled function. A table
  saved with the port records which (`recomp` or `decomp`), and Play shows the split ("62% decompiled C, 38%
  recompiled").
- Bridges in both directions, generated from `sigs.txt` like `adapt.py`'s thunks:
  - decompiled C calling recompiled: arguments into a `recomp_context` (`a0`–`a3`, then the stack words), the stack
    pointer into the port's stack area, the result from `r2`/`f0`;
  - recompiled calling decompiled: the reverse, by name. The recompiled caller's call of `func_X` resolves to the
    bridge, and the bridge stores the result in `r2` even for a function the C declares void (what `V0` already
    does).
- Calls through pointers: both kinds go in the one address table (`LOOKUP_FUNC` and `bemem.py`'s lookup are the same
  table).
- libultra and the rest of the SDK: recompiled calls of SDK functions go to the host, as the decompiled C's do.
- Check, on a racing game that is part decompiled: recompile all of it and play a race; then swap in its matched
  functions a few hundred at a time. Frames stay identical to the all-recomp build at the same inputs, and the
  differential tests from phase 1 pass for every function swapped in.

### 3. Native ports on N64ModernRuntime

- Play's N64ModernRuntime choice becomes buildable: recompiled C plus decompiled C, with the same bridges, on
  ultramodern and RT64. On macOS, RT64 needs Xcode's Metal compiler (see ROADMAP.md, "A choice of runtime for a
  port").
- libultraship ports keep needing decompiled C for the game's own functions. Recomp's register-context functions
  could run there too, through the same bridges, once the libultraship port runs at all.

### 4. Upgrading over time

- When a function matches (the console judge) and passes its differential tests (phase 1), the next port build uses
  its C. Nothing else changes.
- The Porting tab shows a function's status for the port: recompiled, decompiled, or decompiled but different from
  the original (failing differential tests: a port bug to fix, with the recorded call that shows it).

## Risks and open questions

- **Overlays and self-modifying code.** N64Recomp needs every overlay's sections described. Games that load code at
  run time from compressed data need the loader's mapping, which the split already finds for ROM projects
  (`consoles/n64/overlays.py`, `loader_mapping.py`).
- **Jump tables and hand-written assembly.** Functions N64Recomp can't translate stay in the "needs decompiled C"
  list. Kernel and SDK assembly is already handled by the host.
- **Floating-point mode and 64-bit registers.** Recomp keeps the MIPS semantics, such as the FR bit and 64-bit
  GPRs. The bridges convert at the boundary only, as declared types say.
- **Licences.** N64Recomp is MIT. N64ModernRuntime is GPL-3.0, so a native build on it is GPL-3.0. The
  WebAssembly build with recompiled C uses only N64Recomp's output and decompdecomp's host. Say which applies in the
  export, as Play already does for its runtimes.
- **Size and speed.** Recompiled C is larger and slower than decompiled C, but far faster than emulation. The
  browser build's size grows with the recompiled share and shrinks as functions are decompiled.

## Where it would live

- `consoles/n64/recomp.py`: the configuration, running N64Recomp, and the bridges' table (N64-specific, behind a
  connector part such as `PortKit.recompiler`, so another console's static recompiler can plug in the same way).
- `port/wasm/`: the choice per function and the bridge thunks (shared: they are about the port's ABI, not the N64).
- `consoles/n64/emulator`: recording a function's calls for the differential tests.
- Tests: the bridge thunks on hand-made IR (like `tests/test_port_wasm.py`), the configuration from a demo project's
  symbols, and a real game behind `DECOMP_TEST_N64_ROM`.
