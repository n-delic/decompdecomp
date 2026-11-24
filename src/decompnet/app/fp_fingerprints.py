"""Whether a MIPS GCC function was built with `-mfp32` or `-mfp64`, read from the target's own floating-point code.

Three small near-matches of an N64 game built with SN GCC 2.8.1 and `-mfp64` were stuck for the Deep Permuter: their
best C differed from the target only in FP register rows. Under the same compiler with `-mfp32` instead of `-mfp64`
they were exact (25 -> 0, 10 -> 0, and a third a user applied by hand), and two more went 380 -> ~230 and 505 -> 120.
No source spelling reaches it: the register file the compiler allocates single-precision values from is the profile's.
What named it:

- the target's single-precision loads, stores, moves and arithmetic use even FP registers only (`$f2`, `$f4`, `$f12`),
  where the `-mfp64` compile of the same C uses odd or other ones (a double constant: `ldc1 $f2` in the target,
  `$f1` in ours);
- the diff of the best C is only FP register rows: every differing row is the same instruction with another `$fN`.

Either reading proposes the same compiler with the other floating-point flag (`-mfp64` -> `-mfp32`, and the reverse
when the project builds with `-mfp32` and the target's arithmetic reads odd registers). Only the judge decides: the
profile is scored like any other (profile_detection, matching_preflight, profile_climb), and an exact result is never
applied by itself: it is a suggestion for the user's review (`is_fp_variant`, the same gate `-ffast-math` has).
The module reads code and flags only; it never compiles or judges.
"""
from __future__ import annotations

import re
import struct

FP_REGISTER = re.compile(r'\$f\d+')
MIN_SINGLES = 2  # distinct single-precision registers the target must show before its parity says anything
FP_FLAGS = ('-mfp32', '-mfp64')
ARITHMETIC = {0, 1, 2, 3}  # add.s sub.s mul.s div.s: fd, fs, ft
COMPARE_FROM = 0x30  # c.cond.s: fs, ft
UNARY = {4, 5, 6, 7, 0xC, 0xD, 0xE, 0xF, 0x24}  # sqrt abs mov neg round trunc ceil floor cvt.w: fd, fs
CONVERT_FROM = {0x20, 0x21}  # cvt.s, cvt.d: fd, fs


