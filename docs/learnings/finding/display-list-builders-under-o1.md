---
title: Display-list builders that are exact under -O1 once written with the SDK macros
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: open N64 functions of 170 to 590
instructions that write display-list commands, with m2c drafts scoring from about 2,000 to 38,000. Under the default
`-O2` they stay thousands away; under `-O1` a hand rewrite with the SDK macros on `(*gfxp)++` (or a local `Gfx *` read
once and stored back at the end) was often exact on the first compile.

The source change, and the measured result before and after (scores, the judge used), all with the app's judge:

- Commands through `(*gfxp)++`, textures as `gDPLoadTextureBlock*` / `gDPLoadTextureTile*`, a `switch` on a state
  field, `u16` / `s16` counters as the target extends them.
- `s = (c % 16) * w;` where the target masks a `u16` parameter in place (`& 0xF` scored 10).
- A rectangle's far edge `x + (j * 80 + 80)`: plain, fold reassociates it to `(x + j * 80) + 80`. An embedded
  assignment (`x + (x2 = j * 80 + 80)`) keeps it (1531 -> 205); one remaining site needed `({ i * 24 + 24; })` (-> 0).
  The statement expression is scaffolding.
- A command both arms of an `if`/`else` end with, written in each arm: cse no longer reuses the arm's `j + 1` for the
  loop's `j++`, and cross-jumping merges the copies (700 -> 0).
- A global display-list cursor declared as a scalar pointer, not a struct member: the member form reloads it after
  every command.
- A table of pointers indexed in a loop, declared as a one-member struct array, is addressed through `$at` where the
  plain pointer array had its address hoisted (550 -> 65).
- A flag cleared before a guarded block that sets it last, written as the guard's `else` arm (330 -> 0; library family
  `flag_default_else`).
- Neighbouring globals the target addresses each with its own `lui` (a fade value and its direction, a screen's width
  and height) declared as separate scalars, not one struct: a struct gives them a shared base register (3015 -> 0).
- Floats the target copies to the stack with `lwc1` / `swc1` are separate `f32` arguments; a struct of floats passed by
  value is copied with `lw` / `sw` (10410 -> 0 with the callee prototypes).
- A counter field updated as `(f & 0xF8) | ((f & 7) + 1)` with the count computed first: `n = f & 7;` then
  `(f & 0xF8) | (n + 1)` (35 -> 0).
- `v % 16` of a `u16` for `v & 0xF` and a loop's trailing statement written in both arms are library families
  (`unsigned_mask_modulo`, `tail_statement_into_arms`).

Failed experiments that would otherwise be repeated: `gbi_macro_param_cursor` yields nothing on drafts that store the
cursor after every command (library family `gbi_post_increment` covers those; alone it is not exact). For the far-edge
form, casts to `u32`, `sizeof` terms and the comma operator changed nothing.

What is controlled evidence and what is one function: each item is the measured result on one function. That the
profile is `-O1` was shown by the exact judge for every function it matched.
