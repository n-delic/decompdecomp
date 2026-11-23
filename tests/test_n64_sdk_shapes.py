"""libultra routines told by structure, for builds no fingerprint table has (consoles/n64/sdk_shapes.py)."""
import struct

from decompnet import consoles
from decompnet.consoles.n64 import sdk_shapes


def _i(op, rs, rt, imm):
    return op << 26 | rs << 21 | rt << 16 | imm & 0xFFFF


JAL = 3 << 26 | 0x8000
NOP = 0


def _code(*words, pad=0):
    return b"".join(struct.pack(">I", w) for w in (*words, *([NOP] * pad)))


def _create_thread(**drop):
    words = {
        "id": _i(0x2B, 4, 5, 0x14),     # sw $a1, 0x14($a0)
        "flags": _i(0x29, 4, 0, 0x12),  # sh $zero, 0x12($a0)
        "state": _i(0x29, 4, 8, 0x10),  # sh $t0, 0x10($a0)
    }
    return _code(JAL, *[w for k, w in words.items() if k not in drop], JAL, pad=40)


def test_a_build_no_table_has_is_told_osCreateThread_by_the_thread_fields_it_sets():
    found = sdk_shapes.identify(_create_thread())
    assert found["name"] == "osCreateThread" and found["corpus"] == sdk_shapes.CORPUS
    # the same on a little-endian build of the layout is read the same way
    little = b"".join(struct.pack("<I", struct.unpack(">I", _create_thread()[i:i + 4])[0])
                      for i in range(0, len(_create_thread()), 4))
    assert sdk_shapes.identify(little, big_endian=False)["name"] == "osCreateThread"


def test_a_function_missing_any_of_the_thread_stores_or_the_calls_is_not_named():
    for missing in ("id", "flags", "state"):
        assert sdk_shapes.identify(_create_thread(**{missing: 1})) is None
    assert sdk_shapes.identify(_code(JAL, *[_i(0x2B, 4, 5, 0x14), _i(0x29, 4, 0, 0x12), _i(0x29, 4, 8, 0x10)], pad=40)) is None
    # a struct that only has a halfword of zero at +0x10 (cleared, not a state) isn't a thread's
    clear = _code(JAL, _i(0x2B, 4, 5, 0x14), _i(0x29, 4, 0, 0x12), _i(0x29, 4, 0, 0x10), JAL, pad=40)
    assert sdk_shapes.identify(clear) is None
    assert sdk_shapes.identify(_create_thread()[:60]) is None  # too short to be one


def _yield_thread(*, branch=False, extra_store=False, state=2):
    words = [_i(9, 29, 29, -0x28), _i(0x2B, 29, 31, 0x1C), JAL, _i(0x2B, 29, 16, 0x18),
             _i(0x0F, 0, 15, 0x8025), _i(0x23, 15, 15, 0x16E0), _i(9, 0, 14, state), _i(0x0F, 0, 4, 0x8025),
             _i(9, 4, 4, 0x16D8), JAL, _i(0x29, 15, 14, 0x10), JAL, _i(0, 0, 0, 0), _i(0x23, 29, 31, 0x1C),
             _i(9, 29, 29, 0x28), 0x03E00008, NOP]
    if branch:
        words.insert(5, _i(4, 0, 0, 1))  # beq $zero, $zero
    if extra_store:
        words.insert(5, _i(0x2B, 4, 4, 8))  # sw $a0, 8($a0): the run queue's, not the frame's
    return _code(*words)


def test_osYieldThread_is_told_by_marking_the_running_thread_runnable_between_three_calls():
    assert sdk_shapes.identify(_yield_thread())["name"] == "osYieldThread"
    assert sdk_shapes.identify(_yield_thread(branch=True)) is None
    assert sdk_shapes.identify(_yield_thread(extra_store=True)) is None
    assert sdk_shapes.identify(_yield_thread(state=4)) is None  # running, not runnable: a different routine's store


def test_the_n64_console_offers_the_shapes_to_library_detection():
    n64 = consoles.get("n64")
    assert n64.sdk_shape_sizes == (sdk_shapes._MIN_ANY, sdk_shapes._MAX)
    assert n64.sdk_shape_identity(_create_thread())["name"] == "osCreateThread"
    assert consoles.get("ps1").sdk_shape_identity(_create_thread()) is None
