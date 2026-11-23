"""libultra's thread and message-queue routines told by structure (consoles/n64/sdk_shapes.py), for builds no
fingerprint table has: a game that links its own copy runs it against kernel structures the host doesn't keep, so
its threads never start (a TLB-mapped game whose osStartThread was its own) or never wake (its own osRecvMesg).
The code here is built from instruction words, never copied from a game."""
import struct

from decompnet.consoles.n64 import sdk_shapes


def _i(op, rs, rt, imm):
    return op << 26 | rs << 21 | rt << 16 | imm & 0xFFFF


JAL = 3 << 26 | 0x8000
NOP = 0
JR_RA = 0x03E00008


def _code(*words, pad=0):
    return b"".join(struct.pack(">I", w) for w in (*words, *([NOP] * pad)))


def _build(spec):
    pad = spec.pop("pad")
    return _code(*[w for words in spec.values() for w in words], pad=pad)


def _lhu_state():
    return _i(0x25, 4, 14, 0x10)  # lhu $t6, 0x10($a0): the thread's state


def _sh_state(reg=15):
    return _i(0x29, 4, reg, 0x10)  # sh $reg, 0x10($a0)


def _start_thread(**override):
    spec = {"state": [_lhu_state()], "stores": [_sh_state()] * 4, "queue": [_i(0x23, 4, 8, 8)],
            "priority": [_i(0x23, 2, 9, 4)], "constants": [_i(9, 0, 15, 2), _i(9, 0, 24, 8), _i(9, 0, 25, 1)],
            "calls": [JAL] * 7, "pad": 60}
    spec.update(override)
    return _build(spec)


def test_osStartThread_is_told_by_queueing_a_thread_runnable_or_waiting_and_reading_the_running_priority():
    assert sdk_shapes.identify(_start_thread())["name"] == "osStartThread"
    assert sdk_shapes.identify(_start_thread(stores=[_sh_state()] * 2)) is None  # two state stores: not it
    assert sdk_shapes.identify(_start_thread(calls=[JAL] * 3)) is None
    assert sdk_shapes.identify(_start_thread(stores=[_sh_state()] * 4 + [_i(0x29, 4, 15, 0x12)])) is None
    assert sdk_shapes.identify(_start_thread(constants=[_i(9, 0, 15, 2), _i(9, 0, 25, 1)])) is None  # no waiting (8)


def _set_priority(**override):
    spec = {"state": [_lhu_state()], "stores": [_sh_state()], "priority": [_i(0x2B, 4, 5, 4), _i(0x23, 2, 9, 4)],
            "queue": [_i(0x23, 4, 8, 8)], "constants": [_i(9, 0, 15, 2)], "calls": [JAL] * 5, "pad": 40}
    spec.update(override)
    return _build(spec)


def test_osSetThreadPri_is_told_by_storing_the_priority_and_one_state():
    assert sdk_shapes.identify(_set_priority())["name"] == "osSetThreadPri"
    assert sdk_shapes.identify(_set_priority(priority=[_i(0x23, 2, 9, 4)])) is None  # no priority store: a reader
    assert sdk_shapes.identify(_set_priority(stores=[_sh_state(), _i(0x29, 4, 15, 8)])) is None  # another halfword
    # an audio engine's routine with the same offsets and halfword stores of its own was a false match in a game
    assert sdk_shapes.identify(_set_priority(stores=[_sh_state()] + [_i(0x29, 16, 2, o) for o in (8, 0xA, 0xC)])) is None


def _destroy_thread(**override):
    spec = {"state": [_lhu_state()],
            "links": [_i(0x23, 4, 8, 0xC), _i(0x2B, 4, 8, 0xC), _i(0x23, 9, 9, 0xC), _i(0x2B, 9, 10, 0xC)],
            "constants": [_i(9, 0, 15, 1)], "calls": [JAL] * 4, "pad": 40}
    spec.update(override)
    return _build(spec)


def test_osDestroyThread_is_told_by_unlinking_a_thread_from_the_list_of_all_threads():
    assert sdk_shapes.identify(_destroy_thread())["name"] == "osDestroyThread"
    assert sdk_shapes.identify(_destroy_thread(links=[_i(0x23, 4, 8, 0xC)])) is None  # not walking the list
    assert sdk_shapes.identify(_destroy_thread(constants=[_i(9, 0, 15, 2)])) is None  # makes threads runnable
    assert sdk_shapes.identify(_destroy_thread(calls=[JAL] * 8)) is None


def _queue_routine(kind, modulo=True):
    """The ring buffer of an OSMesgQueue (validCount +8, first +0xC, msgCount +0x10, msg +0x14) as each of
    osRecvMesg, osSendMesg and osJamMesg works it."""
    common = [JAL] * 6 + [_i(0, 8, 9, 0x1B) if modulo else NOP, _i(0x29, 5, 14, 0x10), _i(9, 0, 14, 8),
                          _i(9, 0, 2, -1), _i(0x23, 4, 8, 0x14), _i(0x2B, 4, 9, 8)]
    if kind == "recv":
        extra = [_i(0x23, 4, 10, 0xC), _i(0x23, 4, 11, 0xC), _i(0x23, 4, 12, 0x10), _i(0x2B, 4, 10, 0xC),
                 _i(9, 10, 10, -1)]
    elif kind == "send":
        extra = [_i(0x23, 4, 10, 0xC), _i(0x23, 4, 12, 0x10), _i(0x23, 4, 13, 0x10), _i(0x23, 4, 13, 8),
                 _i(0x23, 4, 13, 8), _i(9, 10, 10, 1)]
    else:  # jam
        extra = [_i(0x23, 4, 10, 0xC), _i(0x23, 4, 12, 0x10), _i(0x23, 4, 13, 0x10), _i(0x23, 4, 13, 0x10),
                 _i(0x2B, 4, 10, 0xC), _i(9, 10, 10, -1), _i(9, 10, 10, 1)]
    return _code(*common, *extra, pad=60)


def test_the_message_queue_routines_are_told_by_how_they_work_the_ring_buffer():
    assert sdk_shapes.identify(_queue_routine("recv"))["name"] == "osRecvMesg"
    assert sdk_shapes.identify(_queue_routine("send"))["name"] == "osSendMesg"
    assert sdk_shapes.identify(_queue_routine("jam"))["name"] == "osJamMesg"
    for kind in ("recv", "send", "jam"):  # a ring needs its modulo: without the divide it is another routine
        assert sdk_shapes.identify(_queue_routine(kind, modulo=False)) is None


def test_osCreateMesgQueue_is_told_by_its_six_stores_and_no_loads_or_calls():
    stores = [_i(0x2B, 4, 6, o) for o in (0, 4, 8, 0xC, 0x10, 0x14)]
    assert sdk_shapes.identify(_code(*stores, JR_RA, NOP, pad=2))["name"] == "osCreateMesgQueue"
    assert sdk_shapes.identify(_code(*stores[:5], JR_RA, NOP, pad=3)) is None  # one store short
    assert sdk_shapes.identify(_code(*stores, _i(0x23, 4, 8, 4), JR_RA, NOP, pad=1)) is None  # a load
