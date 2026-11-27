---
title: Byte swaps written as inline helpers, and one word carrying each divided size
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 100-instruction N64 parser of a little-endian image header: it swaps words and halfwords of a stream, divides each
section's byte size by 4 and records pointers into the stream. Symptoms: `srl` by 24 where the target shifts by 16 and
then by 8, the record parameter in `$a1` where the target copies it to `$a3`, and a `bgez`/`move` pair in the first
signed `/ 4` where ours had `bltzl`.

The source (a clean rewrite from the assembly, each step judged with the app's judge):

- Expanded swap macro: 3405. `static __inline__ u32 swap16(u16 v)` and a word swap of two calls: 1870.
- The stream parameter stepped itself (`tim++`) instead of a second cursor: 1100.
- The shifted upper half named inside the word swap (`high = swap16(v) << 16; return high + swap16(v >> 16);`): 920.
- `tim += img->csize;` before the in-place halfword swaps, not after them: 310.
- One local `n` for both sections (`n = swap32(*tim); img->csize = n / 4;`, then the same for the second size): 0.

decomp-repair `byte_swap_helpers` writes the helpers (`byte_swap_inline_helpers`, `_staged`) and the shared dividend
(`shared_quotient_word`, `byte_swap_helpers_quotient`). Controlled: the matched C with its swaps expanded back to m2c's
form and unshared divisions scores 2035; the combined child is 0, the staged helpers alone 310, unstaged 490.

Failed or still open: on the raw m2c draft (4185) the helpers reach 2470 (the older `byte_swap_spelled_out` 2470 too).
By hand from there: one byte cursor in place of m2c's two (whose `u32 *` offsets were scaled wrongly) 2230, the shared
dividend 1920, the cursor step before the swaps 1620; `reload_temp_inline`, `single_use_inline` and the record views
found nothing below 1620. The typed record and the parameter stepped itself have no rewrite from that form.

One function; why GCC wants the helper form and the shared local is a hypothesis.
