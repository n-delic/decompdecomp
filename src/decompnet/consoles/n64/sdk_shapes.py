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
"""

CORPUS = "N64 library routines by structure"
_MIN, _MAX = 100, 700
_MIN_ANY = 32  # (the smallest routine here, osYieldThread: 60 to 80 bytes)


def _words(code: bytes, big_endian: bool) -> list[int]:
    order = "big" if big_endian else "little"
    return [int.from_bytes(code[i:i + 4], order) for i in range(0, len(code) - 3, 4)]


def _is(word: int, op: int, rt: int | None, imm: int) -> bool:
    return word >> 26 == op and word & 0xFFFF == imm and (rt is None or word >> 16 & 31 == rt)


def thread_creation(words: list[int]) -> bool:
    """`osCreateThread(thread, id, entry, arg, sp, pri)`: id into thread+0x14, flags cleared at +0x12, state (a
    halfword) set at +0x10, interrupts disabled and restored around linking the thread into the active queue."""
    ids = any(_is(w, 0x2B, 5, 0x14) for w in words)  # sw $a1, 0x14(thread)
    flags = any(_is(w, 0x29, 0, 0x12) for w in words)  # sh $zero, 0x12(thread)
    state = any(_is(w, 0x29, None, 0x10) and w >> 16 & 31 != 0 for w in words)  # sh <non-zero>, 0x10(thread)
    calls = sum(1 for w in words if w >> 26 == 3)  # jal: the interrupt disable and restore routines
    return ids and flags and state and calls >= 2


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
    return None
