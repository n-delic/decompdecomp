"""A function whose first word is alignment padding or a dead word, found from its bytes and from who points into it.

Boundary review so far looked at where a function ENDS (a missing delay slot, code that runs on, padding after it).
The start has the same trouble: the assembler fills the gap before an 8-aligned entry with whatever bytes it had
left over, and a disassembler that doesn't know better folds that word into the function that follows. An N64 game
had a dozen of them. Agents then spent hours on C that can never match: the correct source scored 100 with only the
leading word different, a callback pointer into `func + 0x4` pointed at the real entry all along, and the
epilogue gave back a frame the first word never took.

Nothing here changes a boundary. `find` reads the stored layout and says, for a function, "the first word(s) are
probably not its own, the entry is probably at +4", with the evidence. The review dialog shows that as a suggestion
beside agents' (`boundary_investigations.suggestions`), the brief and `get_function` mention it, and a person
decides. It is pure (no compiler, no project): the same function runs in the tests on fake instruction lists.

Evidence, strongest first (each is a sentence in the suggestion, so the reviewer can check it):
  callback  other code points at `func + 4k` (a `lui`/`addiu` pair or a `jal`) and nothing points at `func` itself:
            whoever wrote that code entered the function there;
  layout    the function starts 4 bytes past an 8-byte boundary in a game whose functions nearly all start on one,
            so `func + 4` is the aligned entry, and the function before it ends exactly here;
  dead      the skipped word does nothing for the function: a register it sets is set again before it is read, an
            `addiu $sp` whose frame the epilogue doesn't give back, a stack reload in a function that never made
            that frame, a branch whose delay slot is the real first instruction.
A function is suggested when its callback evidence stands alone (tentative), or layout and dead evidence agree;
callback evidence plus either of the others is strong. Nothing is suggested when anything enters at `func` itself
or branches back to it: that word is then the function's own."""
import json
import struct
import time
from dataclasses import dataclass, field

import rabbitizer

MAX_WORDS = 3  # `func + 4`, `+ 8`, `+ 0xC`: the callbacks seen point at most three words in
ALIGN = 8  # the SN assembler's function alignment
ALIGNED_SHARE = 0.85  # how many of a game's functions must start on one before off-alignment means something
SP, RA, ARG_REGS = 29, 31, (4, 5, 6, 7)
NAMES = ('zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra').split()
CALLER_SAVED = frozenset(range(1, 16)) | {24, 25}  # a call may overwrite these
POINTER_RELOCS = (2, 4, 5, 6)  # 32-bit word, 26-bit jump, %hi, %lo
KIND = 'trim_start'
AGENT = 'decompdecomp start check'


@dataclass(frozen=True)
class Entry:
    """What `find` reads of one function: its place, its words and the addresses its code points at."""
    name: str
    vram: int
    code: bytes
    relocs: tuple = ()
    status: str = 'open'


@dataclass
class Finding:
    name: str
    vram: int
    skip: int  # words to cut off the start
    confidence: str  # 'strong' | 'tentative'
    signals: list = field(default_factory=list)  # 'callback', 'layout', 'dead'
    evidence: list = field(default_factory=list)  # [{'address', 'text'}], like boundary_evidence's

    @property
    def start(self):
        return self.vram + 4 * self.skip

    def summary(self):
        words = 'word' if self.skip == 1 else f'{self.skip} words'
        return (f'possible start boundary problem: the first {words} (0x{self.vram:08X}) may be alignment padding or '
                f'a dead word; suggested start +{4 * self.skip} (0x{self.start:08X})')


def _read(code, big_endian):
    count = len(code) // 4
    return list(struct.unpack(('>' if big_endian else '<') + f'{count}I', code[:4 * count]))


def _try(call):
    try:
        return call()
    except Exception:  # rabbitizer raises for a register field an instruction doesn't have
        return None


def _facts(word, address):
    """(reads, writes, transfers control, links, stores, loads) of one instruction, general registers only."""
    ins = rabbitizer.Instruction(word, address)
    reads = set()
    if ins.readsRs() and (r := _try(lambda: ins.rs.value)) is not None:
        reads.add(r)
    if ins.readsRt() and (r := _try(lambda: ins.rt.value)) is not None:
        reads.add(r)
    if _try(ins.readsRd) and (r := _try(lambda: ins.rd.value)) is not None:
        reads.add(r)
    dest = _try(ins.getDestinationGpr)
    writes = {dest.value} if dest is not None and dest.value else set()
    link = bool(_try(ins.doesLink))
    if link:
        writes.add(RA)
    return reads - {0}, writes, ins.hasDelaySlot(), link, bool(_try(ins.doesStore)), bool(_try(ins.doesLoad)), ins


