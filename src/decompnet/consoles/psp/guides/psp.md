# Matching PSP code

CPU: the Allegrex, a little-endian MIPS II (32-bit registers) with instructions of its own.
- `max`/`min`, `ext`/`ins` (bit fields), `seb`/`seh` (sign extension), `clz`/`clo`, `wsbh`, `rotr`,
  `movz`/`movn`, and `madd`/`msub` into hi/lo: compilers emit them from plain C. The machine draft (m2c) doesn't
  know `max`/`min`/`ext`/`ins`: `M2C_ERROR(/* unknown instruction: min ... */)` stands where they are. `min` is
  `a < b ? a : b`, `ext` a shift and a mask.
- The FPU is single precision: `double` arithmetic becomes calls (`__adddf3`, `__muldf3`). A literal without `f`
  makes a double.
- The VFPU (`lv.q`, `vadd.q`, `vdot.p`, `sv.s`, `mtv`, `vpfxs`...) is the vector unit. C reaches it only through
  inline asm, so a function that uses it is set aside as assembly. The SDK's `sceVfpu*` libraries are such code.
- `long` is 32-bit; `long long` is 64-bit and needs `mult`/`multu` pairs or library calls.

The program is a module (PRX). The loader places it in user memory, at 0x08804000 for a game's main module, and
decompdecomp splits it there. $gp is the module info's `gp_value`, which every module has, used or not: whether the
game keeps small data ($gp-relative globals) is read from its code, and the project's flags say it (`-G8` or
`-sdatathreshold 8` when it does, `-G0` or `-sdatathreshold 0` when it doesn't). A module whose small data isn't the
main program's has its own flags (a compiler range of its addresses).
- Calls to the system go through stubs in `.sceStub.text`. Each stub is set aside: call it like any extern
  function in your C. It has the system function's name (`sceIoOpen`) from the PSP SDK's list, or the game's own
  symbol. A function the list doesn't know keeps its library and NID (`IoFileMgrForUser_109F50BC`).
- The game's other modules (its PRX files on the disc, loaded when it needs them) are split too: each is a segment
  named after its file (`ovl_libmenu`), laid out after the main program and the modules before it, so every module
  has addresses of its own. They call the main program and each other through their own import stubs (set aside
  like the main program's), never directly. Each module may have been built with other flags than the main program
  (another -O): try_compiler tells.
- Encrypted modules on the disc (often Sony's own libraries) are left out; the import log names them.

Compilers: Sony's SDK GCC 3.3.3 (psp-gcc 1.3.1, 1.7.1), Metrowerks CodeWarrior for PSP (3.0.1, builds 121 to 219),
or SN Systems' SNC. The program's ".comment" note says "MW MIPS C Compiler" for CodeWarrior, or the GCC version.
SNC leaves no note: its code copies registers with `or $v0, $a0, $zero` where the others use `addu`.
Every CodeWarrior build writes the same note, so only the code tells them apart: try_compiler(function, compiler,
flags) with several builds when C looks right but won't match.
- CodeWarrior: small data is `-sdatathreshold 8`, not `-G8`. At -O3 it fills the delay slot of `jr $ra`; at -O2
  it leaves a `nop` there. A PSP game built with it matched at -O3. Its frames keep $ra at the top of a 16-byte
  frame (`sw $ra, 0xC($sp)`), and it uses branch-likely (`beql`, `bnel`) a lot.
- GCC: `-G8` when the game keeps small data. Sony's own libraries (libc, libgu) in a CodeWarrior game are often GCC's: their
  frames keep $ra at 0($sp). Suggest a range for them.
- GCC works out by itself that a function defined earlier in the same file only reads its arguments (an `abs`), and
  then schedules calls to it like a `const` function's. When the instructions around such a call are in another
  order, declare it `s32 abs_s32(s32 v) __attribute__((const));` in context.h (the PSP demo's call matched that way).
- SNC: `-G8` when the game keeps small data. Its levels are -O0, -O1, -O2 and -O4 (-O3 builds as -O2, -O5 as -O4). It
  has no -Os. `-Xsched=0` turns its instruction scheduling off. Its frames save $s registers at the bottom and $ra
  above them (`sw $s0, 0($sp)`, `sw $ra, 4($sp)`), and it loads 0 with `li` as `ori`. SNC is tested on the demo
  game only, not yet on a real game.
- Ignore GCC's warning about 'pspspecs' not being installed: it doesn't change the code.
