"""Which start and end boundary problems the bytes alone settle, so the importer can fix them without review.

`start_boundaries` suggests that a function's first word(s) may not be its own; a person decides. An N64 game had
28 functions whose extent was wrong in a way the bytes prove (agents spent hours on each before anyone looked):
  start  the previous function returned, then a few words of its leftovers (its `jr $ra` delay filler, an
         `addiu $sp,+0x20; addiu $sp,-0x20` pair, a `break 7` from its divide check, a dead load, or a
         `lui`/`addiu` pair that built a callback's address), then the real entry, which other code
         stores as a callback `func + 8` or calls;
  end    a function ends in a loop that never returns (`for (;;)`, `osViBlack(1); for (;;);`), and the function
         after it, which other code calls or points at, was left inside its extent.
`decide` is pure (no compiler, no project): it reads words and relocations, as `start_boundaries.find` does, and runs
in the tests on small instruction lists. It answers per function with a `Fix` whose confidence is
  'auto'     the evidence is strong enough to apply at import and Check again (boundary_repairs.apply_entry_fixes),
             each fix recorded with its evidence and undoable from the review dialog;
  'suggest'  tentative: the review dialog shows it, nothing changes (code that looks like hand-written assembly
             lands here with a warning, since an assembly routine and a fragment look alike).
Nothing is said about a matched function, or when anything enters the words to be cut off.

The start rule. The stray words [S, E) and the real entry E = S + 4k (k <= 3) must all hold:
  - the code before S ends on a complete path: an unconditional transfer (`jr`, `j`, `b`) and its delay slot end
    at S (or one unowned word of alignment before S);
  - nothing enters [S, E): no relocation from any function, no branch or jump from outside the stray words;
  - E has an entry's shape: it opens a frame (`addiu $sp,-N`) that the function's epilogue gives back and never
    opens another, or the function is a frameless leaf from E to its `jr $ra`; the straight line from E reads no
    register a caller leaves undefined;
  - and either other code enters E (a callback `lui`/`addiu` pair, a `jal`, a stored word), or the stray words
    contradict being an entry outright (`contradiction`: they read a temporary nobody set, use a callee-saved
    register's value, give back a frame never taken, open a second frame, trap with `break`, load `$ra`, call out
    before saving `$ra`, jump over the frame, store into the caller's frame, or compute a value
    that is lost before anything reads it: an ALU result is hard evidence, a load soft, as a device register's read
    is a load nobody uses).
Whole pieces of at most three words that hold the same way between two functions are archived as padding.
An empty function (`jr $ra; nop`) other code points at, with other code after it in its extent, is split after its
return; the code after it is judged again next round. A retained tail a reviewed adjustment left for review
(`boundary_tail_review`) is kept when other code points at its first word and it reads as a function from there.

The end rule. Inside a function at E (not its first word), all must hold:
  - the two words before E are an unconditional transfer and its delay slot (the loop that never returns);
  - E opens a frame and other code calls or points at E (a switch case never opens a frame, a function does);
  - no branch or jump crosses E in either direction, so the two parts never run into each other;
  - [E, end) gives its frame back before `jr $ra` or never returns, and opens no second frame.
Each threshold was checked on real games: a matched function's extent is proven by the judge, so a fix proposed for
one is a false positive. Over 30,339 matched functions of 36 N64 games (built with several compilers), read as if
unmatched, none is flagged (41,252 of 93 N64 games with the lost-value, empty-function and retained-tail rules); on
the game above 25 of the 28 are fixed, and the rest but one with those rules (docs/learnings/finding/automatic-boundary-fixes.md)."""
import struct
from dataclasses import dataclass, field

import rabbitizer

MAX_WORDS = 3  # `func + 4`, `+ 8`, `+ 0xC`: the stray runs seen are at most three words
SP, RA, GP = 29, 31, 28
ARGS = frozenset({0, 4, 5, 6, 7, GP, SP, RA})  # what a caller leaves defined
SAVED = frozenset(range(16, 24)) | {30}  # callee-saved: read only to save them
CALLER_SAVED = frozenset({1, 2, 3, 24, 25}) | frozenset(range(8, 16))  # lost across a call (a0-a3 the callee reads)
FP_ARGS = frozenset({12, 13, 14, 15})
FP_SAVED = frozenset(range(20, 32))
POINTER_RELOCS = (2, 4, 5, 6)  # 32-bit word, 26-bit jump, %hi, %lo
JR_RA = 0x03E00008


