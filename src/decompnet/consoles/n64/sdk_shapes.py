"""libultra routines told by what they do to the kernel's structures, for builds no fingerprint table has.

A game links its own build of libultra (2.0D to 2.0G have no archive; some studios rebuilt it), so the same routine
comes in sizes the tables lack: `osCreateThread` is 208 bytes in most games, 200 in one, 216 in another, 224 in a third.
Byte tables can't name each one. The port needs the name, because the host's thread functions replace the game's copy:
a game whose own `osCreateThread` ran never registered a thread with the host, and its boot ended with no thread
running (a game with 4 MB pages mapped through the TLB, black window).

A routine is told by the stores that are the same in every build because the struct is: `osCreateThread` sets an
`OSThread`'s id (`sw $a1, 0x14`), clears its flags (`sh $zero, 0x12`), sets its state (`sh <r>, 0x10`) and calls the
interrupt disable and restore routines. Run over every function of every ROM project here, the test found 50
functions of 100 to 700 bytes, and each was an `osCreateThread` (named already, or the unnamed build of one game).
Only the port asks (`identify(builtin=True)`): the display names stay exact-match only.

The same goes for the rest of the thread routines (`osStartThread`, `osSetThreadPri`, `osDestroyThread`) and the
message queue's (`osRecvMesg`, `osSendMesg`, `osJamMesg`, `osCreateMesgQueue`): a game that runs its own copy of one
keeps its threads in queues the host knows nothing of (an `osStartThread` that never started the thread it was
given, an `osRecvMesg` that parked a thread in the game's own `__osEnqueueAndYield`). Each is told by the
`OSThread` and `OSMesgQueue` fields it reads and writes and its calls. Over every ROM project here, each hit was the
routine itself (named already, or the same routine under a prefixed name); an audio engine's routine that shared
most of `osSetThreadPri`'s offsets showed that a halfword store anywhere but +0x10 rules a thread routine out.

The port host runs the same tests at run time (port_wasm/mips.c's shape_identify, a C port: change them together,
tests/test_n64_port_shapes.py runs both over the same words), for the games that decompress or load their real code
and so carry their own libultra in code the build never saw: when the interpreter is about to run a function it has
no C for, the routine is told by these tests and, when the host defines it, the host's runs instead.
"""

CORPUS = "N64 library routines by structure"
_MIN, _MAX = 100, 700
_MIN_ANY = 32  # (the smallest routine here, osYieldThread: 60 to 80 bytes)
_EVENT_SIZES = frozenset({88, 92, 96, 100, 104, 108, 168, 172, 176, 180, 184})  # osSetEventMesg, as builds have it


def _words(code: bytes, big_endian: bool) -> list[int]:
    order = "big" if big_endian else "little"
    return [int.from_bytes(code[i:i + 4], order) for i in range(0, len(code) - 3, 4)]


def _is(word: int, op: int, rt: int | None, imm: int) -> bool:
    return word >> 26 == op and word & 0xFFFF == imm and (rt is None or word >> 16 & 31 == rt)


def thread_creation(words: list[int]) -> bool:
    """`osCreateThread(thread, id, entry, arg, sp, pri)`: id into thread+0x14, flags cleared at +0x12, state (a
    halfword) set at +0x10, interrupts disabled and restored around linking the thread into the active queue."""
    # sw <the id>, 0x14(thread): from $a1 in an optimised build, through a register the argument was spilled to and
    # reloaded in an unoptimised one (a game's own build of libultra had `lw $t6, 0x2C($sp); sw $t6, 0x14($t7)`)
    ids = any(_is(w, 0x2B, None, 0x14) and (w >> 21 & 31) != 29 for w in words)
    # and the entry point into the saved context's pc (+0x11C): what a routine that only reads a thread's fields
    # (the debugger's thread status) and has the stores of an id and the two halfwords does not do
    ids = ids and any(_is(w, 0x2B, None, 0x11C) for w in words)
    flags = any(_is(w, 0x29, 0, 0x12) for w in words)  # sh $zero, 0x12(thread)
    state = any(_is(w, 0x29, None, 0x10) and w >> 16 & 31 != 0 for w in words)  # sh <non-zero>, 0x10(thread)
    calls = sum(1 for w in words if w >> 26 == 3)  # jal: the interrupt disable and restore routines
    # exactly two calls in every one of the hundred copies here (the interrupt disable and restore): a larger function
    # that sets a thread up inline (a program's first function) has more, and is not the library's routine
    return ids and flags and state and 2 <= calls <= 3


