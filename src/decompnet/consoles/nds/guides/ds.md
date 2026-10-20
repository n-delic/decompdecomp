# Matching Nintendo DS code (CodeWarrior for DS)

CPU: the ARM9 (ARM946E-S, ARMv5TE), ARM and Thumb code side by side; little-endian; no FPU and no divide instruction
(`float` math, `/` and `%` are calls to the runtime's helpers: write the plain operator). The
project is a ds-decomp one: the ARM9 program, its ITCM and DTCM parts and each overlay are modules, cut into source
files of about 4 KiB of functions (src/auto/<module>_<address>.c). The guide `arm` has the instruction set, `mwcc`
CodeWarrior's habits (inlining, register order, the stack frame): read them too.

Compiler versions: games were built with one of 24 CodeWarrior for DS versions (1.2, 2.0 and their service packs,
DSi 1.1-1.6), and often several: the SDK's and middleware's libraries came prebuilt with the version they shipped
with. Each source file is built with one version: the project's (project_info), or its own. Versions differ in
small, telling ways; when right-looking C sticks at 80-95%, suspect the version before rewriting the C:
- the epilogue: 1.2 returns with `ldmia sp!, {r4-r6, lr}` then `bx lr`; 2.0 with `ldmia sp!, {r4-r6, pc}`;
- extra stack padding (`sub sp, sp, #4` with nothing stored there), `ands` against `tst` + `and`, and which
  register a value lands in.

try_compiler(function, "DS/1.2/sp4") builds the function's file with that version and shows the diff; compiler
"all" tries every version and says which match. A version that matches is kept for the file and committed, but only
when every function of the file that matched before still matches with it. When the file's other functions need the
old version, give the function a file of its own first (split_file), then try again. Flags are the project's.

Thumb: a function in Thumb code (2-byte instructions) is compiled in a `#pragma thumb on` ... `#pragma thumb off`
block; ARM is the default. Keep the pragmas the file has.

Libraries: NitroSDK (OS_, GX_, G3_, FS_, MI_, SND_, CARD_...), NitroSystem (NNS_) and the C runtime were linked as
prebuilt objects. Name them (they're well known) and match them last: they were built by Nintendo's version, which
is often not the game's.

Typical patterns: literal pools after each function (constants and addresses, in first-use order); `bl` to Thumb
code through veneers; overlays loaded at the same address, each a module of its own; `func_02xxxxxx` and
`data_02xxxxxx` names for what nobody named yet.