@dataclass(frozen=True)
class Entry:
    """One function: its place, its words, the addresses its code points at, and whether it may change."""
    name: str
    vram: int
    code: bytes
    relocs: tuple = ()
    status: str = 'open'


@dataclass
class Fix:
    name: str
    vram: int
    kind: str  # 'trim_start' (the function starts at `at`), 'padding' (the whole piece), 'split_end' (a new function
    # at `at`), 'keep' (a retained tail confirmed as an entry), 'join_previous' (the rest of the function at `at`)
    at: int
    confidence: str  # 'auto' | 'suggest'
    evidence: list = field(default_factory=list)  # sentences, strongest first
    warning: str = ''

    def summary(self):
        if self.kind == 'trim_start':
            words = 'the words before it are' if self.at - self.vram > 4 else 'the word before it is'
            return (f'{self.name} starts {self.at - self.vram} bytes late: the real entry is 0x{self.at:08X}; '
                    f'{words} left in the image as padding')
        if self.kind == 'padding':
            return f'{self.name} is leftover words between two functions; it is archived as padding'
        if self.kind == 'join_previous':
            return f'{self.name} is the rest of the function at 0x{self.at:08X}; it is joined to it'
        if self.kind == 'keep':
            return f'{self.name} is a function from its first word; its review is cleared so agents can work on it'
        return f'{self.name} swallowed the next function: a new function starts at 0x{self.at:08X}'


def _words(code, big_endian):
    n = len(code) // 4
    return list(struct.unpack(('>' if big_endian else '<') + f'{n}I', code[:4 * n]))


def _try(call):
    try:
        return call()
    except Exception:  # rabbitizer raises for a register field an instruction doesn't have
        return None


def _sp_step(word):
    """The signed amount of `addiu $sp,$sp,N`, else None."""
    if word >> 16 != 0x27BD:
        return None
    n = word & 0xFFFF
    return n - 0x10000 if n & 0x8000 else n


def _target(word, address):
    """Where a branch or `j`/`jal` goes, else None."""
    op = word >> 26
    if op in (2, 3):
        return ((address + 4) & 0xF0000000) | ((word & 0x3FFFFFF) << 2)
    if op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (word >> 21) & 31 == 8:
        n = word & 0xFFFF
        return address + 4 + (n - 0x10000 if n & 0x8000 else n) * 4
    return None


def _unconditional(word):
    """`jr`, `j` or `b` (beq $zero,$zero): control never runs on into the word after the delay slot."""
    op = word >> 26
    return op == 2 or (op == 0 and word & 63 == 8) or (word >> 16) == 0x1000


def _calls(word):
    return word >> 26 == 3 or (word >> 26 == 0 and word & 63 == 9)


def _regs(ins):
    """(gprs read, gpr written, fprs read, fprs written, reads hi/lo) of one instruction."""
    reads, fread, fwrite = set(), set(), set()
    for test, reg in ((ins.readsRs, 'rs'), (ins.readsRt, 'rt'), (ins.readsRd, 'rd')):
        if _try(test) and (r := _try(lambda reg=reg: getattr(ins, reg).value)) is not None:
            reads.add(r)
    for test, reg, out in ((ins.readsFs, 'fs', fread), (ins.readsFt, 'ft', fread), (ins.readsFd, 'fd', fread),
                           (ins.modifiesFs, 'fs', fwrite), (ins.modifiesFt, 'ft', fwrite), (ins.modifiesFd, 'fd', fwrite)):
        if _try(test) and (r := _try(lambda reg=reg: getattr(ins, reg).value)) is not None:
            out.add(r)
    dest = _try(ins.getDestinationGpr)
    written = dest.value if dest is not None else None
    hilo = bool(_try(ins.readsHI) or _try(ins.readsLO))
    return reads - {0}, written, fread, fwrite, hilo