def thread_yield(words: list[int]) -> bool:
    """`osYieldThread()`: interrupts off, the running thread's state (a halfword at +0x10) set to runnable (2), the
    run queue enqueued and yielded, interrupts restored: three calls, no branch, and no store besides the frame's and
    that one halfword."""
    if not 8 <= len(words) <= 32 or sum(1 for w in words if w >> 26 == 3) != 3:
        return False
    if any(w >> 26 in (1, 4, 5, 6, 7, 0x14, 0x15, 0x16, 0x17) for w in words):  # no conditional branches
        return False
    stores = [w for w in words if w >> 26 in (0x28, 0x29, 0x2A, 0x2B, 0x2E, 0x31, 0x39, 0x3D)]
    states = [w for w in stores if w >> 26 == 0x29 and w & 0xFFFF == 0x10]
    others = [w for w in stores if w not in states and not (w >> 26 == 0x2B and w >> 21 & 31 == 29)]  # sw ..($sp)
    runnable = {w >> 16 & 31 for w in words if w >> 26 == 9 and w >> 21 & 31 == 0 and w & 0xFFFF == 2}  # addiu r,$zero,2
    return len(states) == 1 and not others and (states[0] >> 16 & 31) in runnable


def _thread_features(words: list[int]) -> dict:
    """What a routine does to an OSThread (next 0x00, priority 0x04, queue 0x08, tlnext 0x0C, state 0x10 as a
    halfword) and how many calls it makes."""
    constants = {w & 0xFFFF for w in words if w >> 26 == 9 and w >> 21 & 31 == 0}  # addiu r, $zero, k
    return {
        "calls": sum(1 for w in words if w >> 26 == 3),
        "reads_state": any(w >> 26 in (0x21, 0x25) and w & 0xFFFF == 0x10 for w in words),  # lh/lhu 0x10
        "state_stores": sum(1 for w in words if w >> 26 == 0x29 and w & 0xFFFF == 0x10),  # sh ..., 0x10
        "other_halfword_stores": sum(1 for w in words if w >> 26 == 0x29 and w & 0xFFFF != 0x10),
        "stores_priority": any(w >> 26 == 0x2B and w & 0xFFFF == 4 for w in words),
        "reads_priority": any(w >> 26 == 0x23 and w & 0xFFFF == 4 for w in words),
        "reads_queue": any(w >> 26 == 0x23 and w & 0xFFFF == 8 for w in words),
        "tlnext": sum(1 for w in words if w >> 26 in (0x23, 0x2B) and w & 0xFFFF == 0xC),
        "runnable": 2 in constants, "waiting": 8 in constants, "stopped": 1 in constants,
    }


def thread_start(words: list[int], size: int) -> bool:
    """`osStartThread(thread)`: looks at the state (a halfword at +0x10), makes the thread runnable (2) or waiting
    (8) and queues it, then reads the running thread's priority to decide whether to yield: seven to nine calls
    (interrupts, enqueue, pop, dispatch, yield, restore) and three or four state stores, none to another halfword."""
    f = _thread_features(words)
    return (200 <= size <= 450 and f["reads_state"] and f["state_stores"] >= 3 and not f["other_halfword_stores"]
            and not f["stores_priority"] and f["reads_priority"] and f["reads_queue"] and f["runnable"]
            and f["waiting"] and f["calls"] >= 6)


def thread_priority(words: list[int], size: int) -> bool:
    """`osSetThreadPri(thread, pri)`: stores the priority (+0x04), re-queues a thread that is waiting on a queue
    (+0x08, by its state), and marks the running thread runnable (one state store) before yielding."""
    f = _thread_features(words)
    return (150 <= size <= 320 and f["reads_state"] and f["state_stores"] == 1 and not f["other_halfword_stores"]
            and f["stores_priority"] and f["reads_priority"] and f["reads_queue"] and f["runnable"]
            and f["calls"] >= 4)