def _sp_step(word):
    """The signed amount of `addiu $sp,$sp,N`, else None."""
    if word >> 16 != 0x27BD:
        return None
    n = word & 0xFFFF
    return n - 0x10000 if n & 0x8000 else n


def _branch_target(word, address):
    op = word >> 26
    if op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (word >> 21) & 31 == 8:
        n = word & 0xFFFF
        return address + 4 + (n - 0x10000 if n & 0x8000 else n) * 4
    return None


def _first_access(words, start, base, reg):
    """Whether `reg` is next read ('read'), written ('write') or neither before the code first branches, in the
    straight line from words[start]. A call reads the argument registers (it may take them) and clobbers the rest."""
    for i in range(start, len(words)):
        reads, writes, transfer, link, _, _, _ = _facts(words[i], base + 4 * i)
        if reg in reads:
            return 'read'
        if link and reg in ARG_REGS:
            return 'read'
        if reg in writes or (link and reg in CALLER_SAVED):
            return 'write'
        if transfer:
            if i + 1 < len(words):  # its delay slot still runs before control leaves
                reads, writes, *_ = _facts(words[i + 1], base + 4 * (i + 1))
                if reg in reads:
                    return 'read'
                if reg in writes:
                    return 'write'
            return None
    return None


def _dead_word(words, i, k, base):
    """Why words[i] (one of the first k) does nothing for the function that follows, or None when it may."""
    word = words[i]
    reads, writes, transfer, link, store, load, ins = _facts(word, base + 4 * i)
    rest = words[k:]
    if transfer:
        if i + 1 == k and not link:
            return f'a {ins.getOpcodeName()} whose delay slot is the real first instruction'
        return None
    if store:
        return None
    step = _sp_step(word)
    if step is not None and step < 0:
        frame = -step
        frees = {s for w in rest if (s := _sp_step(w)) is not None and s > 0}
        if frees and frame not in frees:
            return (f'allocates a {frame:#x} frame the function never gives back (its epilogue restores '
                    + ', '.join(f'{s:#x}' for s in sorted(frees)) + ')')
        return None
    if load and word >> 21 & 31 == SP:
        offset = word & 0xFFFF
        reg = word >> 16 & 31
        made = any(s is not None and s < 0 for s in map(_sp_step, rest))
        stored = any(_facts(w, 0)[4] and w >> 21 & 31 == SP and w & 0xFFFF == offset for w in rest)
        if not made and not stored:
            return f'reloads ${NAMES[reg]} from the stack of a function that never made a frame'
        return None
    if writes and not load:
        reg = next(iter(writes))
        if _first_access(words, i + 1, base, reg) == 'write':
            return f'sets ${NAMES[reg]}, which is set again before anything reads it'
    return None


def find(entries, *, big_endian=True):
    """{function name: Finding} for every unmatched entry whose first word(s) look like padding or dead code."""
    entries = sorted((e for e in entries if len(e.code) >= 4), key=lambda e: e.vram)
    if not entries:
        return {}
    aligned = sum(e.vram % ALIGN == 0 for e in entries) / len(entries) >= ALIGNED_SHARE
    pointed = {}  # address -> names of the other functions whose code points there
    for e in entries:
        for _, kind, address in e.relocs:
            if kind in POINTER_RELOCS:
                pointed.setdefault(address, set()).add(e.name)
    starts = {e.vram for e in entries}
    ends = {}
    for e in entries:
        ends.setdefault(e.vram + len(e.code), e)
    found = {}
    for e in entries:
        words = _read(e.code, big_endian)
        if e.status == 'matched' or len(words) < MAX_WORDS + 1:
            continue
        entered = pointed.get(e.vram, set()) - {e.name}
        looped = any(_branch_target(w, e.vram + 4 * i) == e.vram for i, w in enumerate(words))
        if entered or looped:
            continue  # something enters at the first word: it is the function's own
        hits = [k for k in range(1, MAX_WORDS + 1) if pointed.get(e.vram + 4 * k, set()) - {e.name}]
        if hits:
            k = hits[0]
        elif aligned and e.vram % ALIGN:
            k = (ALIGN - e.vram % ALIGN) // 4
        else:
            k = 1  # no pointer to tell: the frame check below decides, for the first word only
        if e.vram + 4 * k in starts or (len(words) - k) < 2:
            continue
        callback = bool(hits)
        layout = aligned and e.vram % ALIGN != 0 and (e.vram + 4 * k) % ALIGN == 0
        reasons = [_dead_word(words, i, k, e.vram) for i in range(k)]
        dead = all(reasons)
        # A frame the first word makes and the epilogue doesn't give back, or a reload from a frame nothing made, is
        # a function that contradicts itself: the word is not its own whatever the layout says.
        framed = not hits and reasons[0] is not None and reasons[0].startswith(('allocates', 'reloads'))
        if not (callback or (layout and dead) or framed):
            continue
        evidence = []
        if callback:
            who = sorted(pointed[e.vram + 4 * k] - {e.name})
            evidence.append({'address': e.vram + 4 * k, 'text': (
                f'{", ".join(who[:3])}{" and others" if len(who) > 3 else ""} point{"s" if len(who) == 1 else ""} at '
                f'{e.name} + {4 * k:#x} (0x{e.vram + 4 * k:08X}); nothing points at {e.name} itself, so that is its entry.')})
        if layout:
            before = ends.get(e.vram)
            evidence.append({'address': e.vram, 'text': (
                f'The function starts 4 bytes past an 8-byte boundary; {e.vram + 4 * k:#010x} is aligned like '
                f'{int(ALIGNED_SHARE * 100)}% or more of this game\'s functions'
                + (f', and {before.name} ends exactly at {e.name}.' if before else '.'))})
        for i, why in enumerate(reasons):
            if why:
                evidence.append({'address': e.vram + 4 * i, 'text': f'The word at 0x{e.vram + 4 * i:08X} {why}.'})
        before = ends.get(e.vram)
        if before is not None and (repeated := [i for i in range(k) if words[i] in _read(before.code, big_endian)]):
            evidence.append({'address': e.vram + 4 * repeated[0],
                             'text': f'That word also appears in the preceding function {before.name}.'})
        signals = [s for s, on in (('callback', callback), ('layout', layout), ('dead', dead or framed)) if on]
        found[e.name] = Finding(e.name, e.vram, k, 'strong' if callback and len(signals) > 1 else 'tentative',
                                signals, evidence)
    return found


