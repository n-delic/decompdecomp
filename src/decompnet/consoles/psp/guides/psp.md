# Matching PSP code

CPU: the Allegrex, a little-endian MIPS II (32-bit registers) with instructions of its own.
- `max`/`min`, `ext`/`ins` (bit fields), `seb`/`seh` (sign extension), `clz`/`clo`, `wsbh`, `rotr`,
  `movz`/`movn`, and `madd`/`msub` into hi/lo: compilers emit them from plain C.
- The FPU is single precision: `double` arithmetic becomes calls (`__adddf3`, `__muldf3`). A literal without `f`
  makes a double.
- The VFPU (`lv.q`, `vadd.q`, `vdot.p`, `sv.s`, `mtv`, `vpfxs`...) is the vector unit. C reaches it only through
  inline asm, so a function that uses it is set aside as assembly. The SDK's `sceVfpu*` libraries are such code.
- `long` is 32-bit; `long long` is 64-bit and needs `mult`/`multu` pairs or library calls.

The program is a module (PRX). The loader places it in user memory, at 0x08804000 for a game's main module, and
decompdecomp splits it there. $gp is the module info's `gp_value`.
- Calls to the system go through stubs in `.sceStub.text`. Each stub is named after its library and NID
  (`IoFileMgrForUser_109F50BC`) and is set aside: call it like any extern function in your C.
- The game's other modules (its PRX files on the disc, loaded when it needs them) are split too: each is a segment
  named after its file (`ovl_libmenu`), laid out after the main program and the modules before it, so every module
  has addresses of its own. They call the main program and each other through their own import stubs (set aside
  like the main program's), never directly. Each module may have been built with other flags than the main program
  (another -O): try_compiler tells.
- Encrypted modules on the disc (often Sony's own libraries) are left out; the import log names them.

Compilers: Sony's SDK GCC 3.3.3 (psp-gcc 1.3.1, 1.7.1), Metrowerks CodeWarrior for PSP (3.0.1, builds 121 to 219),
or SN Systems' SNC. The program's ".comment" note says "MW MIPS C Compiler" for CodeWarrior, or the GCC version.
Every CodeWarrior build writes the same note, so only the code tells them apart: try_compiler(function, compiler,
flags) with several builds when C looks right but won't match.
- CodeWarrior: small data is `-sdatathreshold 8`, not `-G8`. At -O3 it fills the delay slot of `jr $ra`; at -O2
  it leaves a `nop` there. A PSP game built with it matched at -O3. Its frames keep $ra at the top of a 16-byte
  frame (`sw $ra, 0xC($sp)`), and it uses branch-likely (`beql`, `bnel`) a lot.
- GCC: `-G8` when the game uses $gp. Sony's own libraries (libc, libgu) in a CodeWarrior game are often GCC's: their
  frames keep $ra at 0($sp). Suggest a range for them.
- Ignore GCC's warning about 'pspspecs' not being installed: it doesn't change the code.