class Why(str):
    """A reason the words can't start a function. `hard`: no compiled code does this anywhere near an entry. A soft
    one (a register read nobody set) also fits a switch case reached through a table the relocations never saw, so
    it counts only when the real entry opens a frame (a case never does) or other code points at it."""

    def __new__(cls, text, hard=True):
        self = super().__new__(cls, text)
        self.hard = hard
        return self


def _overwritten(words, i, reg):
    """Whether the value words[i] writes to `reg` is lost before anything reads it: a later instruction of the straight
    line sets `reg` again, or (for a register a call doesn't keep) the line calls out first. Compiled code doesn't
    compute a value nobody reads; a word left over from the function before often does."""
    for j in range(i + 1, len(words)):
        reads, written = _regs(rabbitizer.Instruction(words[j]))[:2]
        if reg in reads:
            return False
        if rabbitizer.Instruction(words[j]).hasDelaySlot():
            if _try(rabbitizer.Instruction(words[j]).isBranchLikely):
                return False  # its delay slot runs only when it branches: the value lives on the other path
            if j + 1 < len(words):
                reads, written = _regs(rabbitizer.Instruction(words[j + 1]))[:2]
                if reg in reads:
                    return False
                if written == reg:
                    return True
            return _calls(words[j]) and reg in CALLER_SAVED
        if written == reg:
            return True
    return False


def contradiction(words, base, stop, *, frame_after=False):
    """Why the straight-line code words[0:stop] can't be the start of a compiled function (a `Why`: the first hard
    reason, else the first soft one), or None. `frame_after`: the function's real frame opens at words[stop], so a
    jump over it contradicts. Words after `stop` are read only to see whether a value the stray words compute is used."""
    soft = None
    for why in _reasons(words, base, stop, frame_after):
        if why.hard:
            return why
        soft = soft or why
    return soft


def _reasons(words, base, stop, frame_after):
    defined, fdefined = set(ARGS), set(FP_ARGS)
    saved_ra = framed = False
    for i in range(stop):
        word, at = words[i], base + 4 * i
        ins = rabbitizer.Instruction(word, at)
        name = ins.getOpcodeName()
        reads, written, fread, fwrite, hilo = _regs(ins)
        store = bool(_try(ins.doesStore))
        base_reg = word >> 21 & 31
        data = word >> 16 & 31 if store else None
        if name in ('break', 'syscall') or _try(ins.isTrap):
            yield Why(f'`{name}` traps; a function never begins with one (the previous one\'s divide check left it)')
        step = _sp_step(word)
        if step is not None and step > 0 and not framed:
            yield Why(f'gives back a {step:#x} frame that was never taken')
        if step is not None and step < 0 and framed:
            yield Why('opens a second stack frame; a compiled function opens one')
        if step is not None and step < 0:
            framed = True
        if store and base_reg == SP and not framed and (word & 0xFFFF) >= 0x10 and (word & 0xFFFF) < 0x8000:
            yield Why(f'stores into the caller\'s frame at {word & 0xFFFF:#x}($sp) before opening its own')
        for reg in reads:
            if reg in SAVED and not (store and reg == data and base_reg == SP) and reg not in defined:
                yield Why(f'uses ${NAMES[reg]}, a callee-saved register, before setting it', hard=False)
            if reg not in defined and reg not in SAVED:
                yield Why(f'reads ${NAMES[reg]}, which no caller sets', hard=False)
        for reg in fread:
            if reg in FP_SAVED and not (store and base_reg == SP) and reg not in fdefined:
                yield Why(f'uses $f{reg}, a callee-saved register, before setting it', hard=False)
            if reg not in fdefined and reg not in FP_SAVED:
                yield Why(f'reads $f{reg}, which no caller sets', hard=False)
        if hilo and not any(_try(rabbitizer.Instruction(w).modifiesLO) for w in words[:i]):
            yield Why(f'`{name}` reads a multiply result nothing computed', hard=False)
        if written == RA and not _try(ins.doesLink):
            yield Why('sets $ra before saving it, so it could never return to its caller')
        if store and data == RA and base_reg == SP:
            saved_ra = True
        if _calls(word) and not saved_ra:
            yield Why(f'calls out (`{name}`) before saving $ra, so it could never return to its caller')
        target = _target(word, at)
        if target is not None and _unconditional(word):
            if target == base:
                yield Why('jumps to itself: a loop that never returns, the end of the function before')
            if frame_after and target > base + 4 * stop:
                yield Why('jumps over the frame the function opens, which its epilogue then gives back')
        # A value computed and then lost (an ALU result: hard; a load: soft, since a volatile read of a device
        # register is a load nobody uses). Not in a delay slot, which may be used on the other path only.
        if (written is not None and written not in (0, SP, RA) and not ins.hasDelaySlot()
                and not (i and rabbitizer.Instruction(words[i - 1]).hasDelaySlot()) and _overwritten(words, i, written)):
            yield Why(f'computes ${NAMES[written]} and loses it before anything reads it', hard=not _try(ins.doesLoad))
        if written is not None:
            defined.add(written)
        fdefined |= fwrite


