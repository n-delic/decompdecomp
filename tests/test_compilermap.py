"""The compiler map: habits that rule a compiler out, and the judge's confirmations spreading over neighbouring
functions (one object file is one compiler). Hand-made MIPS words; the survey that compiles is the engine's."""

import struct

from decompnet import compilermap as cm

GCC = ("ee-gcc-2.95.3", ("-O2", "-G8"))
GCC_G0 = ("ee-gcc-2.96", ("-O2", "-G0"))
CW = ("mwcps2-3.0.1", ("-O3", "-sdatathreshold", "8"))
NOP, JR_RA = 0, 0x03E00008
BEQL = (0x14 << 26) | (2 << 21) | 3  # beql v0, zero, +3
GP_LOAD = (0x23 << 26) | (28 << 21) | (2 << 16) | 0x10  # lw v0, 0x10(gp)


def code(*words):
    words = list(words) + [NOP] * (8 - len(words)) + [JR_RA, NOP]
    return struct.pack(f"<{len(words)}I", *words)


def test_habits_a_compilers_matched_code_never_shows_rule_it_out():
    funcs = []
    # matched GCC code uses branch-likely now and then; matched CodeWarrior code never does
    for i in range(30):
        funcs.append((0x100000 + i * 0x40, 0x28, code(BEQL if i % 3 == 0 else NOP), GCC, True, None))
    for i in range(cm.NEVER_FROM):
        funcs.append((0x200000 + i * 0x40, 0x28, code(), CW, True, None))
    cw_beql = (0x200000 + (cm.NEVER_FROM + 5) * 0x40, 0x28, code(BEQL), CW, False, None)
    g0_gp = (0x300000, 0x28, code(GP_LOAD), GCC_G0, False, None)
    plain = (0x300040, 0x28, code(), GCC_G0, False, None)
    built = cm.build(funcs + [cw_beql, g0_gp, plain], big_endian=False)
    assert built.never[cm.kind(CW)] == ["branch-likely"]
    assert "$gp-relative" in built.never[cm.kind(GCC_G0)]
    assert built.contradicted == {cw_beql[0], g0_gp[0]}


def test_the_judges_confirmations_spread_to_their_neighbours():
    funcs = [(0x100000 + i * 0x40, 0x40, code(), GCC, False, CW if i in (3, 6) else None) for i in range(10)]
    built = cm.build(funcs, big_endian=False)
    looks = [cm.kind(built.of[f[0]]) for f in funcs]
    assert looks == [cm.kind(GCC)] * 3 + [cm.kind(CW)] * 4 + [cm.kind(GCC)] * 3
    (run,) = built.proposals(256, 3)
    assert (run.start, run.end, run.functions, run.confirmed) == (0x1000C0, 0x1001C0, 4, 2)
    assert run.label == CW


def test_without_confirmations_nothing_is_proposed():
    funcs = [(0x100000 + i * 0x40, 0x40, code(), GCC, i % 2 == 0, None) for i in range(10)]
    built = cm.build(funcs, big_endian=False)
    assert not built.proposals(256, 3) and not built.contradicted


def test_a_functions_habits_are_read_once(monkeypatch):
    """GTA San Andreas: decoding every function's code again was 590 of the 930 ms each rebuild took, after every
    match. The code itself is the key, so a function whose code changes is read again."""
    seen = []
    real = cm.quirks
    monkeypatch.setattr(cm, "_quirks_seen", {})
    monkeypatch.setattr(cm, "quirks", lambda c, big: seen.append(c) or real(c, big))
    funcs = [(0x100000 + i * 0x40, 0x28, code(BEQL if i % 2 else NOP), GCC, i < 3, None) for i in range(10)]
    first = cm.build(funcs, big_endian=False)
    again = cm.build([f[:4] + (True,) + f[5:] for f in funcs], big_endian=False)  # more matched, the same code
    assert len(seen) == 2  # two different bodies among the ten functions
    assert first.examples != again.examples
