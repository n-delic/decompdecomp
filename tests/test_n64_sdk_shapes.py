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
        "pc": _i(0x2B, 4, 6, 0x11C),    # sw $a2, 0x11C($a0): the entry point into the saved context
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
    for missing in ("id", "flags", "state", "pc"):
        assert sdk_shapes.identify(_create_thread(**{missing: 1})) is None
    assert sdk_shapes.identify(_code(JAL, *[_i(0x2B, 4, 5, 0x14), _i(0x29, 4, 0, 0x12), _i(0x29, 4, 8, 0x10)], pad=40)) is None
    # a struct that only has a halfword of zero at +0x10 (cleared, not a state) isn't a thread's
    clear = _code(JAL, _i(0x2B, 4, 5, 0x14), _i(0x29, 4, 0, 0x12), _i(0x29, 4, 0, 0x10), JAL, pad=40)
    assert sdk_shapes.identify(clear) is None
    assert sdk_shapes.identify(_create_thread()[:60]) is None  # too short to be one


def test_an_unoptimised_build_stores_the_id_through_a_register_it_was_spilled_to():
    """A game's own libultra, built without optimisation, had `lw $t6, 0x2C($sp); sw $t6, 0x14($t7)`: the id is not
    stored from $a1. The stack's own store of the argument is no thread's id; and a routine with the id and halfword
    stores but no saved pc (the debugger's thread status reader, 472 bytes in several games) is not osCreateThread."""
    spilled = _code(JAL, _i(0x2B, 15, 14, 0x14), _i(0x29, 9, 0, 0x12), _i(0x29, 9, 24, 0x10), _i(0x2B, 11, 10, 0x11C),
                    JAL, pad=40)
    assert sdk_shapes.identify(spilled)["name"] == "osCreateThread"
    on_the_stack = _code(JAL, _i(0x2B, 29, 14, 0x14), _i(0x29, 9, 0, 0x12), _i(0x29, 9, 24, 0x10),
                         _i(0x2B, 11, 10, 0x11C), JAL, pad=40)
    assert sdk_shapes.identify(on_the_stack) is None
    # a program's first function that sets a thread up inline, among many calls, is not the library's routine
    inline = _code(*([JAL] * 6), _i(0x2B, 15, 14, 0x14), _i(0x29, 9, 0, 0x12), _i(0x29, 9, 24, 0x10),
                   _i(0x2B, 11, 10, 0x11C), pad=60)
    assert sdk_shapes.identify(inline) is None
    no_pc = _code(JAL, _i(0x2B, 15, 14, 0x14), _i(0x29, 9, 0, 0x12), _i(0x29, 9, 24, 0x10), JAL, pad=40)
    assert sdk_shapes.identify(no_pc) is None


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


def test_the_thread_getters_are_named_by_the_field_they_read():
    """A game's own `osGetThreadId` loads the running thread from the kernel's pointer, which only the real kernel
    keeps: it answered 0 to every caller, so a mutex comparing its owner with `osGetThreadId(0)` found itself the owner
    and never locked (a fighting game's main loop spun in its recursion path). The host answers for the thread."""
    def words(*hexes):
        return b"".join(bytes.fromhex(h) for h in hexes)

    head = ("14800003", "00000000", "3c04800c", "8c841cb0", "03e00008")
    assert sdk_shapes.identify(words(*head, "8c820014"))["name"] == "osGetThreadId"
    assert sdk_shapes.identify(words(*head, "8c820004"))["name"] == "osGetThreadPri"
    assert sdk_shapes.identify(words(*head, "8c820018")) is None  # some other field
    assert sdk_shapes.identify(words("00000000", "00000000", *head[2:], "8c820014")) is None  # no zero-argument branch
    assert sdk_shapes.identify(words(*head[:4], "ac850020", "03e00008", "8c820014")) is None  # it stores
