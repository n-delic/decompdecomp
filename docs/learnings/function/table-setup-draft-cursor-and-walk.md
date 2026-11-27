---
title: a table-setup loop from its m2c draft: cursor temporaries, an end-address walk and a mask in the loop test
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

An 84-instruction N64 loop that fills a range of a global table of three-word records from a word stream, with a
small search loop inside. Saved best: the m2c draft, 3472 (SN64 GCC 2.8.1, the function's own profile).

The source change, in order, measured with the app's judge (`decomp judge-worker`):

1. m2c printed four `lw; addiu cursor, 4` pairs as `void *` temporaries added to a `s32 *` cursor (a draft that was
   wrong C: the first `p + 4` is sixteen bytes). Written `x = *p++;` four times: 3472 -> 2765 (`m2c_cursor_chain`).
2. The pointer walked from `table + 0xC0` to `table + 0x240` in steps of 12, written as an unsigned index over a
   record array (`for i = 16; i < 48`): -> 665 (`symbol_walk_records`). The same pointer set to `&table[i]` inside
   an indexed loop was still 1890: every access has to index the table itself.
3. The search loop's `v != (key & 0x1FFE000)` written `key &= 0x1FFE000;` before the loop: -> 260
   (`loop_mask_in_place`).
4. The result's start address in a local of its own, not written back into the stream value: -> 110
   (`local_web_split`).
5. `t = x & 3; if (...) { ...; t = x & 3; } if (t == 2)` written as `if ((x & 3) == 2)`: -> 0
   (`reload_temp_inline`).

A clean rewrite matched sooner: the search loop reading the table word once into a block local (`u32 v = *p;`)
instead of reading `*p` in both exit tests was 1081 -> 0.

Failed experiments: a `goto` search loop and `while`/`do` forms reading `*p` twice (1081 to 1366); `u8` for the
byte the loop compares against (1366); a pointer loop `r = &table[16]; r < &table[48]` (3807).

What is controlled evidence and what is one function: one function; no compiler-source explanation was read.