NAMES = ('zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra').split()


def _straight(words, start):
    """The words from `start` up to and including the first transfer's delay slot (the straight line run first)."""
    for i in range(start, len(words)):
        if rabbitizer.Instruction(words[i]).hasDelaySlot():
            return words[start:i + 2]
    return words[start:]


def _closed(words, base, start):
    """The index just past the code that runs from words[start]: after the delay slot of the first return or jump
    that no earlier branch jumps beyond (what follows can't be reached from `start`), else len(words)."""
    reach = start
    for i in range(start, len(words)):
        word = words[i]
        t = _target(word, base + 4 * i)
        if t is not None and not _calls(word) and base <= t < base + 4 * len(words):
            reach = max(reach, (t - base) // 4)
        if _unconditional(word) and reach <= i + 1:
            return min(i + 2, len(words))
    return len(words)


def _frame_shape(words, start, stop=None):
    """Whether words[start:stop] is a framed function: it opens a frame at its first word, gives exactly that frame
    back wherever it pops one, opens no other, and either returns or never returns at all."""
    step = _sp_step(words[start])
    if step is None or step >= 0:
        return False
    rest = words[start + 1:stop]
    if any((s := _sp_step(w)) is not None and s < 0 for w in rest):
        return False
    pops = {s for w in rest if (s := _sp_step(w)) is not None and s > 0}
    returns = JR_RA in rest
    return pops == {-step} if returns else not pops


def _leaf_shape(words, start, stop=None):
    """Whether words[start:stop] is a frameless leaf: no stack moves, not a nop first, ending on `jr $ra` and its slot."""
    body = words[start:stop]
    return (len(body) >= 2 and body[0] != 0 and body[-2] == JR_RA
            and not any(_sp_step(w) is not None for w in body))


def _handwritten(words, base):
    """The first instruction no compiler emits (cache, coprocessor 0, eret...), or None."""
    for i, word in enumerate(words):
        ins = rabbitizer.Instruction(word, base + 4 * i)
        if _try(ins.isLikelyHandwritten):
            return ins.getOpcodeName()
    return None


def decide(entries, *, big_endian=True, word_at=None, stored=frozenset(), reviewed=frozenset()):
    """{function name: Fix} for the unmatched functions whose start or end the bytes settle (or suggest).
    `reviewed`: the names of tails a reviewed boundary adjustment kept for review (`boundary_tail_review`); one whose
    start other code points at and that reads as a function from there is kept ('keep'), so agents may claim it.
    `word_at(address)`: the image's word there, or None, for the code before a function no live entry covers.
    `stored`: code addresses some aligned word of the image holds (function tables, a switch's jump table): an
    address there is entered, whatever the relocations say. Asked only of the few words a fix would cut off, so it
    may search the image lazily."""
    entries = sorted((e for e in entries if len(e.code) >= 4), key=lambda e: e.vram)
    pointed = {}  # address -> {(function name, address of the instruction pointing there)}
    for e in entries:
        for offset, kind, address in e.relocs:
            if kind in POINTER_RELOCS:
                pointed.setdefault(address, set()).add((e.name, e.vram + offset))
    owner = {}  # address -> the entry whose extent covers it
    shared = set()  # addresses several entries cover (overlays loaded at one address): a pointer there is anyone's
    flow = {}  # address -> addresses of the branches and jumps going there
    starts = {e.vram for e in entries}
    for e in entries:
        for i, w in enumerate(_words(e.code, big_endian)):
            if e.vram + 4 * i in owner:
                shared.add(e.vram + 4 * i)
            owner[e.vram + 4 * i] = e
            if (t := _target(w, e.vram + 4 * i)) is not None and not _calls(w):
                flow.setdefault(t, set()).add(e.vram + 4 * i)

    def word(address):
        e = owner.get(address)
        if e is not None:
            return _words(e.code[address - e.vram:address - e.vram + 4], big_endian)[0]
        return word_at(address) if word_at else None

    def returned_before(address):
        """Whether code before `address` ends on a complete path (a transfer and its delay slot)."""
        for gap in (0, 4):
            if gap and address - 4 in owner:
                break
            w = word(address - 8 - gap)
            if w is not None and _unconditional(w):
                return True
        return False

    def entered(lo, hi, sources):
        """Whether anything outside `sources` enters [lo, hi)."""
        for address in range(lo, hi, 4):
            if {at for _, at in pointed.get(address, ())} - sources or flow.get(address, set()) - sources:
                return True
        return False

    found = {}
    for e in entries:
        if e.status == 'matched':
            continue
        words = _words(e.code, big_endian)
        if any(a in shared for a in range(e.vram, e.vram + 4 * len(words), 4)):
            continue  # another overlay shares these addresses: pointers and flow can't be told apart
        fix = _start(e, words, pointed, entered, returned_before, starts, word)
        cut = (fix.at if fix.kind == 'trim_start' else e.vram + 4 * len(words)) if fix is not None else e.vram
        if any(address in stored for address in range(e.vram, cut, 4)):
            continue  # a word of data points into the words to cut off (a table): they may be entered after all
        if fix is None:
            fix = _end(e, words, pointed, flow) or _returned_early(e, words, pointed, flow)
        if fix is None and e.name in reviewed:
            fix = _entry(e, words, pointed)
        if fix is not None:
            found[e.name] = fix
    for before, after in zip(entries, entries[1:]):
        if before.name not in found and after.name not in found and (fix := _runs_on(before, after, pointed, shared, big_endian)):
            found[after.name] = fix
    return found


def _runs_on(before, after, pointed, shared, big_endian):
    """`after` is the rest of `before`: `before` opens a frame and branches into `after`, which nothing else enters,
    opens no frame of its own and gives `before`'s frame back (an N64 game cut one function where its first part
    ended in a jump, though that part's own branch went to the next word). Compiled code never branches into another
    function (a `j` may, as a tail call: only branches count). Fix.at is `before`'s start: the joined function's."""
    if 'matched' in (before.status, after.status) or before.vram + len(before.code) != after.vram:
        return None
    first, rest = _words(before.code, big_endian), _words(after.code, big_endian)
    lo, hi = after.vram, after.vram + 4 * len(rest)
    if any(a in shared for a in range(before.vram, hi, 4)):
        return None
    into = sorted({before.vram + 4 * i for i, w in enumerate(first) if w >> 26 != 2 and not _calls(w)
                   and (t := _target(w, before.vram + 4 * i)) is not None and lo <= t < hi})
    if not into or _callers(after, after.vram, pointed) or _sp_step(rest[0]) is not None:
        return None
    words = first + rest
    if not _frame_shape(words, 0) or any(_sp_step(w) is not None and _sp_step(w) > 0 for w in first):
        return None  # `before` gives its frame back only in `after`: one function
    if any(words[i] & 0xFFFF8000 == 0x27BD8000 and words[i - 2] == JR_RA for i in range(2, len(words))):
        return None  # a return, then a new frame: another function starts in there
    asm = _handwritten(words, before.vram)
    return Fix(after.name, after.vram, 'join_previous', before.vram, 'suggest' if asm else 'auto', [
        f'{before.name} branches into {after.name} (from 0x{into[0]:08X}); compiled code never branches into another '
        'function, and nothing else points at it.',
        f'{before.name} opens a frame that only {after.name} gives back, and {after.name} opens none of its own.'],
        f'`{asm}` is an instruction no compiler emits: this may be hand-written assembly, so it is left for review.' if asm else '')


def _start(e, words, pointed, entered, returned_before, starts, owner_words):
    S = e.vram
    if not returned_before(S):
        return None
    for k in range(1, min(MAX_WORDS, len(words)) + 1):
        E = S + 4 * k
        # A complete backward loop can be a standalone no-return routine (including SDK panic stubs), even when
        # nobody in the discovered code calls it. A preceding return does not make that routine padding.
        if k >= 2 and _unconditional(words[k - 2]):
            target = _target(words[k - 2], E - 8)
            if target is not None and S <= target < E - 4:
                return None  # _end may still split the following independently referenced entry, preserving the loop
        stray = set(range(S, E, 4))
        if entered(S, E, stray):
            return None  # something enters the words that would be cut off: they are this function's own
        why = contradiction(words, S, k, frame_after=k < len(words) and _sp_step(words[k]) is not None)
        if k == len(words):
            # The whole piece is leftovers between two functions: the next one must be an entry (it opens a frame
            # or other code enters it), and a lone word must sit where alignment puts filler (4 past 8, before an
            # 8-aligned entry; boundary_repairs' alignment rule decides the rest).
            nxt = owner_words(E)
            entry_after = nxt is not None and (_sp_step(nxt) is not None and _sp_step(nxt) < 0
                                               or {name for name, _ in pointed.get(E, ())} - {e.name})
            if why and E in starts and entry_after and (k > 1 or S % 8 == 4):
                return Fix(e.name, S, 'padding', S, 'auto', [
                    f'The code before 0x{S:08X} returns, so nothing runs on into it, and nothing points at it.',
                    f'Read as a function it {why}.', f'The next function starts right after it, at 0x{E:08X}.'])
            return None
        # A word of data equal to E is no evidence: on 36 N64 games such coincidences put 7 matched functions' real
        # starts in doubt. Only code that builds or calls the address counts (stored words only ever block a fix).
        callers = sorted({name for name, _ in pointed.get(E, ())} - {e.name})
        if rabbitizer.Instruction(words[k - 1]).hasDelaySlot() and not callers:
            continue  # E is the delay slot of the word before: only a pointer says it is an entry too
        if any({name for name, _ in pointed.get(S + 4 * j, ())} - {e.name} for j in range(k + 1, MAX_WORDS + 1)):
            continue  # other code enters further in: that is the entry, not this one
        stop = _closed(words, S, k)
        framed = _frame_shape(words, k, stop)
        leaf = not framed and _leaf_shape(words, k, stop)
        if not (framed or leaf):
            continue
        line = _straight(words, k)
        if contradiction(line, E, len(line)) is not None:
            continue  # the code from E doesn't read like an entry either
        if not (callers or why and (why.hard or framed)):
            continue
        if not callers and leaf and stop - k <= 2:
            continue  # a bare `jr $ra` nothing calls may be the end of the code before: no evidence of an entry
        evidence = []
        if callers:
            evidence.append(f'{", ".join(callers[:3])}{" and others" if len(callers) > 3 else ""} '
                            f'point{"s" if len(callers) == 1 else ""} at 0x{E:08X} ({e.name} + {4 * k:#x}); '
                            f'nothing points at {e.name} itself or the words before 0x{E:08X}.')
        if why:
            evidence.append(f'Read as the start of a function, the word{"s" if k > 1 else ""} at 0x{S:08X} {why}.')
        evidence.append('The code before it returns, so nothing runs on into these words.')
        evidence.append(f'0x{E:08X} ' + ('opens the frame the function\'s epilogue gives back.' if framed else
                                         'begins a frameless leaf that ends on its return.'))
        confidence = 'auto'
        warning = ''
        if asm := _handwritten(words, S):
            confidence = 'suggest'
            warning = (f'`{asm}` is an instruction no compiler emits: this may be hand-written assembly rather than '
                       'a fragment of compiled code, so it is left for review.')
        return Fix(e.name, S, 'trim_start', E, confidence, evidence, warning)
    return None


def _callers(e, address, pointed):
    return sorted({name for name, _ in pointed.get(address, ())} - {e.name})


def _entry(e, words, pointed):
    """A retained tail confirmed as an entry: other code points at its first word, it opens the frame its epilogue
    gives back (or is a frameless leaf), and its straight line reads nothing a caller leaves undefined."""
    callers = _callers(e, e.vram, pointed)
    stop = _closed(words, e.vram, 0)
    shape = _frame_shape(words, 0, stop) or _leaf_shape(words, 0, stop)
    line = _straight(words, 0)
    if not callers or not shape or contradiction(line, e.vram, len(line)) is not None:
        return None
    return Fix(e.name, e.vram, 'keep', e.vram, 'auto', [
        f'{", ".join(callers[:3])}{" and others" if len(callers) > 3 else ""} point{"s" if len(callers) == 1 else ""} '
        f'at 0x{e.vram:08X}, the first word of this retained tail.',
        f'0x{e.vram:08X} ' + ('opens the frame the function\'s epilogue gives back.' if _sp_step(words[0]) is not None
                             else 'begins a frameless leaf that ends on its return.')])


def _returned_early(e, words, pointed, flow):
    """An empty function (`jr $ra; nop`) other code calls, with the words of other code after it inside its extent:
    nothing after the return can run as part of it, so they are split off (an N64 game had one; their own start is
    judged again next round). Only this shape: a function returning further in may be followed by an unreachable
    `jr $ra` its compiler left, or by data (110 functions of 93 N64 games would have been split that way)."""
    S = e.vram
    if not _callers(e, S, pointed):
        return None
    stop = 2
    if len(words) < stop + 3 or words[0] != JR_RA or words[stop] in (0, JR_RA) or not any(_unconditional(w) for w in words[stop:-1]):
        return None  # zeros, another bare return or something that never ends: alignment or data, not a function
    C = S + 4 * stop
    if any(src >= C for t in range(S, C, 4) for src in flow.get(t, ())):
        return None  # the code after the return branches back into it: they are one piece of code
    asm = _handwritten(words, S)
    return Fix(e.name, S, 'split_end', C, 'suggest' if asm else 'auto', [
        f'{", ".join(_callers(e, S, pointed)[:3])} point at 0x{S:08X}, and its code returns at 0x{C - 8:08X} '
        'with no branch or jump table reaching beyond.',
        f'Nothing from before 0x{C:08X} runs into the code after it, so that code is not part of {e.name}.'],
        f'`{asm}` is an instruction no compiler emits: this may be hand-written assembly, so it is left for review.'
        if asm else '')


def _end(e, words, pointed, flow):
    S, end = e.vram, e.vram + 4 * len(words)
    for i in range(2, len(words) - 1):
        step = _sp_step(words[i])
        if step is None or step >= 0 or not _unconditional(words[i - 2]):
            continue
        E = S + 4 * i
        callers = sorted({name for name, _ in pointed.get(E, ())} - {e.name})
        if not callers:
            continue
        crosses = False
        for j, w in enumerate(words):
            t = _target(w, S + 4 * j)
            if t is not None and not _calls(w) and S <= t < end and (S + 4 * j < E) != (t < E):
                crosses = True
                break
        head = _frame_shape(words, 0, i) if _sp_step(words[0]) is not None else not any(
            _sp_step(w) is not None for w in words[:i])
        if crosses or not head or not _frame_shape(words, i, _closed(words, S, i)):
            continue
        back = _target(words[i - 2], E - 8)
        loop = 'a loop that never returns' if back is not None and back < E else 'a jump away'
        evidence = [f'{", ".join(callers[:3])}{" and others" if len(callers) > 3 else ""} '
                    f'call{"s" if len(callers) == 1 else ""} or point{"s" if len(callers) == 1 else ""} at '
                    f'0x{E:08X}, inside {e.name}.',
                    f'The code before it ends in {loop} (0x{E - 8:08X}), so it never runs on into 0x{E:08X}.',
                    f'0x{E:08X} opens a frame of its own, which its code gives back; no branch crosses between the two.']
        confidence, warning = 'auto', ''
        if asm := _handwritten(words, S):
            confidence = 'suggest'
            warning = (f'`{asm}` is an instruction no compiler emits: this may be hand-written assembly rather than '
                       'two compiled functions, so it is left for review.')
        return Fix(e.name, S, 'split_end', E, confidence, evidence, warning)
    return None
