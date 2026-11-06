# Matching PlayStation 3 code

CPU: the Cell's PowerPC core (the PPU), a big-endian 64-bit PowerPC with AltiVec (`lvx`, `vaddubm`, `vperm`...).
The program is an ELF64 whose registers are 64 bits wide; Sony's compilers keep **pointers and `long` 32 bits wide**
(`lwz`/`stw` for a pointer, `ld`/`std` only for `long long` and register saves). `int` is 32-bit, `long long` 64.

The compiler
- Retail games were built with Sony's GCC 4.1.1 (or SN's SNC), one build per SDK generation; the program's `.comment`
  says which ("SDK270"). The free ps3dev GCC makes a different (64-bit pointer) program, so it cannot match retail code
  exactly. When the user has set up their own SDK, each copy is a compiler named `ps3sdk-<generation>` (`ps3sdk-270`)
  and the game starts on the copy whose stamp equals its own; `try_compiler` can switch to another copy. Those run from
  the user's copy, with the SDK's headers (`#include "sys/process.h"` style) and its libgcc and intrinsics headers.

Calling convention
- Arguments in r3-r10 (floats f1-f13), the result in r3 (f1). r14-r31 and f14-f31 belong to the caller: a function
  saves what it uses, usually `std r31, -8(r1)` below the stack pointer and `stdu r1, -n(r1)` for its frame, and
  `mflr r0` / `std r0, 16(r1)` around calls. A leaf function often has no frame at all.
- r1 is the stack pointer, r2 the **TOC**, r13 is reserved (the system's), r12 and r11 are scratch.
- A function's address is not its code: a pointer to a function points at a **descriptor** (the code address and the
  TOC base, 8 bytes in Sony's ABI, 24 in the usual 64-bit one). A call through a pointer loads the code address from
  the descriptor into the count register (`mtctr`, `bctrl`) and saves and restores r2 around it (`std r2, 40(r1)` and
  `ld r2, 40(r1)`). Write the plain call; the compiler does the rest.

Reading the code
- Globals are reached through the TOC: `lwz r9, 8(r2)` loads the address of a global (or of a function's descriptor)
  from the TOC, then the global is read through r9. Large programs use `addis r9, r2, hi` + `lwz r9, lo(r9)`. The
  listing names the datum the slot holds (`# D_00A12340`). A constant such as a float is sometimes stored in the TOC
  and loaded directly (`lfd f0, 16(r2)`).
- A `bl` to an address that is not a function you can name may go through a stub the linker made; it is set aside.
- A call into a system library (`cellSysutil_videoGetResolution`, `sysPrxForUser_...`) goes through an import stub the
  loader links by the library's name and a NID; the import names those stubs (PSL1GHT's names, or `<library>_<NID>`
  where it has none) and sets them aside. A kernel call is `li r11, N; sc` and is named `sys_...` from N. Call the name
  you see in the listing; nobody writes the stub in C.
- `sc` is a system call and `lwarx`/`stwcx.` are atomics: no C compiler emits them, so such a function is
  hand-written assembly and is set aside.
- `rlwinm`, `rldicl` and `rldicr` are shifts and masks: `x >> 16 & 0xFF`, `(u8)x`; `extsw` widens an `int` to 64 bits
  and appears on every return of an `int` function. `cntlzw` is a count of leading zeros.
- The listing's addresses are the game's; the machine draft (m2c) does not know this processor, so write the C from
  the listing.

Matching
- After a call, the linker may have written `ld r2, 40(r1)` where the compiler left a `nop`. The judge accepts that
  when the call goes to the right function; do not try to produce it in C.
- A wrong callee, a wrong global or a different constant in the TOC fails even when the code looks the same: the
  judge compares what each instruction refers to, then the bytes.
- Function order and declaration order decide register allocation. `-O1` and `-O2` are both common; `try_compiler`
  switches the level for a function.

Compilers: Sony's PPU GCC 4.1.1 built most retail games (the program's `.comment` says `GCC: (GNU) 4.1.1 (CELL ...)`),
some used SN Systems' SNC. The free ps3dev GCC is installed for homebrew and the demo, and **does not** match retail
code: it makes 64-bit pointers.
