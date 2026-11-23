"""libultra's hardware routines told by structure (consoles/n64/sdk_shapes.py): the video manager's, the PI's DMA, the
RSP's, the audio interface's, the cache operations', the Count register's and the timers'. A game that decompresses or
loads its real code carries its own copy of each; it runs against registers and kernel lists the host doesn't keep
(no retrace, no DMA, no graphics task), so the port replaces it by the host's own, found by what it does.
The code here is built from instruction words, never copied from a game; tests/test_n64_port_shapes.py runs the host's
C test on the same words."""
import struct

import pytest

from decompnet.consoles.n64 import sdk_shapes

NOP = 0
JR_RA = 0x03E00008
JAL = 3 << 26 | 0x8000  # (the interrupt disable and restore: a leaf at 0x80020000 in the C test's image)
JAL_COUNT = 3 << 26 | 0x8040  # (a leaf at 0x80020100 that reads the Count register)
SLTU = 5 << 21 | 6 << 16 | 1 << 11 | 0x2B


def _i(op, rs, rt, imm):
    return op << 26 | rs << 21 | rt << 16 | imm & 0xFFFF


def _code(*words, pad=0):
    return b"".join(struct.pack(">I", w) for w in (*words, *([NOP] * pad)))


def _stores(base, *offsets, reg=5):
    return [_i(0x2B, base, reg, o) for o in offsets]


def vi_swap_buffer(ori=0x10):
    return _code(JAL, *_stores(15, 4, reg=4), _i(0x25, 4, 3, 0), _i(0x0D, 3, 3, ori), _i(0x29, 4, 3, 0), JAL, pad=10)


def vi_black(andi=0xFFDF):
    return _code(JAL, _i(0x25, 4, 3, 0), _i(0x0D, 3, 3, 0x20), _i(0x0C, 3, 3, andi), _i(0x29, 4, 3, 0), JAL, pad=14)


def vi_set_event(ori=None):
    return _code(JAL, _i(0x2B, 3, 4, 0x10), _i(0x2B, 3, 5, 0x14), _i(0x29, 3, 6, 2), *([_i(0x0D, 3, 3, ori)] if ori else []),
                 JAL, pad=12)


def vi_set_mode(extra=()):
    return _code(JAL, _i(0x2B, 15, 4, 8), _i(0x23, 9, 10, 4), _i(0x2B, 8, 10, 0xC), _i(9, 0, 24, 1), _i(0x29, 25, 24, 0),
                 *extra, JAL, pad=8)


def pi_start_dma(read=0xB, write=0xC, zero=0x14):
    return _code(_i(9, 0, 2, read), _i(9, 0, 2, write), _i(0x29, 16, 2, 0), _i(0x28, 16, 5, 2),
                 *_stores(16, 4, 8, 0xC, 0x10, reg=3), _i(0x2B, 16, 0, zero), JAL, JAL, pad=40)


def epi_start_dma(zero=False):
    return _code(_i(9, 0, 2, 0xF), _i(9, 0, 2, 0x10), _i(0x29, 16, 2, 0), _i(0x24, 16, 3, 2),
                 _i(0x2B, 16, 0 if zero else 4, 0x14), JAL, JAL, pad=30)


def sp_task_start_go(reg=4, loop=True):
    return _code(_i(9, 0, reg, 0x125), JAL, *([_i(5, 2, 0, -3)] if loop else [NOP]), JAL, pad=10)


def ai_set_next_buffer(sb=True):
    return _code(_i(0x0F, 0, 5, 0xA450), _i(0x0D, 5, 5, 4), _i(0x0C, 2, 2, 0x1FFF),
                 *([_i(0x28, 1, 2, 0)] if sb else [NOP]), pad=36)


def ai_get_length(offset=4):
    return _code(_i(0x0F, 0, 2, 0xA450), _i(0x0D, 2, 2, offset), JR_RA, _i(0x23, 2, 2, 0))


def ai_set_frequency(luis=2):
    return _code(*[_i(0x0F, 0, 3, 0xA450)] * luis, _i(0x0D, 3, 3, 0x10), _i(0x0D, 3, 3, 0x14), pad=60)


def get_count(register=9):
    return _code(0x40004800 & ~(31 << 11) | register << 11 | 2 << 16, JR_RA, NOP)


def get_time(calls=(JAL, JAL_COUNT, JAL), pad=27):
    return _code(*calls, SLTU, pad=pad)  # (with the return and its delay slot added: 132 bytes)


def cache(*ops, calls=0, pad=12):
    return _code(*[_i(0x2F, 8, op, 0) for op in ops], *([JAL] * calls), pad=pad)


