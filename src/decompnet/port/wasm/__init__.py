"""A WebAssembly build of a finished game (docs/PORTING.md, "A WebAssembly build"): the game's C compiled for
wasm32, then translated back to C by wasm2c and built with a host for the PC. The game runs in a sandboxed memory
laid out as the console's: its data at the console's own addresses, read and written big-endian, so the C needs
none of the 64-bit pointer fixes a native port does, and data the ROM holds is copied into place as it is.

- build.py: the driver (`python -m decompnet.port.wasm SAVE ROM`): gathers the game's C from its save, compiles,
  links, translates and builds the host. Every input comes from the game's save repository and the console's kit.
- beir.py: rewrites each function's IR: symbols at their console addresses, initialised data big-endian, one
  signature per function (with adapt.py's thunks where a call disagrees with it).
- adapt.py: thunks for calls whose types differ from the callee's; arguments the matched C left in registers.
- sigs.py: each function's one signature, from the definitions, the console's library prototypes and declarations.
- beslots.py: the 32-bit slots the linker fills in data, made big-endian.
- bemem.py: wasm2c's loads and stores made big-endian; uncached addresses through the host; calls through pointers
  by console address.
- stubs.py: a logging stub for every import the host doesn't implement.
- wrap.py: a debugging aid that logs a translated function's calls.

The console's own part (the host, its renderer and its library's prototypes) is its port kit's `wasm` folder
(N64: consoles/n64/port_wasm/: host.c, render.c, libultra.sigs); every C file there is built into the host.
"""
