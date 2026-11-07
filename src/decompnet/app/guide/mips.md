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


### Cheap scheduling experiments

Before rewriting a near match by hand, `compile_family` can test `typed_temporary`,
`branch_orientation`, or `narrowing` with just the function and candidate revision. The first
materializes narrow stores or dereferenced call arguments into typed locals; the second exchanges
braced branches and negates a simple comparison; the third explores mask-to-cast narrowing.
These are bounded hypotheses, always checked by the exact judge. Under known `-g`/`-g3` flags,
`source_lines` tests text-only statement placement that an AST formatter cannot preserve.
Keep the successful source layout when debug labels affect scheduling. Compiler flags must remain
those verified for the function; formatting variants do not justify changing the judge or guessing ABI arguments.

For large formatting-library routines, inspect local SDK/library leads before broad permutation.
Keep character and output-length variables distinct, signed output arguments, macros, fallthrough
case groups and flag-loop labels. Try `condition_assignment`, `conditional_output`, `switch_flag_loop`,
`shared_tail_copy`, `target_switch_order` and `formatter_bundle` through local repairs. These are
bounded hypotheses, not match claims. Recheck a historical best against the current header/compiler;
only the destination's exact linked verdict accepts C. See docs/COMPILER_MATCHING_LEARNINGS.md.

### Count-loop and frame-only repairs

For a count loop whose final misses are a NOP/shift or register scheduling, try
`count_phase_lifetime`, `count_load_qualification` and `count_lifetime_bundle`.
They preserve the existing read locations, prove an unsigned16 count and restore
its value for later phases. Qualification alone can worsen allocation, so use the
bounded combined candidates rather than assuming each intermediate must improve.
`normalization_scope` tests an equivalent one-iteration block around an existing
signed16 normalization. No load or cast is moved across the loop test.

If only stack offsets differ, `modular_guard_width` is prioritized. It recognizes
an existing bounded identity guard, not arbitrary locals, and checks the selected
compiler's integer/narrowing contract. The demonstrated KMC result is a controlled
replay of one known match. Never add observable calls, reads, stale captures or
undefined arithmetic to manufacture a frame. Mismatch categories and rendered
position counts guide investigation; the ordinary exact judge still accepts C.

`proven_local_width` tries narrower signed storage only after proving bounds across all
assignments in a straight-line function. Branches and unknown expressions are skipped.
`learned_expression` reuses small templates from exact results under the same compiler
profile. Local repair batches and deep search spend their normal compile allowance on these
hypotheses; no model call is needed to generate them. They still require an exact judge.


Before tuning a candidate, check any **Boundary review** warning. A target ending in a
prologue or load sequence can be truncated by a segment boundary; it does not establish
handwritten assembly. Inspect adjacent original bytes and known mappings, then use a
copied reimport or proven piece join. Do not spend the candidate budget on an incomplete
extent or vote it assembly just because no return was imported. Gap audit results are
review leads, including when direct control flow closes; they never establish a match.

Old GCC narrow-store experiments need a declaration at the beginning of a compound
statement: use `{ u8 tmp = value; *destination = tmp; }` so an unbraced `if/else`
remains valid C89. For signed scalar normalization, the local repair recognizes
both `-value - bias` and `0 - value - bias` under its existing unused-local guards.
A successful frozen-output replay still needs the ordinary exact game judge before
it counts as a match.

For branching void drafts, an observed `v0`-preserving signed exit is a partial-return hypothesis,
not a defined result on every path. Deep search can test a final guard plus a separate scalar
lifetime diagnostically even when the single edit regresses. Read caller consumption and negative
experiments in the function brief. Mapped middle quotient scopes and depth estimates are search
priorities only; the original compiler and linked byte/data judge still decide each trial.