def registers(code: bytes, big_endian: bool = True) -> tuple[set[int], set[int], set[int]]:
    """(registers of single-precision values the code writes or reads, the subset used by arithmetic and compares,
    registers of doubles).

    `lwc1`/`swc1` and `mtc1`/`mfc1` name one register; the `.s` operations name their operands, `ldc1`/`sdc1` and the
    `.d` operations a double's first register. A double's two `lwc1` halves are in the first set only: the second set
    is what a compiler chose for a single in an operation. An odd double register exists only under `-mfp64`."""
    words = struct.unpack((">" if big_endian else "<") + f"{len(code) // 4}I", code[:len(code) // 4 * 4])
    seen: set[int] = set()
    arithmetic: set[int] = set()
    doubles: set[int] = set()
    for w in words:
        op = w >> 26
        if op in (0x31, 0x39):  # lwc1, swc1
            seen.add((w >> 16) & 31)
        elif op in (0x35, 0x3D):  # ldc1, sdc1
            doubles.add((w >> 16) & 31)
        elif op == 0x11:
            fmt, fn = (w >> 21) & 31, w & 63
            fd, fs, ft = (w >> 6) & 31, (w >> 11) & 31, (w >> 16) & 31
            if fmt in (0, 4):  # mfc1, mtc1
                seen.add(fs)
            elif fmt in (16, 17):
                regs = {fs}
                if fn in ARITHMETIC:
                    regs |= {fd, ft}
                elif fn in UNARY or fn in CONVERT_FROM:
                    regs.add(fd)
                elif fn >= COMPARE_FROM:
                    regs.add(ft)
                else:
                    continue
                if fmt == 17:
                    doubles |= regs
                    if fn == 0x20:  # cvt.s.d: the result is a single
                        doubles.discard(fd)
                        seen.add(fd)
                        arithmetic.add(fd)
                    continue
                if fn == 0x21:  # cvt.d.s: the result is a double
                    regs.discard(fd)
                    doubles.add(fd)
                seen |= regs
                arithmetic |= regs
    return seen, arithmetic, doubles


def parity(code: bytes, big_endian: bool = True) -> str | None:
    """'even' when every floating-point register the target uses is even (at least MIN_SINGLES of them, one in an
    operation), 'odd' when an operation reads or writes an odd single or a double sits in an odd register (only
    `-mfp64` allows it), else None (nothing to say)."""
    seen, arithmetic, doubles = registers(code, big_endian)
    if any(r % 2 for r in arithmetic | doubles):
        return 'odd'
    if len(seen | doubles) >= MIN_SINGLES and (arithmetic or doubles) and not any(r % 2 for r in seen | doubles):
        return 'even'
    return None


def mode(flags) -> str:
    """The floating-point register model the flags build: '64' for `-mfp64`, else '32' (GCC's MIPS default here)."""
    flags = flags.split() if isinstance(flags, str) else list(flags)
    return '64' if '-mfp64' in flags and not any(f == '-mfp32' for f in flags[flags.index('-mfp64'):]) else '32'


def flipped(flags) -> list[str]:
    """The flags with the other floating-point flag: `-mfp64` replaced by `-mfp32` where it stands, and the reverse;
    `-mfp64` is appended when neither is there (the compiler's default is the 32-bit file)."""
    flags = flags.split() if isinstance(flags, str) else list(flags)
    other = '-mfp32' if mode(flags) == '64' else '-mfp64'
    kept = [other if f in FP_FLAGS else f for f in flags]
    return kept if any(f in FP_FLAGS for f in flags) else [*flags, other]


def register_rows_only(rows) -> bool:
    """True when the diff has differing rows and every one is the same instruction with other FP registers."""
    differing = [(left, right) for marker, left, right in rows if marker != ' ']
    if not differing:
        return False
    for left, right in differing:
        if (left == right or not FP_REGISTER.search(left + right)
                or FP_REGISTER.sub('$f#', left).strip() != FP_REGISTER.sub('$f#', right).strip()):
            return False
    return True


def why(own_flags, code: bytes | None, rows, big_endian: bool = True) -> str | None:
    """The reading that points to the other floating-point flag, or None. `rows` may be empty (no diff known)."""
    current = mode(own_flags)
    seen = parity(code, big_endian) if code else None
    if seen == 'even' and current == '64':
        return 'its single-precision values use even FP registers only, where -mfp64 allocates odd ones'
    if seen == 'odd' and current == '32':
        return 'its single-precision arithmetic reads odd FP registers, which -mfp32 does not use'
    if rows and register_rows_only(rows) and seen != ('even' if current == '32' else 'odd'):
        return 'the saved C differs from the target only in FP register rows'
    return None


def probe(own, code, rows=(), big_endian: bool = True) -> list[str] | None:
    """The flags to judge the saved C under, `own`'s with the other floating-point flag, when the evidence points to it."""
    if not own.get('toolchain') or own.get('cmd') or own['toolchain'].startswith('ido-') or why(
            own['flags'], code, rows, big_endian) is None:
        return None
    return flipped(own['flags'])


def is_fp_variant(own_flags, flags) -> bool:
    """True when `flags` are `own_flags` with only the floating-point flag changed: a trial the user must approve."""
    own_flags = own_flags.split() if isinstance(own_flags, str) else list(own_flags)
    flags = flags.split() if isinstance(flags, str) else list(flags)
    strip = lambda items: [f for f in items if f not in FP_FLAGS]  # noqa: E731
    return strip(own_flags) == strip(flags) and mode(own_flags) != mode(flags)
