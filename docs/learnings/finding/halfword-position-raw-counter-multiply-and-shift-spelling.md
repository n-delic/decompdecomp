---
title: a u16 counter multiplied unextended means the sum it feeds was a halfword, and `* 4` on a halfword is `<< 2`
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

Two twin N64 sprite drawers (664 and 653 instructions, KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`; texture load, then a
grid of scissored texture rectangles in a hi-res and a lo-res arm). Both stood at the wall written up in
sprite-drawer-twin-cse-profile-and-raw-multiply.md and sprite-tile-drawer-gbi-macros.md: the target tests its loop
counters as halfwords (`addiu $t9,$t9,1; andi $v1,$t9,0xffff; slt`, and `move $s7,$zero; slt; beqz` as the inner
loop's first test) but multiplies them unextended (`mult $a0,$t9`). `u16` counters gave the tests and an `andi` before
each multiply; `s32` counters the multiply and a `blez` first test.

The rule (convert.c, gcc 2.7.2, `convert_to_integer`): an `int` expression assigned to a halfword is narrowed by
distributing the truncation over PLUS/MINUS (always, since `TRULY_NOOP_TRUNCATION (16, 32)` holds on MIPS) and over a
MULT whose operands, unwidened, are both no wider than the result and of one signedness (`u16 * u16`, `u8 * u16`). The
product is then done in HImode, and `expand_binop` widens a HImode multiply without extending its operands: the counter
is read raw. So the target's positions were halfwords: `u16 tx = r->x + X + r->cw * i;`, and every value made from
them (`ly = ty + Y`, the copies of `tx`) too.

Typing them alone was worse (430 -> 790 and up), which is why it was given up before: their reads in the SDK's
scissored-rectangle macro, `(s16) (tx * 4)`, got an `andi` each. MULT is narrowed only when both operands are no wider
than the result, and the literal `4` is an `int`: the halfword is zero-extended first. `(s16) (tx << 2)` is narrowed
(LSHIFT narrows when the count is less than the result's precision) and reads the register raw, as the target does.
Retyped and shifted together: 430 -> 110, every instruction the target's.

The source change, and the measured result before and after (scores, the judge used):

Isolated judge (`fast_judge`), then the app's `compile_and_diff`:

1. `tx`, its two copies, `ty`, `ly`, `ny` typed `u16`; each `E * 4` whose `E` names one of them written `E << 2` (the
   `* 2`/`* 8` on the `u16` shadow that already was one stay): 430 -> 110 (first twin), 600 -> 10 (second twin, under the
   profile without `-fno-cse-skip-blocks`). decomp-repair `halfword_position_shift`.
2. The sum with the product last, `x + X + cw * i` (the target adds the position first): 110 -> 100, 10 -> 0. The same
   family's second child.
3. First twin only: `s = w << 5` written in the then arm (`arm_value_sink`, 100 -> 60), and `ny` written at its two uses
   as `(ty + arg1)` (`reload_temp_inline`, 60 -> 0; it reads one declaration per line, so the family writes the retyped
   declarations that way).

The second twin also needed its function profile without `-fno-cse-skip-blocks` (the texture-load macro reads the width
again after its `?:` clamps, 320 rows of three reloads): exact there by `try_compiler`, applied for that function alone
with `suggest`. The first twin's profile never had the flag.

Failed experiments that would otherwise be repeated:

- `s16` positions (890-1715), `s16` copies of a `u16` position (1250-1700): the copies sign-extend.
- `u16` positions with the macro's `* 4` (790-2485): an `andi` per read, the cascade the earlier notes measured.
- `<< 2` on only some reads (2885-3765): the frame grows and the counters spill.
- The positions typed and the `(u16)` cast kept on the product (the earlier note's step 8): one `andi` after `mflo`.

What is controlled evidence and what is one function:

The convert.c rule is read in the compiler source; the extension surviving combine for `(s16)(u16 * 4)` and not for
`(s16)(u16 << 2)` is observed on these two functions only. Both twins went to 0 with the same edits.