# ---- the project: stored functions in, findings out ----

def entries(net, proj):
    """The project's current functions (archived ones left out) as `Entry` values."""
    from .metadata_rows import rows
    fields = ('name', 'vram', 'target', 'relocs', 'status', 'gone_mark')
    query = 'SELECT name,vram,CASE WHEN target IS NULL THEN NULL ELSE hex(target) END AS target,relocs,status,gone_mark'
    out = []
    for row in rows(net.project_store(proj['id']), fields, query + ' FROM functions WHERE project_id=?', proj['id']):
        if row['gone_mark'] is None and row['vram'] is not None and row['target']:
            out.append(Entry(row['name'], row['vram'], bytes.fromhex(row['target']),
                             tuple(tuple(r) for r in json.loads(row['relocs'] or '[]')), row['status']))
    return out


def findings(net, proj):
    """{name: Finding} for the project, kept until its functions change (the audit and every brief ask)."""
    if proj['arch'] != 'mips':
        return {}
    from .code_coverage import _view
    return _view(net, proj, 'start-boundaries', lambda: find(entries(net, proj), big_endian=bool(proj['big_endian'])))


def brief(net, proj, fn):
    """The line agents read when the function they're about to work on probably starts too early, or None."""
    if fn['status'] == 'matched':
        return None
    found = findings(net, proj).get(fn['name'])
    if found is None:
        return None
    return (found.summary().capitalize() + '. ' + ' '.join(e['text'] for e in found.evidence[:3])
            + '\nNo C can match this extent exactly, so tuning it is wasted work. Do not compile or search against it: '
              'release it and say so in your notes. The first word is not changed for you; a person reviews the suggestion '
              'in Review function boundaries.')


def suggestion(found, packet):
    """The record `suggest_boundary` would save for this finding (boundary_investigations.report), never stored:
    it is read again from the bytes each time, so it can't go stale."""
    cites = [e['address'] for e in found.evidence]
    return {'function': found.name, 'action': KIND, 'end': None, 'start': found.start, 'confidence': found.confidence,
            'supporting': found.summary() + '. ' + ' '.join(e['text'] for e in found.evidence),
            'counter': 'Every check here reads the stored bytes: an entry reached through a table or a pointer the '
                       'disassembly never saw would not show up, and a function can legitimately have a second entry point.',
            'uncertainty': 'Applying it means cutting the first word(s) off with split_function and setting them aside as '
                           'padding; nothing was changed. Confirm the entry in the assembly first.',
            'citations': cites, 'agent': AGENT, 'model': '', 'time': time.time(),
            'revision': packet['revision'], 'image': packet['image'], 'automatic': True}


def audit_rows(net, proj):
    """Rows for the review dialog's list of entries to inspect: suggestions, not blocks (agents may still claim)."""
    return [{'function': f.name, 'suggested_action': KIND, 'start': f.start, 'confidence': f.confidence,
             'note': f.summary(), 'override': True}
            for f in findings(net, proj).values()]