def set_timer(extra=()):
    return _code(_i(0x2B, 16, 0, 0), _i(0x2B, 16, 0, 4), *_stores(16, 8, 0xC, 0x10, 0x14, 0x18, 0x1C), *extra,
                 JAL, JAL, pad=25)


def functions():
    """Each routine and its near misses: (label, bytes). A near miss is the same routine short of one thing the test asks."""
    return [
        ("vi-swap", vi_swap_buffer()), ("vi-swap-other-flag", vi_swap_buffer(ori=0x8)),
        ("vi-black", vi_black()), ("vi-black-no-clear", vi_black(andi=0xFFFF)),
        ("vi-event", vi_set_event()), ("vi-event-with-flags", vi_set_event(ori=0x10)),
        ("vi-mode", vi_set_mode()), ("vi-mode-with-event-store", vi_set_mode(extra=[_i(0x2B, 8, 10, 0x10)])),
        ("pi", pi_start_dma()), ("pi-no-write-type", pi_start_dma(write=0x7)), ("pi-no-zero-word", pi_start_dma(zero=0x18)),
        ("epi", epi_start_dma()), ("epi-zero-handle", epi_start_dma(zero=True)),
        ("sp", sp_task_start_go()), ("sp-wrong-register", sp_task_start_go(reg=8)), ("sp-no-loop", sp_task_start_go(loop=False)),
        ("ai-next", ai_set_next_buffer()), ("ai-next-no-byte", ai_set_next_buffer(sb=False)),
        ("ai-length", ai_get_length()), ("ai-length-other-register", ai_get_length(offset=8)),
        ("ai-frequency", ai_set_frequency()), ("ai-frequency-one-lui", ai_set_frequency(luis=1)),
        ("count", get_count()), ("count-status", get_count(register=12)),
        ("time", get_time()), ("time-interrupts-only", get_time(calls=(JAL, JAL, JAL))),
        ("time-another-size", get_time(pad=28)),  # (a game's own function that reads the time the same way: 136 bytes)
        ("icache", cache(0x10)), ("icache-with-writeback", cache(0x10, 0x19)), ("icache-with-a-call", cache(0x10, calls=1)),
        ("dcache", cache(0x15, 0x11, 1)), ("wb", cache(0x19)), ("wb-all", cache(1, pad=6)), ("wb-all-big", cache(1, pad=30)),
        ("timer", set_timer()), ("timer-more-fields", set_timer(extra=[_i(0x2B, 16, 5, 0x20)])),
        ("timer-halfword", set_timer(extra=[_i(0x29, 16, 5, 0x2)])),
    ]


EXPECTED = {
    "vi-swap": "osViSwapBuffer", "vi-black": "osViBlack", "vi-event": "osViSetEvent", "vi-mode": "osViSetMode",
    "pi": "osPiStartDma", "epi": "osEPiStartDma", "sp": "osSpTaskStartGo", "ai-next": "osAiSetNextBuffer",
    "ai-length": "osAiGetLength", "ai-frequency": "osAiSetFrequency", "count": "osGetCount", "time": "osGetTime",
    "icache": "osInvalICache", "dcache": "osInvalDCache", "wb": "osWritebackDCache", "wb-all": "osWritebackDCacheAll",
    "timer": "osSetTimer",
}
# (the bytes alone are the routine's; the host also asks what it calls: the interrupt disable and the Count read)
HOST_ONLY_REJECTS = {"time-interrupts-only"}


def _with_return(code: bytes) -> bytes:
    """The function as the interpreter finds it: up to its `jr $ra` and the delay slot (one is added when it has none)."""
    words = [w for (w,) in struct.iter_unpack(">I", code)]
    if JR_RA not in words:
        words += [JR_RA, NOP]
    at = words.index(JR_RA)
    return b"".join(struct.pack(">I", w) for w in words[:at + 2])


@pytest.mark.parametrize("label,code", functions())
def test_each_routine_is_named_and_each_near_miss_is_not(label, code):
    got = (sdk_shapes.identify(_with_return(code)) or {}).get("name")
    if label == "time-interrupts-only":
        assert got == "osGetTime"  # bytes alone: its callees are what the host checks
        return
    assert got == EXPECTED.get(label), (label, got)


def test_the_tests_cover_every_hardware_routine_the_port_replaces():
    from decompnet.port.wasm import interp
    assert set(EXPECTED.values()) <= set(interp.SHAPE_NAMES)
    assert len(set(EXPECTED.values())) == 17
