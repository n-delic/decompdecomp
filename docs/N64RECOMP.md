# N64Recomp in decompdecomp: a port before the decomp is done, and a reference for every function

Status: phases 0 and 1 are built (Porting → Recompile It, on-demand recompilation, and the `compare_behaviour` tool; `consoles/n64/recomp.py`, `app/recompile.py`, `app/behaviour.py`), and phase 2 for the native build (Play and the app export: `port/wasm/recomp.py`); the rest is a plan. ROADMAP.md, "N64Recomp: a playable game from day one", tracks it.

Use **Recompile It** early to check the ROM split. The results include functions the split missed, functions
N64Recomp set aside, and generated C that failed to compile. Review those findings before spending time matching
functions. Builds that use N64Recomp also prepare its output on demand.

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

### 1. A reference for each function (differential tests) (built)

`compare_behaviour(function)` (`app/behaviour.py`):
- **Record:** the reversing emulator stops at the function's first calls in your ROM and records each (`EMU_RECORD`:
  the GPRs, the FPRs and RDRAM).
- **Recompiled side:** a harness runs each call through the recompiled C (`recomp.harness`, with decompdecomp's
  runtime `recomp_runtime.c`, whose libultra stubs say when they're reached).
- **Decompiled side:** the port's host runs it through the decompiled C (`PORT_CALL`; `funcs.c`'s invokers take each
  function's arguments as o32 passed them).
- **Compare:** $v0, $v1, $f0 and the memory each leaves outside the call's stack, named by symbol.

On a racing game, with the backwards race start's `$v0` bug put back in the port, both recorded calls of the
start-heading helper report `$v0: recompiled 00000800, decompiled 00000000`. With the fix, they're the same. A first
run takes about a minute and a half (recording, Recompile, the port's build); later ones only record and run.

For a compiling open function, `compare_candidate_behaviour(function, revision)`
checks the current agent C in a disposable WebAssembly port build. It compares
return and memory observations for up to three recorded calls and reports the
first difference per call. Calls reaching a recompiler runtime stub are
inconclusive. The existing exact compiler judge still decides matches.

**Check behaviour** (Porting) does the same for every matched function at once (`behaviour.batch`):
- **One run:** the game runs once with a breakpoint on each function. RetroReversing/mupen64plus-core#3 takes
  16,384 execution breakpoints and checks them in constant time; each is removed once its first call is recorded.
- **As they come:** each call is compared as it arrives, several at once, and its 4 MB recording deleted. The
  frontend waits while too many are waiting (`EMU_RECORD_PENDING`), so disk use stays around 100 MB.
- **Fair comparisons:** only the registers that hold what a function returns are compared (the port's invoker says
  which), and a void function's $v0 only when a caller takes it (the suspicious calls' void-result). A word where
  the recompiled C stored a return address and the port 0 is a note, not a difference, since C has no $ra. The
  harness reserves address space up to KSEG1, and a call that keeps calling a runtime stub (waiting for a message
  that never comes) gives up instead of writing without end.
- **Saved:** results go in build/behaviour/batch.json with each function's matched C. Stop keeps them, and a later
  check redoes only what's new or changed.
- **Shown:** Port readiness has a step ("Behaves like the original"), the counts, and the functions that differ.


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

Built for the native builds (Play, the app export, the behaviour check, the Miyoo Mini's), not yet the browser build.
Play runs Recompile first when a project has none. The recompiled C runs in the host, natively, rather than in the
WebAssembly module: the game's C already imports what it has no C for (`env.func_X`), and funcs.c's invokers already
call the game's C from o32's words, so both directions needed only generated C (`port/wasm/recomp.py`):

- each function by its console address: the game's C if the module exports it, else the host's by name (or library
  detection's name for it: recompiled libultra would drive what the host emulates), else N64Recomp's C renamed
  `R_<name>`, else a stub that names it (one N64Recomp refused, which it writes as an empty function, or whose C
  branches to a label it doesn't have);
- recomp.h rewritten for the port's memory (big-endian loads; N64Recomp's `MEM_W(o, r) = v` stores rewritten as
  calls; uncached RAM and registers through port_io_load/port_io_store);
- the game's C calling recompiled C: its import, typed, fills a register context as o32 passes those types, the
  $a0-$a3 it doesn't declare from the register words (adapt.py's REGS), and leaves $v0 there. A function only
  recompiled C has and no prototype describes gets MIPS's own signature (eight words in, a word out), so adapt.py
  fills what a call leaves out from the caller's arguments, as MIPS leaves them: a racing game's matched alInit calls
  `alSynNew()` with none, its $a0 and $a1 still alInit's;
- calls through pointers and threads' entries: port_func falls back to a bridge with integer arguments;
- a MIPS stack per host thread for recompiled code (0x14000000 up), handed down across nested calls both ways;
- `PORT_RECOMP_TRACE=1` logs every call from recompiled C into the game's.
- `PORT_RECOMP_CALLS=ADDR,ADDR...` logs each entry to those recompiled functions with `$a0`-`$a3`, `$ra` and `$sp`;
  `PORT_RECOMP_CHANGED=ADDR` checks that RAM word at every recompiled function's entry and says when it changed,
  and which function was being entered (how a racing game's overflowing audio command list was found).
- `PORT_AUDIO_TRACE=1` logs each audio command (ABI 1) and the RAM address it names; `PORT_AI_TRACE=1` each access to
  the audio interface's registers.

On that racing game, with half its matched C (1,003 functions) left out of the build so recompiled C runs in its
place, the game boots and draws as the all-matched build does (479 graphics tasks and 96 frames in 4 s). A second
racing game, with 545 matched functions, runs 350 more as recompiled C through its NuSystem threads up to its main
thread, whose code calls into an overlay its split doesn't have.

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


Native CLI crashes during translation are isolated with bounded body-subset probes, then confirmed with the
individual body active. The affected function is listed as an explicit stub with its crash signal; the remaining
functions are translated again for the final output. If even an all-stub probe crashes, if no individual body
reproduces the failure, or if a resource limit kills the tool, the job fails rather than blaming a guessed function.
The recompiler's stub count is separate from matched-source progress and does not prove gameplay compatibility.

Normal refusals no longer stop a large game after a fixed 400-function batch. Each failed full pass must identify
a new input body; the run bound scales to the input count, and each body's isolation has a separate probe limit.
A generated static body's failure is isolated to the input body that independently causes it to be generated,
rather than adding an invalid generated name to the CLI's stubs list.

If the CLI emits an invalid function body (for example a goto whose label is outside the imported extent), the
compiler check maps the error back to that body and retries it as an explicit stub. Errors in runtime headers
or outside a function do not blame a nearby body. Saved source and the matching judge remain unchanged.