def thread_destroy(words: list[int], size: int) -> bool:
    """`osDestroyThread(thread)`: takes the thread off its queue unless it is stopped, then off the list of every
    thread (the `tlnext` links at +0x0C, walked until a priority of -1), and dispatches another if it was running:
    three to five calls and no store to the thread's halfwords."""
    f = _thread_features(words)
    return (150 <= size <= 320 and f["reads_state"] and f["state_stores"] == 0 and not f["other_halfword_stores"]
            and f["tlnext"] >= 4 and f["stopped"] and not f["runnable"] and not f["waiting"]
            and 3 <= f["calls"] <= 5)


def _queue_features(words: list[int]) -> dict:
    """What a routine does to an OSMesgQueue (mtqueue 0x00, fullqueue 0x04, validCount 0x08, first 0x0C,
    msgCount 0x10, msg 0x14) and to an OSThread's state halfword (+0x10)."""
    def loads(offset):
        return sum(1 for w in words if w >> 26 == 0x23 and w & 0xFFFF == offset)

    def stores(offset):
        return sum(1 for w in words if w >> 26 == 0x2B and w & 0xFFFF == offset)

    def adds(imm):  # addiu r, s, imm with s a real register (not the constant form)
        return any(w >> 26 == 9 and w >> 21 & 31 != 0 and w & 0xFFFF == imm for w in words)

    constant = {w & 0xFFFF for w in words if w >> 26 == 9 and w >> 21 & 31 == 0}  # addiu r, $zero, k
    return {
        "calls": sum(1 for w in words if w >> 26 == 3),
        "divides": sum(1 for w in words if w >> 26 == 0 and w & 0x3F in (0x1A, 0x1B)),  # div, divu: the ring's modulo
        "state_stores": sum(1 for w in words if w >> 26 == 0x29 and w & 0xFFFF == 0x10),
        "waiting": 8 in constant, "no_block": 0xFFFF in constant,  # state 8; the -1 a non-blocking call returns
        "valid_stores": stores(8), "first_stores": stores(0xC), "valid_down": adds(0xFFFF), "valid_up": adds(1),
        "first_loads": loads(0xC), "count_loads": loads(0x10), "msg_loads": loads(0x14),
        "stored": {o for o in (0, 4, 8, 0xC, 0x10, 0x14) if stores(o)},
    }


def _queue_routine(words: list[int], size: int) -> bool:
    """Common to osRecvMesg, osSendMesg and osJamMesg: the blocking ring buffer, six calls (interrupts, yield, pop,
    enqueue, restore and the thread's state), one modulo, a -1 for the non-blocking case, the state set to waiting."""
    f = _queue_features(words)
    return (250 <= size <= 360 and f["calls"] == 6 and f["divides"] == 1 and f["state_stores"] == 1
            and f["waiting"] and f["no_block"] and f["valid_stores"] == 1 and f["msg_loads"] >= 1)


def queue_receive(words: list[int], size: int) -> bool:
    """`osRecvMesg(mq, &msg, flag)`: reads the first message (+0x14 ring), moves `first` on (+0x0C stored) and takes
    one off the count; it reads the ring's size once or twice."""
    f = _queue_features(words)
    return (_queue_routine(words, size) and f["first_stores"] == 1 and f["valid_down"] and f["count_loads"] <= 2
            and f["first_loads"] == 2)


def queue_send(words: list[int], size: int) -> bool:
    """`osSendMesg(mq, msg, flag)`: writes after the last message (`first + validCount`, modulo the size) and adds one
    to the count; `first` is only read."""
    f = _queue_features(words)
    return _queue_routine(words, size) and f["first_stores"] == 0 and not f["valid_down"] and f["valid_up"]


def queue_jam(words: list[int], size: int) -> bool:
    """`osJamMesg(mq, msg, flag)`: writes in front of the first message (`first + size - 1`, modulo the size), so
    it reads the ring's size three or four times, stores `first` and adds one to the count."""
    f = _queue_features(words)
    return (_queue_routine(words, size) and f["first_stores"] == 1 and f["valid_down"] and f["count_loads"] >= 3
            and f["valid_up"])


