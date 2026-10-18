# MIPS for N64 and PlayStation 2 decompilation

The N64 (R4300i) uses the o32 calling convention. The PlayStation 2 (Emotion Engine, R5900) uses the EABI.
They differ in the arguments and in `$gp`; the rest of this guide is the same for both. guide("ps2") has
the PS2's own instructions.

Registers
- N64 (o32): `$a0-$a3` are the first four integer/pointer arguments. More arguments go on the stack at
  0x10($sp)+. `$f12`, `$f14`: first two float arguments (doubles use register pairs).
- PS2 (EABI): eight integer/pointer arguments in registers, `$a0-$a3` then `$t0-$t3`. A function that
  reads `$t0` before setting it takes five or more arguments. Float arguments go in `$f12` and up.
- `$v0` (`$v1` for the high half of 64-bit on N64): return value. `$f0`: float return.
- `$s0-$s7`, `$fp`, `$f20-$f30` (even): callee-saved. Seeing `sw $s0, ...($sp)` in the prologue means
  the function keeps a value alive across calls in `$s0`.
- `$t0-$t9`, `$at`, `$v0/1`, `$a0-3`: scratch (caller-saved).
- `$ra`: return address. `$sp`: stack. `$zero`: always 0.
- `$gp`: unused when built with -G0 (most N64 games). PS2 games are usually built with -G8: globals of up
  to 8 bytes are read through `$gp` (`lw $v0, %gp_rel(sym)($gp)`), so declare them with their real size.

Delay slots
- The instruction after any branch or jump runs before the branch takes effect. Assembly shows it
  indented by one space.
- `beql`/`bnel`/... ("likely"): the delay slot runs only if the branch is taken. Compilers emit them for
  loops and simple if/else. GCC 2.7.2 at -O2 uses them a lot.

Addresses
- `lui $at, %hi(sym)` + `lw $v0, %lo(sym)($at)` loads a global. `%hi`/`%lo` split one address over two
  instructions. `addiu` with `%lo` takes the address itself (`&sym`).
- `jal func`: a call. `jalr $reg`: a call through a pointer. `jr $ra`: return. `jr $reg` with a `jtbl_`
  load before it: a `switch` compiled to a jump table.

Types from instructions
- Loads: `lb` s8, `lbu` u8, `lh` s16, `lhu` u16, `lw` s32/u32/pointer, `lwc1` f32, `ldc1` f64.
- Stores: `sb`, `sh`, `sw`, `swc1`, `sdc1` (stores don't tell signedness).
- Compares: `slt`/`slti` signed, `sltu`/`sltiu` unsigned. `sra` signed shift, `srl` unsigned.
- `mult`/`div` + `mflo`/`mfhi`: multiply, divide, modulo. Division by constants often becomes a multiply
  by a magic number (`lui`+`ori`, `mult`, `mfhi`, shifts): write the plain `/` in C.
- `andi x, 0xFF` / `0xFFFF` after arithmetic: truncation to u8/u16. `sll` 24 + `sra` 24: cast to s8.
- `cvt.s.w`, `cvt.d.s`, `trunc.w.s`: int/float conversions. A `double` constant in float code means the
  original wrote `0.5` instead of `0.5f`.

Stack frame
- `addiu $sp, $sp, -N` / `addiu $sp, $sp, N`: frame size. On N64, GCC and IDO both reserve 0x10 bytes
  for the argument area of calls. Locals whose address is taken live on the stack (`addiu $a1, $sp, 0x10` = `&local`).