def event_message(words: list[int], size: int) -> bool:
    """`osSetEventMesg(event, mq, msg)`: indexes the event table by eight bytes an event (`sll r, r, 3`, added to the
    table's address), stores the queue at +0 and the message at +4 of its entry between the interrupt disable and
    restore calls, and, in later builds, sends the pre-NMI event's message when shut down (reading two globals and
    setting a third). It reads no structure through a register: its loads are the stack's spills and those globals,
    and the only stores through another register are the entry's two words (and the global)."""
    calls = sum(1 for w in words if w >> 26 == 3)
    index = any(w >> 26 == 0 and w & 0x3F == 0 and w >> 6 & 31 == 3 and w >> 11 & 31 != 0 for w in words)  # sll r,r,3
    table = any(w >> 26 == 0 and w & 0x3F == 0x21 for w in words)  # addu: the table's base and the event's offset
    entry = [w for w in words if w >> 26 == 0x2B and w >> 21 & 31 != 29]  # sw through something but the stack
    offsets = {w & 0xFFFF for w in entry}
    queue, message = 0 in offsets, 4 in offsets
    structure = [w for w in entry if 8 <= w & 0xFFFF <= 0xFF]  # a field of some other structure
    loads = sum(1 for w in words if w >> 26 == 0x23 and w >> 21 & 31 != 29)  # lw through something but the stack
    narrow = any(w >> 26 in (0x28, 0x29) for w in words)  # sb, sh
    back = any(w >> 26 in (4, 5, 6, 7, 0x14, 0x15) and w & 0x8000 for w in words)  # a branch backwards: a loop
    return (size in _EVENT_SIZES and 2 <= calls <= 3 and index and table and queue and message and not structure
            and loads <= 4 and not narrow and not back)


def queue_creation(words: list[int], size: int) -> bool:
    """`osCreateMesgQueue(mq, msgs, count)`: six stores into the queue and no load or call: both lists empty
    (the thread-list sentinel), the count and first zeroed, the size and the ring's address set."""
    f = _queue_features(words)
    return (32 <= size <= 48 and f["calls"] == 0 and f["stored"] == {0, 4, 8, 0xC, 0x10, 0x14}
            and not any(w >> 26 in (0x23, 0x21, 0x25) for w in words))


def identify(code: bytes, big_endian: bool = True) -> dict | None:
    """{"name", "corpus", "evidence"} for a function whose structure is a libultra routine's, else None."""
    if len(code) % 4 or not _MIN_ANY <= len(code) <= _MAX:
        return None
    words = _words(code, big_endian)
    if len(code) >= _MIN and thread_creation(words):
        return {"name": "osCreateThread", "corpus": CORPUS,
                "evidence": "initialises an OSThread (id, flags, state) between interrupt disable and restore"}
    if thread_yield(words):
        return {"name": "osYieldThread", "corpus": CORPUS,
                "evidence": "marks the running thread runnable and yields between interrupt disable and restore"}
    for check, name, evidence in (
            (thread_start, "osStartThread",
             "makes a thread runnable or waiting and queues it, then yields if it outranks the running one"),
            (thread_priority, "osSetThreadPri",
             "stores a thread's priority, re-queues it and yields if the running thread no longer outranks the queue"),
            (thread_destroy, "osDestroyThread", "unlinks a thread from its queue and from the list of every thread"),
            (queue_receive, "osRecvMesg", "takes the first message off a ring buffer, waiting while it is empty"),
            (queue_send, "osSendMesg", "puts a message after the last of a ring buffer, waiting while it is full"),
            (queue_jam, "osJamMesg", "puts a message in front of the first of a ring buffer, waiting while it is full"),
            (queue_creation, "osCreateMesgQueue", "sets up an empty message queue: lists, count, ring"),
            (event_message, "osSetEventMesg", "stores a queue and a message in the event's two words of the table")):
        if check(words, len(code)):
            return {"name": name, "corpus": CORPUS, "evidence": evidence}
    return None
