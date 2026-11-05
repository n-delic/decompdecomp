"""Free matches from identical functions: code the game has more than once.

Games are full of functions compiled from the same source: Mario Party 6's 80 minigame modules each link the
same helpers, libraries are inlined into many files, and an SDK's functions are identical from one game to the
next. Two functions are twins when their machine code is the same once the addresses their relocations fill in
are masked (and the relocations are of the same kinds at the same places). A quarter of Mario Party 6's
unmatched functions have a matched twin.

When one twin matches, its code is tried for every other twin, with the names it refers to swapped for the ones
the other twin's relocations name (function and globals by name: `fn_1_3F7C` becomes `fn_1_A2C0`), and the
judge decides, as for any attempt: a twin that only looks the same (other constants, other types) doesn't match.
This runs as the first part of autopilot, and again for a function's twins whenever it matches. No agent time,
no tokens.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path

from .. import arch, similarity
from ..elfobj import MASKS_BY_ARCH, Reloc, mask_relocs
from ..network import NetworkError
from ..project import compiler_for

log = logging.getLogger("decompdecomp")
IDENT = re.compile(r"^[A-Za-z_]\w*$")
_MACHINE = {"EM_PPC": ("ppc", "big"), "EM_PPC64": ("ppc64", "big"), "EM_MIPS": ("mips", None), "EM_ARM": ("arm", "little")}
_CACHE_VERSION = 6  # of twins.json: 2 added why code is hand-written assembly, 3 ARM's BIOS call stubs, 4 ARM's
# system instructions, 5 COFF objects (PC) and the C names of their symbols, 6 MIPS NOP-only targets
# A twin's code that didn't match isn't tried again for this long while neither function's code changes (the
# same source gives the same result): every agent start's pass would otherwise redo hundreds of compiles (Mario
# Party 6: about 900, 15 minutes beside the agents). After a while it's tried again: a header can have changed.
MISS_TTL = 24 * 3600
# Across games, functions this small are the same everywhere (a return, a stub): they match on their own anyway
ACROSS_MIN_BYTES = 32
FORK_PRINTS_TTL = 10 * 60  # a fork's target objects are looked at again after this long (they can be rebuilt)
WHOLE_WORD = (0, 4, 0xFFFFFFFF)  # a relocation of a type MASKS_BY_ARCH doesn't know: its whole word is masked
LABELS_BACK, LABEL_REACH = 64, 0x10000  # rom_renames looks this many labels down, at most this many bytes below
SQL_CHUNK = 500  # ids per IN (...): under SQLite's limit of variables in one statement
WHY_CHARS = 300  # how much of a compiler's message a miss keeps
UNDECLARED_RETRIES = 4  # names a fork twin's file declares later, declared and built again, at most
_GENERATED = ("__dt__", "__ct__", "__sinit", "__vt__")  # C++ code the compiler writes: nothing to copy


@dataclass
class Print:
    """A function's fingerprint: masked code + relocation layout, and what each relocation refers to."""
    key: str
    refs: list[str]  # the name each relocation refers to, in order (sections as ".data" etc.)
    size: int
    addrs: list[int] = field(default_factory=list)  # ROM projects: the address each relocation fills in
    asm: str = ""  # why no compiler makes this code (asm_reason: consoles/, arch/), or ""
    name: str = ""  # how C writes the function's own name, where its symbol isn't that (Visual C++'s _f@8: f)


def _mask(code: bytearray, relocs: list[tuple[int, int]], arch: str, order: str) -> None:
    for off, rtype in relocs:
        delta, length, mask = MASKS_BY_ARCH.get((arch, rtype), MASKS_BY_ARCH.get(("thumb", rtype), WHOLE_WORD))
        at = off + delta
        if at < 0 or at + length > len(code):
            continue
        val = int.from_bytes(code[at:at + length], order)
        code[at:at + length] = (val & ~mask & ((1 << (8 * length)) - 1)).to_bytes(length, order)


def _key(code: bytes, layout: list[tuple[int, int]]) -> str:
    digest = hashlib.sha1(code)
    digest.update(repr(layout).encode())
    return digest.hexdigest()


def coff_prints(data: bytes) -> dict[str, Print]:
    """object_prints for a COFF object (a PC project's targets: a section per function), its names as C writes them
    (coffobj.c_name: `_func_0041B470@8` is func_0041B470), so a twin's code is renamed where its C says them; a jump
    table's own labels by their place in the function, the same in every twin."""
    from .. import coffobj

    out: dict[str, Print] = {}
    for owner, raw, relocs in coffobj.sections(data):
        if not owner:
            continue
        code = bytearray(raw)
        _mask(code, [(off, kind) for off, kind, *_ in relocs], "x86", "little")
        refs = [f"$L+{value:X}" if section else coffobj.c_name(name) for _, _, name, section, value in relocs]
        out[owner] = Print(_key(bytes(code), [(off, kind) for off, kind, *_ in relocs]), refs, len(raw),
                           name=coffobj.c_name(owner))
    return out


def object_prints(data: bytes) -> dict[str, Print]:
    """Fingerprints of every function an object file defines, by symbol, with whether its code is hand-written
    assembly where its processor tells that without its console's facts (PowerPC: arch/)."""
    from elftools.elf.elffile import ELFFile
    from elftools.elf.relocation import RelocationSection

    from .. import coffobj

    if coffobj.is_coff(data):
        return coff_prints(data)

    elf = ELFFile(BytesIO(data))
    arch_id, order = _MACHINE.get(elf["e_machine"], ("", None))
    order = order or ("little" if elf.little_endian else "big")
    symtab = elf.get_section_by_name(".symtab")
    if symtab is None or not arch_id:
        return {}
    processor = arch.get(arch_id)
    reason = processor.asm_reason if not processor.asm_needs_console else processor.padding_reason
    by_section: dict[int, list] = defaultdict(list)
    modes: dict[int, list[tuple[int, bool]]] = defaultdict(list)  # ARM: section -> (address, Thumb?) of $a/$t
    for s in symtab.iter_symbols():
        if s["st_info"]["type"] == "STT_FUNC" and isinstance(s["st_shndx"], int) and s["st_size"] > 0 and s.name:
            by_section[s["st_shndx"]].append(s)
        elif arch_id == "arm" and s.name.split(".")[0] in ("$a", "$t") and isinstance(s["st_shndx"], int):
            modes[s["st_shndx"]].append((s["st_value"], s.name.startswith("$t")))
    out: dict[str, Print] = {}
    for shndx, syms in by_section.items():
        sec = elf.get_section(shndx)
        if sec["sh_type"] == "SHT_NOBITS":
            continue
        raw = sec.data()
        code = bytearray(raw)
        rels: list[tuple[int, int, str]] = []
        for rs in elf.iter_sections():
            if not isinstance(rs, RelocationSection) or rs["sh_info"] != shndx:
                continue
            table = elf.get_section(rs["sh_link"])
            for r in rs.iter_relocations():
                sym = table.get_symbol(r["r_info_sym"])
                name = elf.get_section(sym["st_shndx"]).name if sym["st_info"]["type"] == "STT_SECTION" and \
                    isinstance(sym["st_shndx"], int) else sym.name
                rels.append((r["r_offset"], r["r_info_type"], name))
        rels.sort()
        _mask(code, [(o, t) for o, t, _ in rels], arch_id, order)
        section_modes = sorted(modes[shndx])
        for s in syms:
            lo = s["st_value"] & ~1 if arch_id == "arm" else s["st_value"]  # (bit 0: a Thumb function)
            mine = [(o - lo, t, n) for o, t, n in rels if lo <= o < lo + s["st_size"]]
            facts = {}
            if arch_id == "arm":  # its mode: bit 0, else the mapping symbol it starts under (dsd's objects)
                at = [t for a, t in section_modes if a <= lo]
                facts["thumb"] = True if s["st_value"] & 1 else (at[-1] if at else None)
            out[s.name] = Print(_key(bytes(code[lo:lo + s["st_size"]]), [(o, t) for o, t, _ in mine]),
                                [n for _, _, n in mine], s["st_size"],
                                asm=(reason(raw[lo:lo + s["st_size"]], order == "big", **facts) or "") if reason else "")
    return out


def rom_print(fn, arch: str, big_endian: bool, label) -> Print:
    """Fingerprint of a ROM project's function, from its target bytes and the addresses its relocations fill in."""
    relocs = sorted((int(r[0]), int(r[1]), int(r[2])) for r in json.loads(fn["relocs"] or "[]") if len(r) >= 3)
    code = mask_relocs(fn["target"] or b"", [Reloc(o, t, "", 0) for o, t, _ in relocs], arch, big_endian)
    return Print(_key(code, [(o, t) for o, t, _ in relocs]), [label(a) for _, _, a in relocs], len(fn["target"] or b""),
                 [a for _, _, a in relocs])


def rom_renames(syms, source: str, a: Print, b: Print, a_name: str, b_name: str, syms_b=None) -> dict[str, str] | None:
    """renames() for a ROM project, where relocations are addresses: for each address a's code refers to, the
    name a's C writes for it (the nearest symbol at or below it that the C mentions: a struct whose field it is,
    an array whose element), and for b the same offset from b's address, by its name or its automatic one.
    `syms_b`: b's names when b is in another game (the same address there can be something else)."""
    import bisect

    words = set(re.findall(r"\b[A-Za-z_]\w*\b", source))
    out: dict[str, str] = {}
    if a_name != b_name:
        out[a_name] = b_name
    for in_a, in_b in zip(a.addrs, b.addrs):
        if in_a == in_b and syms_b is None:
            continue
        below = bisect.bisect_right(syms.starts, in_a) - 1
        base = None
        for k in range(below, max(-1, below - LABELS_BACK), -1):  # back to the symbol the C writes
            sym = syms.by_addr[syms.starts[k]]
            if in_a - sym.address > LABEL_REACH:
                break
            if sym.name in words:
                base = sym
                break
        if base is None:
            continue  # the C doesn't name it (a literal, a string it defines): the judge compares the values
        to = in_b - (in_a - base.address)
        other = (syms_b or syms).by_addr.get(to)
        name = other.name if other is not None else f"D_{to:08X}"
        if out.setdefault(base.name, name) != name:
            return None
    return out


def renames(a: Print, b: Print, a_name: str, b_name: str) -> dict[str, str] | None:
    """What to rename in a's code for it to be b's: its own name, and every name a relocation of a refers to,
    to what the same relocation of b refers to. None when that can't be said in C (a name refers to two
    different things, or one side isn't a C name)."""
    out: dict[str, str] = {}
    if a_name != b_name:
        if not (IDENT.match(a_name) and IDENT.match(b_name)):
            return None
        out[a_name] = b_name
    for x, y in zip(a.refs, b.refs):
        if x == y:
            continue
        bx, _, ox = x.partition("+")
        by, _, oy = y.partition("+")
        if ox != oy:
            return None  # another field or element: the code differs, not just the names
        if bx.startswith(".") or by.startswith(".") or bx.startswith("@") or by.startswith("@"):
            continue  # a file's own data (literals, statics): the C writes the values, the judge compares them
        if not (IDENT.match(bx) and IDENT.match(by)):
            return None
        if out.setdefault(bx, by) != by:
            return None
    return out


def _declared(header_a: str, header_b: str, names: dict[str, str], a_name: str) -> str:
    """What a's header declares for a's names, declared for b's too (`extern Player g_player;` for D_80047000),
    unless b's header declares them already."""
    decls = []
    for old_name, new_name in names.items():
        if old_name == a_name or re.search(rf"\b{re.escape(new_name)}\b", header_b):
            continue
        line = next((ln.strip() for ln in header_a.splitlines() if re.search(rf"\b{re.escape(old_name)}\b", ln)
                     and ln.strip().endswith(";") and "{" not in ln and not ln.strip().startswith(("#", "//", "/*"))),
                    None)
        if line:
            decls.append(apply(line, {old_name: new_name}))
    return "\n".join(decls) + ("\n" if decls else "")


def apply(source: str, names: dict[str, str]) -> str:
    """Rename identifiers in C source, all at once (so a -> b, b -> c doesn't make a into c)."""
    if not names:
        return source
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b")
    return pattern.sub(lambda m: names[m.group(1)], source)


def _file_compiler(console, fp, fn) -> str | None:
    """The compiler a project builds a function's source file with, where each file has its own (None: its build's)."""
    try:
        source = fp.unit(fn["unit"]).source
    except Exception:  # noqa: BLE001  a unit the project no longer has: no compiler to compare
        return None
    return console.file_compiler(fp, source) if source else None


@dataclass
class Job:
    state: str = "running"
    pairs: int = 0
    tried: int = 0
    matched: int = 0
    asm: int = 0  # hand-written assembly (twins of asm functions, code no compiler makes), out of agents' way
    cross_candidates: int = 0
    cross_tried: int = 0
    cross_matched: int = 0
    started_at: float = field(default_factory=time.time)
    stop: threading.Event = field(default_factory=threading.Event)
    ordered: threading.Event = field(default_factory=threading.Event)  # assembly set aside, claim order stored


def _ar_members(data: bytes) -> list[bytes]:
    if not data.startswith(b"!<arch>\n"):
        raise NetworkError("This is not an ar object archive")
    out, at = [], 8
    while at + 60 <= len(data):
        header = data[at:at + 60]
        if header[58:60] != b"`\n":
            raise NetworkError("The object archive has an invalid member header")
        try:
            size = int(header[48:58].strip())
        except ValueError:
            raise NetworkError("The object archive has an invalid member size") from None
        start, end = at + 60, at + 60 + size
        if end > len(data):
            raise NetworkError("The object archive ends inside a member")
        out.append(data[start:end])
        at = end + (size % 2)
    return out


class Twins:
    """Per project: fingerprints (cached), and the work of trying matched code on its twins."""

    def __init__(self, net):
        self.net = net
        self._prints: dict[int, tuple[float, tuple | None, dict[int, Print]]] = {}  # (when, ROM layout, prints)
        self._index_stamps: dict[int, tuple] = {}
        self._print_generations = defaultdict(int)
        self._index_lock = threading.Lock()  # full refresh and new matches cannot erase each other's rows
        self._lock = threading.Lock()
        self._queue: dict[int, set[int]] = defaultdict(set)  # project id -> newly matched functions to propagate
        self._busy: set[int] = set()
        self._trying: set[int] = set()  # functions a pass is trying now (a full pass and one after a match can overlap)
        self.jobs: dict[str, Job] = {}  # slug -> the full pass (the one the overview shows)

    # ------------------------------------------------------------------ fingerprints

    def prints(self, proj) -> dict[int, Print]:
        """function id -> fingerprint, for every function of the project (ROM: from the database, kept until a split
        or a cut changes the functions, as they took 0.7 s on GTA San Andreas every 10 minutes; fork: from the target
        objects, looked at again every 10 minutes and cached in the project's state folder by their modification
        times)."""
        # Indexing already holds the shared index lock. Looking the project up again can
        # synchronize progress and wait for a project lock whose owner needs this index
        # to record a restored match. Read the kind from the caller's project snapshot.
        fork = self.net.cfg(proj).get("kind") == "fork"
        if not fork and getattr(self.net.store._local, 'in_tx', False):
            return self._build_rom_prints(proj)  # Never publish values from a transaction that may roll back.
        layout = None if fork else self.net.store.read_version('fingerprints', proj['id'])
        def build():
            return self._fork_prints(proj) if fork else self._rom_prints(proj)
        with self._lock:
            generation = self._print_generations[proj['id']]
        prints = self.net.store.read_cache.get(('twin-prints', proj['id']), (fork, layout, generation), build,
                                              ttl=FORK_PRINTS_TTL if fork else None)
        with self._lock:
            self._prints[proj['id']] = (time.time(), layout, prints)
        return prints

    def _rom_prints(self, proj) -> dict[int, Print]:
        from .view_snapshots import persistent
        revision = self.net.store.read_version('fingerprints', proj['id'])
        def build():
            return {str(fid): asdict(p) for fid, p in self._build_rom_prints(proj).items()}
        values = persistent(self.net.store, ('rom-fingerprints', proj['id']), (2, revision, self._print_generations[proj['id']]), build)
        return {int(fid): Print(**value) for fid, value in values.items()}

    def _build_rom_prints(self, proj) -> dict[int, Print]:
        cfg = self.net.cfg(proj)
        syms = self.net.symbols(proj["id"])
        return {r["id"]: rom_print(r, proj["arch"] or "mips", bool(cfg.get("big_endian", True)), syms.label) for r in self.net.store.all(
            "SELECT id, target, relocs FROM functions WHERE project_id=? AND status!='asm'", proj["id"])}

    def _fork_prints(self, proj) -> dict[int, Print]:
        from ..forks.project import STATE, ForkError, ForkProject

        fp = ForkProject(Path(proj["root"]))
        cache_path = fp.root / STATE / "twins.json"
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        if cache.get("_v") != _CACHE_VERSION:
            cache = {"_v": _CACHE_VERSION}
        rows = self.net.store.all("SELECT id, name, unit, meta FROM functions WHERE project_id=?", proj["id"])
        by_unit: dict[str, list] = defaultdict(list)
        for r in rows:
            by_unit[r["unit"]].append(r)
        out: dict[int, Print] = {}
        changed = False
        for uname, urows in by_unit.items():
            try:
                unit = fp.unit(uname)
            except (ForkError, ValueError):
                continue
            if unit.target is None or not unit.target.exists():
                continue
            mtime = unit.target.stat().st_mtime
            entry = cache.get(uname)
            if not entry or entry.get("mtime") != mtime:
                try:
                    prints = object_prints(unit.target.read_bytes())
                except Exception:  # an object pyelftools can't read: no twins for it
                    prints = {}
                entry = {"mtime": mtime, "prints": {k: [p.key, p.refs, p.size, p.asm, p.name] for k, p in prints.items()}}
                cache[uname] = entry
                changed = True
            for r in urows:
                sym = json.loads(r["meta"] or "{}").get("symbol") or r["name"].split("@")[0]
                p = entry["prints"].get(sym)
                if p:
                    out[r["id"]] = Print(p[0], p[1], p[2], asm=p[3], name=p[4])
        if changed:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(cache), encoding="utf-8")
            except OSError:
                pass
        return out

    def forget(self, proj) -> None:
        with self._lock:
            self._prints.pop(proj["id"], None)
            self._print_generations[proj['id']] += 1

    # ------------------------------------------------------------------ who's whose twin

    def pairs(self, proj, only: set[int] | None = None, min_size: int = 8) -> list[tuple[int, int]]:
        """(matched function, unmatched twin) pairs to try, the twins of `only` (newly matched ones) or all."""
        prints = self.prints(proj)
        rows = {r["id"]: r for r in self.net.store.all(
            "SELECT id, status FROM functions WHERE project_id=?", proj["id"])}
        groups: dict[str, list[int]] = defaultdict(list)
        for fid, p in prints.items():
            if p.size >= min_size:  # (a lone `blr` is everyone's twin, and its own easy match anyway)
                groups[p.key].append(fid)
        out = []
        for members in groups.values():
            matched = [f for f in members if rows.get(f) and rows[f]["status"] == "matched"]
            if not matched:
                continue
            if only is not None and not set(matched) & only:
                continue
            for f in members:
                if rows.get(f) and rows[f]["status"] == "open":
                    out.append((matched[0], f))
        return out

    def twin_of(self, proj, fid: int) -> int | None:
        """A matched twin of a function, for its brief."""
        prints = self.prints(proj)
        p = prints.get(fid)
        if p is None or p.size < 8:
            return None
        for other, q in prints.items():
            if other != fid and q.key == p.key:
                r = self.net.store.one("SELECT status FROM functions WHERE id=?", other)
                if r and r["status"] == "matched":
                    return other
        return None

    def update_boosts(self, proj, pending: bool = False) -> int:
        """Store the claim order twins give (functions.boost): for each group of identical unmatched functions, one
        gets the group's size - 1 (matching it matches the others), the others -1. `pending`: a pass is about to
        try matched code on its unmatched twins, which go last meanwhile (-1): they may be free in a minute.
        Returns how many groups."""
        prints = self.prints(proj)
        rows = {r["id"]: r["status"] for r in self.net.store.all(
            "SELECT id, status FROM functions WHERE project_id=?", proj["id"])}
        groups: dict[str, list[int]] = defaultdict(list)
        for fid, p in prints.items():
            if p.size >= 8:
                groups[p.key].append(fid)
        updates = []
        names = {r["id"]: r["name"] for r in self.net.store.all(
            "SELECT id, name FROM functions WHERE project_id=? AND status='matched'", proj["id"])}
        for members in groups.values():
            open_ = sorted(f for f in members if rows.get(f) == "open")
            matched = [f for f in members if rows.get(f) == "matched"]
            if matched:
                if pending:
                    updates += [(-1, f) for f in open_]
                elif not any(names.get(f, "").startswith(_GENERATED) for f in matched):
                    # its twin's code didn't match by itself (a type or declaration this file lacks, mostly):
                    # an agent starts from it, shown in the brief, so these go first
                    updates += [(1, f) for f in open_]
                continue
            if len(open_) < 2:
                continue
            updates.append((len(open_) - 1, open_[0]))
            updates += [(-1, f) for f in open_[1:]]
        with self.net.store.tx() as db:
            db.execute("UPDATE functions SET boost=0 WHERE project_id=? AND boost!=0", (proj["id"],))
            db.executemany("UPDATE functions SET boost=? WHERE id=?", updates)
        return sum(1 for b, _ in updates if b > 0)

    def set_aside_asm(self, proj) -> int:
        """Unmatched functions whose code no compiler makes (Console.asm_reason) get status 'asm': out of agents'
        way. Returns how many."""
        if not self.net.fork_slug(proj["slug"]):
            return self._set_aside_rom_asm(proj)
        prints = self.prints(proj)
        found = [fid for fid, p in prints.items() if p.asm]
        n = 0
        for at in range(0, len(found), SQL_CHUNK):
            chunk = found[at:at + SQL_CHUNK]
            for r in self.net.store.all(f"SELECT * FROM functions WHERE id IN ({','.join('?' * len(chunk))}) "
                                        "AND status='open'", *chunk):
                if '"reopened"' in (r["meta"] or ""):  # the user gave it back to the agents (Network.reopen)
                    continue
                self.net.forks.mark_asm(r, reason=prints[r["id"]].asm)
                n += 1
        return n

    def _set_aside_rom_asm(self, proj) -> int:
        """ROM projects: the console's asm_reason on each open function's code."""
        console = self.net.console(proj)
        from .boundary_review import refresh
        refresh(self.net,proj,console)
        marks = []
        for r in self.net.store.all("SELECT id, target, meta FROM functions WHERE project_id=? AND status='open'", proj["id"]):
            if '"reopened"' not in (r["meta"] or "") and (why := console.asm_reason(r["target"] or b"")):
                marks.append((json.dumps({**json.loads(r["meta"] or "{}"), "asm_code": why}), r["id"]))
        if marks:
            with self.net.store.tx() as db:
                db.executemany("UPDATE functions SET status='asm', meta=? WHERE id=? AND status='open'", marks)
                db.executemany("DELETE FROM claims WHERE function_id=?", [(fid,) for _, fid in marks])
        return len(marks)

    def _full_running(self, slug: str) -> bool:
        return (job := self.jobs.get(slug)) is not None and job.state == "running"

    def stop(self, slug: str) -> None:
        if (job := self.jobs.get(slug)) is not None and job.state == "running":
            job.stop.set()

    def refresh(self, slug: str) -> Job | None:
        """Bring a project's twins up to date in the background (see full_pass). Runs when agents start."""
        if (job := self.jobs.get(slug)) is not None and job.state == "running":
            return job
        if slug in self.net.links:  # a room member: the host's copy does this, and its matches come back here
            return None
        job = Job()
        with self._lock:
            self.jobs[slug] = job  # now: agents starting meanwhile wait for its claim order (job.ordered)

        def work() -> None:
            try:
                self.net.recheck(slug, log=log.info, only_if_updated=True)  # once after each update: what it knows
                self.full_pass(slug, job)
            except Exception as e:
                log.info("twins: %s: %s", slug, e)
                job.state = "done"
        threading.Thread(target=work, daemon=True, name=f"twins-{slug}").start()
        return job

    def full_pass(self, slug: str, job: Job | None = None) -> Job:
        """Hand-written assembly set aside, the claim order (agents claim in it from the start, not after the pass),
        matched code tried on every unmatched twin, the claim order again. One at a time per project: while one runs
        (agents started it, say), a second call waits for it."""
        with self._lock:
            running = self.jobs.get(slug)
            if running is not None and running.state == "running" and running is not job:
                job, mine = running, False
            else:
                job, mine = job or Job(), True
                self.jobs[slug] = job
        if not mine:
            while job.state == "running":
                time.sleep(1)
            return job
        try:
            proj = self.net.project_row(slug)
            job.asm += self.set_aside_asm(proj)
            self.update_boosts(proj, pending=True)
            job.ordered.set()
            self.run(slug, job)
            job.state = "running"  # run() finishes its local pass; cross-project work is still cancellable
            job.matched += self.across_games(slug, job)
            from . import free_candidates, named_donors, similar_matches

            free_candidates.budget(job, self.net.cascades.seconds, self.net.cascades.attempts)
            started = time.monotonic()
            job.matched += named_donors.run(self, slug, job)
            job.matched += free_candidates.run(self, slug, job)
            job.matched += similar_matches.run(self, slug, job)
            with self.net.store.tx() as db:
                db.execute('INSERT INTO reuse_runs(project_id,attempts,matched,bytes,seconds,created_at) VALUES(?,?,?,?,?,?)',
                           (proj['id'], job.reuse_attempts, job.reuse_matched,
                            job.reuse_bytes, time.monotonic() - started, time.time()))
            job.matched += self.net.permuter.repair_pass(slug, job.stop).matched
            from .iterative_repairs import optional_phase
            job.matched += optional_phase(self, slug, job)
            self.reuse_names(slug, job)
            from .sdk_names import name_functions
            name_functions(self.net, slug, job)
            self.update_boosts(self.net.project_row(slug))
        finally:
            job.ordered.set()
            if job.state == "running":  # (it failed before the pass)
                job.state = "stopped" if job.stop.is_set() else "done"
        return job

    def source_for(self, proj, a, b, includes: bool = False) -> str | None:
        """a's code made into b's (names swapped as their relocations say), or None if that can't be said.
        `includes`: (forks) bring a's file's includes along too."""
        prints = self.prints(proj)
        pa, pb = prints.get(a["id"]), prints.get(b["id"])
        if pa is None or pb is None or pa.key != pb.key:
            return None
        if self.net.fork_slug(proj["slug"]):
            names = self._fork_names(pa, pb, a, b)
            if names is None:
                return None
            return self.net.forks.twin_source(proj, a, b, names, includes)
        source = a["matched_source"]
        if not source:
            return None
        names = rom_renames(self.net.symbols(proj["id"]), source, pa, pb, a["name"], b["name"])
        if names is None:
            return None
        header = self.net._context(proj["id"], b["unit"])["header"]
        return _declared(header, header, names, a["name"]) + apply(source, names)

    @staticmethod
    def _fork_names(pa: Print, pb: Print, a, b) -> dict[str, str] | None:
        meta_a, meta_b = json.loads(a["meta"] or "{}"), json.loads(b["meta"] or "{}")
        return renames(pa, pb, pa.name or meta_a.get("symbol") or a["name"], pb.name or meta_b.get("symbol") or b["name"])

    # ------------------------------------------------------------------ the work

    def try_pair(self, agent, proj, a_id: int, b_id: int) -> str:
        """match | miss | asm | skip, for a's matched code on its twin b."""
        a = self.net.store.one("SELECT * FROM functions WHERE id=?", a_id)
        b = self.net.store.one("SELECT * FROM functions WHERE id=?", b_id)
        if a is None or b is None or a["status"] != "matched" or b["status"] != "open":
            return "skip"
        if self.net.store.one("SELECT 1 FROM claims WHERE function_id=?", b_id):
            return "skip"  # an agent is on it: its brief names the twin (see twin_of)
        with self._lock:
            if b_id in self._trying:
                return "skip"
            self._trying.add(b_id)
        try:
            return self._try_pair(agent, proj, a, b)
        finally:
            with self._lock:
                self._trying.discard(b_id)

    @staticmethod
    def _miss_key(source: str) -> str:
        return hashlib.sha1(source.encode("utf-8", "replace")).hexdigest()[:20]

    def _missed(self, b_id: int, source: str) -> bool:
        return self.net.store.one("SELECT 1 FROM twin_misses WHERE function_id=? AND source_hash=? AND at > ?",
                                  b_id, self._miss_key(source), time.time() - MISS_TTL) is not None

    def known_miss(self, proj, a_id: int, b_id: int) -> bool:
        """Whether a's code was tried on b, as it is now, and didn't match (see MISS_TTL)."""
        a = self.net.store.one("SELECT * FROM functions WHERE id=?", a_id)
        b = self.net.store.one("SELECT * FROM functions WHERE id=?", b_id)
        if a is None or b is None:
            return False
        try:
            source = self.source_for(proj, a, b)
        except (NetworkError, OSError, ValueError):
            return False
        return bool(source) and self._missed(b_id, source)

    def across_games(self, slug: str, job: Job | None = None) -> int:
        """Matched code from the user's other games, tried on this game's identical functions: libraries (an SDK's,
        a compiler's runtime, middleware) come in the same code from one game to the next, built by the same
        compiler. Same kind of processor, code identical once addresses are masked; compiler agreement ranks donors first; the
        names swapped as each game's relocations say; the judge decides. Better partial donors are tried
        afterwards through the same destination judge. Returns matches only, never partial improvements."""
        proj = self.net.project_row(slug)
        fork = bool(self.net.fork_slug(slug))
        from . import shared_reuse
        archived = self.net.match_library.reuse(self, proj, job) if hasattr(self.net, "match_library") else 0
        shared_reuse.discover(self, proj, job=job)
        if fork:
            matched = archived + shared_reuse.transfer(self, proj, job)
            mine = self.prints(proj)
            wanted = defaultdict(list)
            for row in self.net.store.all("SELECT id FROM functions WHERE project_id=? AND status='open'", proj["id"]):
                target = mine.get(row["id"])
                if target and not target.asm and target.size >= ACROSS_MIN_BYTES:
                    wanted[target.key].append(row["id"])
            from .partial_reuse import run as reuse_partial
            return matched + reuse_partial(self, proj, wanted, mine, job)
        if not (fork or self.net.is_rom(proj)) or slug in self.net.links:
            return 0
        from .free_candidates import exhausted
        bounded = job is not None and hasattr(job, 'reuse_limit')
        cfg = self.net.cfg(proj)
        mine = self.prints(proj)
        wanted: dict[str, list[int]] = defaultdict(list)
        for r in self.net.store.all("SELECT id FROM functions WHERE project_id=? AND status='open'", proj["id"]):
            p = mine.get(r["id"])
            if p is not None and not p.asm and p.size >= ACROSS_MIN_BYTES:
                wanted[p.key].append(r["id"])
        if not wanted:
            return archived
        others = [p for p in self.net.store.all(
            "SELECT * FROM projects WHERE id != ? AND arch=? AND big_endian=?",
            proj["id"], proj["arch"], proj["big_endian"]) if self.net.is_rom(p)] if not fork else []
        for other in others:
            if bounded and exhausted(job):
                return archived
            self.index_project(other)
        matched = archived
        agent, _ = self.net.local_agent("autopilot")
        if not fork:
            # Imported projects do not necessarily fill the database's architecture columns.
            for other in self.net.store.all("SELECT * FROM projects WHERE id != ?", proj["id"]):
                if self.net.fork_slug(other["slug"]) and other["slug"] not in self.net.links:
                    try:
                        matched += self._fork_to_rom(agent, proj, other, wanted, mine, job)
                    except Exception:
                        log.exception("twins: ROM %s from imported %s", slug, other["slug"])
        by_id = {p["id"]: p for p in others}
        sources = []
        keys = list(wanted)
        for at in range(0, len(keys), SQL_CHUNK):
            part = keys[at:at + SQL_CHUNK]
            sources += self.net.store.all(
                "SELECT i.*, f.name, f.vram, f.matched_source FROM cross_game_index i "
                "JOIN functions f ON f.id=i.function_id AND f.status='matched' "
                "WHERE i.project_id != ? AND i.fingerprint IN (" + ",".join("?" for _ in part) + ")",
                proj["id"], *part)
        sources.sort(key=lambda a: a["compiler"] != self._compiler_key(cfg["compiler"]))
        source_prints = {p["id"]: self.prints(p) for p in others}
        source_symbols = {p["id"]: self.net.symbols(p["id"]) for p in others}
        target_symbols = self.net.symbols(proj["id"])
        headers = {p["id"]: self.net._context(p["id"])["header"] for p in others}
        for a in sources:
            if job is not None and job.stop.is_set():
                break
            other = by_id.get(a["project_id"])
            if other is None:
                continue
            pa = source_prints[other["id"]].get(a["function_id"])
            if pa is None:
                continue
            for b_id in wanted.get(a["fingerprint"], []):
                if bounded and exhausted(job):
                    return matched
                b = self.net.store.one("SELECT * FROM functions WHERE id=? AND status='open'", b_id)
                if b is None or self.net.store.one("SELECT 1 FROM claims WHERE function_id=?", b_id):
                    continue
                if job is not None:
                    job.cross_candidates += 1
                names = rom_renames(source_symbols[other["id"]], a["matched_source"] or "", pa, mine[b_id],
                                    a["name"], b["name"], target_symbols)
                if names is None or not a["matched_source"]:
                    continue
                header_b = self.net._context(proj["id"], b["unit"])["header"]
                source = _declared(headers[other["id"]], header_b, names, a["name"]) + apply(a["matched_source"], names)
                if self._missed(b_id, source):
                    continue
                if job is not None:
                    job.cross_tried += 1
                if bounded:
                    job.reuse_attempts += 1
                outcome, why = self._attempt_twin(agent, proj, a, b, source, origin=f" in {other['name']}", mapping=names)
                with self.net.store.tx() as db:
                    db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,"
                               "source_function_id,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                               (proj["id"], b_id, other["id"], a["function_id"], outcome, why[:WHY_CHARS], time.time()))
                    if outcome == "miss":
                        db.execute("INSERT OR REPLACE INTO twin_misses(function_id, source_hash, project_id, at, why) "
                                   "VALUES(?,?,?,?,?)", (b_id, self._miss_key(source), proj["id"], time.time(), why))
                if outcome == "match":
                    if bounded:
                        job.reuse_matched += 1
                        job.reuse_bytes += b["size"]
                    matched += 1
                    if job is not None:
                        job.cross_matched += 1
                    log.info("twins: %s matched from %s in %s", b["name"], a["name"], other["slug"])
        from .partial_reuse import run as reuse_partial
        return matched + reuse_partial(self, proj, wanted, mine, job)

    @staticmethod
    def _compiler_key(compiler: dict) -> str:
        return json.dumps(compiler, sort_keys=True, separators=(",", ":"))

    def index_project(self, proj) -> int:
        """Refresh the persistent library from verified ROM C; reopened or resplit functions leave the index."""
        if not self.net.is_rom(proj):
            return 0
        with self._index_lock:
            return self._index_project(proj)

    def _index_project(self, proj) -> int:
        cfg = self.net.cfg(proj)
        counts = self.net.store.one("SELECT COUNT(*) AS n,MAX(matched_at) AS latest FROM functions "
                                    "WHERE project_id=? AND status='matched' AND matched_source IS NOT NULL", proj["id"])
        stamp = (self.net._layout_key(proj), self._compiler_key(cfg["compiler"]),
                 self._compiler_key({"ranges": cfg.get("compiler_ranges") or [],'functions':cfg.get('function_compilers') or {}}), counts["n"], counts["latest"])
        if self._index_stamps.get(proj["id"]) == stamp:
            return counts["n"]
        prints = self.prints(proj)
        rows = self.net.store.all("SELECT id,name,vram,rom_offset,matched_at FROM functions WHERE project_id=? AND status='matched' "
                                  "AND matched_source IS NOT NULL", proj["id"])
        entries = [(r["id"], proj["id"], prints[r["id"]].key,
                    self._compiler_key(compiler_for(cfg, r["vram"], r)), prints[r["id"]].size,
                    r["matched_at"] or 0) for r in rows if r["id"] in prints and not prints[r["id"]].asm]
        with self.net.store.tx() as db:
            db.execute("DELETE FROM cross_game_index WHERE project_id=?", (proj["id"],))
            db.executemany("INSERT INTO cross_game_index VALUES(?,?,?,?,?,?)", entries)
        self._index_stamps[proj["id"]] = stamp
        return len(entries)

    def index_function(self, proj, fid: int) -> None:
        for snapshot in getattr(self, "_shared_discovery", {}).values():
            snapshot["at"] = 0  # new evidence is visible in the next bounded discovery pass
        if not self.net.is_rom(proj):
            return
        with self._index_lock:
            self._index_function(proj, fid)

    def clear_index(self, project_id: int) -> None:
        """An import replaced the function layout; discard signatures before they can be suggested."""
        with self._index_lock, self.net.store.tx() as db:
            db.execute("DELETE FROM cross_game_index WHERE project_id=?", (project_id,))
            self._index_stamps.pop(project_id, None)

    def _index_function(self, proj, fid: int) -> None:
        row = self.net.store.one("SELECT id,name,vram,rom_offset,matched_at,status,matched_source FROM functions WHERE id=?", fid)
        p = self.prints(proj).get(fid)
        cfg = self.net.cfg(proj)
        with self.net.store.tx() as db:
            if row is None or row["status"] != "matched" or not row["matched_source"] or p is None or p.asm or not cfg.get("compiler"):
                db.execute("DELETE FROM cross_game_index WHERE function_id=?", (fid,))
            else:
                db.execute("INSERT OR REPLACE INTO cross_game_index VALUES(?,?,?,?,?,?)",
                           (fid, proj["id"], p.key,
                            self._compiler_key(compiler_for(cfg, row["vram"], row)), p.size,
                            row["matched_at"] or 0))

    def cross_game_results(self, slug: str) -> dict:
        """What exact transfers tried, including failures and where accepted C came from."""
        proj = self.net.project_row(slug)
        rows = self.net.store.all(
            "SELECT a.outcome,a.reason,a.created_at,f.name AS target,s.name AS source,p.name AS game,p.slug AS source_slug "
            "FROM cross_game_attempts a JOIN functions f ON f.id=a.function_id "
            "JOIN functions s ON s.id=a.source_function_id JOIN projects p ON p.id=a.source_project_id "
            "WHERE a.project_id=? ORDER BY a.id DESC LIMIT 30", proj["id"])
        matches = self.net.store.all(
            "SELECT a.outcome,a.reason,a.created_at,f.name AS target,s.name AS source,p.name AS game,p.slug AS source_slug "
            "FROM cross_game_attempts a JOIN functions f ON f.id=a.function_id "
            "JOIN functions s ON s.id=a.source_function_id JOIN projects p ON p.id=a.source_project_id "
            "WHERE a.project_id=? AND a.outcome='match' ORDER BY a.id DESC LIMIT 30", proj["id"])
        counts = self.net.store.all("SELECT outcome,COUNT(*) AS n FROM cross_game_attempts WHERE project_id=? "
                                    "GROUP BY outcome", proj["id"])
        from .shared_reuse import summary
        return {"counts": {r["outcome"]: r["n"] for r in counts}, "recent": [dict(r) for r in rows],
                "matches": [dict(r) for r in matches], "discovery": summary(self, proj)}

    def suggestions(self, slug: str, function: str, limit: int = 5) -> list[dict]:
        """Read-only cross-game and local SDK name hints. Similar code is a lead, never a verified match."""
        proj = self.net.project_row(slug)
        from .matched_context import reference_hints
        from .shared_reuse import hints
        if self.net.fork_slug(slug):
            return reference_hints(self.net, hints(self, proj, function, limit))
        def finish(items):
            from .shared_reuse import hints as shared_hints
            merged = {}
            for hint in [*shared_hints(self, proj, function, limit), *items]:
                merged.setdefault((hint["name"], hint["game"]), hint)
            return reference_hints(self.net, list(merged.values())[:limit])

        fn = self.net._function(function, slug)
        target = self.prints(proj).get(fn["id"])
        if target is None or target.size < ACROSS_MIN_BYTES:
            return []
        compiler = self._compiler_key(compiler_for(self.net.cfg(proj), fn["vram"], fn))
        exact = self.net.store.all(
            "SELECT i.*, f.name, p.name AS game FROM cross_game_index i "
            "JOIN functions f ON f.id=i.function_id AND f.status='matched' "
            "JOIN projects p ON p.id=i.project_id WHERE i.project_id!=? AND p.arch=? AND p.big_endian=? "
            "AND i.fingerprint=? AND i.compiler=? LIMIT ?", proj["id"], proj["arch"], proj["big_endian"],
            target.key, compiler, limit)
        hints = [{"name": r["name"], "game": r["game"], "score": 1.0, "kind": "verified C in another game",
                  "function_id": r["function_id"]} for r in exact]
        for r in self.net.store.all("SELECT corpus,name FROM sdk_signatures WHERE arch=? AND big_endian=? "
                                    "AND fingerprint=?", proj["arch"], proj["big_endian"], target.key):
            from .sdk_library import allowed
            if not allowed(self.net, proj, r["corpus"]):
                continue
            hints.append({"name": r["name"], "game": r["corpus"], "score": 1.0,
                          "kind": "SDK object signature; name only"})
        from .sdk_hypotheses import hints as library_hints
        hints += library_hints(self.net, proj, fn, limit)
        if proj["arch"] != "mips" or len(hints) >= limit:
            return finish(hints)
        def candidates():
            # Limit indexed metadata before reading any target blobs. Sorting the old joined
            # projection materialized code bytes for every size-compatible function.
            rows = self.net.store.all("""WITH shortlist AS MATERIALIZED (
                SELECT i.function_id,i.project_id,i.size FROM cross_game_index i INDEXED BY idx_cross_game_compiler_size
                JOIN functions f INDEXED BY idx_functions_matched_id ON f.id=i.function_id AND f.status='matched'
                JOIN projects p ON p.id=i.project_id
                WHERE i.project_id!=? AND p.arch=? AND p.big_endian=? AND i.fingerprint!=?
                AND i.compiler=? AND i.size BETWEEN ? AND ? ORDER BY ABS(i.size-?),i.function_id LIMIT 400)
                SELECT s.function_id,f.name,f.target,f.vram,p.name AS game FROM shortlist s
                JOIN functions f ON f.id=s.function_id JOIN projects p ON p.id=s.project_id""",
                proj['id'], proj['arch'], proj['big_endian'], target.key, compiler,
                max(ACROSS_MIN_BYTES, int(target.size * .75)), int(target.size / .75), target.size)
            ops = similarity.mips_mnemonics(fn['target'], bool(proj['big_endian']), fn['vram'] or 0)
            by_id = {str(r['function_id']):r for r in rows}
            decoded = {str(r['function_id']):similarity.mips_mnemonics(r['target'], bool(proj['big_endian']), r['vram'] or 0)
                       for r in rows if r['target']}
            return [{'name':by_id[fid]['name'], 'game':by_id[fid]['game'], 'score':round(score,3),
                     'kind':'similar code; unverified', 'function_id':by_id[fid]['function_id']}
                    for fid, score in similarity.rank(ops, decoded, top=limit, floor=.8)]
        version = (self.net.store.read_version('reuse_index'), self.net.store.read_version('fingerprints'),
                   self.net.store.read_version('matched_evidence'), self.net.store.read_version('project_list'))
        # Keep only the strongest names/IDs/scores, never the 400 target blobs or decoded instruction lists.
        hints += self.net.store.read_cache.get(('suggestion-shortlist', proj['id'], target.key, compiler, target.size, limit),
                                              version, candidates)
        return finish(hints)

    def import_sdk_objects(self, path: Path, corpus: str) -> int:
        """Index only signatures and names from a user-selected local ELF object or ar archive."""
        if not path.is_file() or path.suffix.lower() not in (".o", ".a"):
            raise NetworkError("Choose a local .o or .a SDK object file")
        if path.stat().st_size > 100 * 1024 * 1024:
            raise NetworkError("SDK object archive is larger than 100 MB")
        data = path.read_bytes()
        members = [data] if path.suffix.lower() == ".o" else _ar_members(data)
        signatures = []
        shapes = []
        for member in members:
            try:
                from elftools.common.exceptions import ELFError
                from elftools.elf.elffile import ELFFile
                elf = ELFFile(BytesIO(member))
                machine = _MACHINE.get(elf["e_machine"])
                if machine is None:
                    continue
                arch_id, order = machine
                big = int((order or ("little" if elf.little_endian else "big")) == "big")
                from .sdk_hypotheses import shapes as library_shapes
                shapes += library_shapes(member, corpus, object_prints(member))
                signatures += [(corpus, arch_id, big, name, p.key, p.size)
                               for name, p in object_prints(member).items() if p.size >= ACROSS_MIN_BYTES]
            except (ELFError, ValueError, KeyError, OSError):
                continue  # an archive can contain a non-object symbol table
        with self.net.store.tx() as db:
            db.executemany("INSERT OR IGNORE INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) "
                           "VALUES(?,?,?,?,?,?)", signatures)
            db.executemany("INSERT OR REPLACE INTO sdk_shapes VALUES(?,?,?,?,?,?,?)", shapes)
        return len(signatures)

    def reuse_names(self, slug: str, job: Job | None = None, *, donor_slug: str | None = None) -> int:
        """Infer names from exact verified donors, preserving existing names and rejecting ambiguity.

        These are inferred names, not proof of the original source's spelling. Tiny generic functions
        are excluded. The normal rename operation checks all existing matches before saving changes.
        """
        from .. import consoles
        from .function_names import generated, record
        from .function_names import state as name_state

        proj = self.net.project_row(slug)
        if slug in self.net.links:
            return 0
        cfg = self.net.cfg(proj)
        console = consoles.of(cfg)
        mine = self.prints(proj)
        wanted = {p.key for p in mine.values() if p.size >= ACROSS_MIN_BYTES and not p.asm}
        donors = defaultdict(dict)
        for other in self.net.store.all("SELECT * FROM projects WHERE id != ?", proj["id"]):
            if job is not None and job.stop.is_set():
                return 0
            if donor_slug is not None and other["slug"] != donor_slug:
                continue
            if other["slug"] in self.net.links:
                continue
            try:
                con = consoles.of(self.net.cfg(other))
                if (con.arch, con.big_endian) != (console.arch, console.big_endian):
                    continue
                rows = self.net.store.all("SELECT id,name,meta FROM functions WHERE project_id=? AND status='matched'",
                                          other["id"])
                if not rows:
                    continue
                prints = self.prints(other)
                for row in rows:
                    p = prints.get(row["id"])
                    name = (json.loads(row["meta"] or "{}").get("real") or p.name or row["name"].split("@")[0]) if p else ""
                    if p and p.key in wanted and not p.asm and IDENT.fullmatch(name) and not generated(name) and not name.startswith('func_'):
                        donors[p.key].setdefault(name, {"source_project": other["slug"],
                                                       "source_function_id": row["id"], "source_name": name})
            except (NetworkError, OSError, ValueError):
                continue  # moved or incompletely prepared donor
        rows = self.net.store.all("SELECT * FROM functions WHERE project_id=?", proj["id"])
        taken = {r["name"] for r in rows}
        candidates = []
        for row in rows:
            p = mine.get(row["id"])
            choices = donors.get(p.key, {}) if p else {}
            naming = name_state(row)
            if naming["named"] or naming["renamed"] or len(choices) != 1:
                continue
            name, provenance = next(iter(choices.items()))
            if name in taken or self.net.store.one("SELECT 1 FROM claims WHERE function_id=?", row["id"]):
                continue
            candidates.append((row, name, provenance))
        # A shared fingerprint can occur several times in the target. Do not pick an arbitrary owner.
        counts = defaultdict(int)
        for _, name, _ in candidates:
            counts[name] += 1
        candidates = [(r, n, p) for r, n, p in candidates if counts[n] == 1][:32]
        if not candidates or (job is not None and job.stop.is_set()):
            return 0
        agent, _ = self.net.local_agent("autopilot")
        if not self.net.fork_slug(slug):
            try:
                result = self.net.rename_symbols(agent, slug, [(r["name"], n) for r, n, _ in candidates],
                                                 "Names inferred from exact verified functions in other projects")
            except (NetworkError, OSError, ValueError):
                return 0
            if not result.startswith("✅"):
                return 0
            with self.net.store.tx() as db:
                for row, name, provenance in candidates:
                    record(db, row["id"], row["name"], name, method="exact_cross_game", **provenance)
            return len(candidates)
        renamed = 0
        for row, name, provenance in candidates[:4]:
            if job is not None and job.stop.is_set():
                break
            try:
                result = self.net.rename_symbol(agent, slug, row["name"], name,
                                                "Name inferred from an exact verified function in "
                                                + provenance["source_project"])
            except (NetworkError, OSError, ValueError):
                continue
            if not result.startswith("✅"):
                continue
            with self.net.store.tx() as db:
                record(db, row["id"], row["name"], name, method="exact_cross_game", **provenance)
            renamed += 1
        return renamed

    def _fork_to_rom(self, agent, proj, other, wanted, mine, job) -> int:
        """Try imported, verified C against identical ROM fingerprints; only the ROM judge accepts it.

        Donor headers are deliberately not installed in the ROM project. Self-contained functions and
        declarations already available in its context work; missing dependencies remain ordinary misses.
        """
        from .. import consoles

        _, fp, cfg = self.net.forks.get(other["slug"])
        console = consoles.of(cfg)
        if console.arch != proj["arch"] or console.big_endian != bool(proj["big_endian"]):
            return 0
        bounded = job is not None and hasattr(job, "reuse_limit")
        theirs = self.prints(other)
        matched = 0
        for a in self.net.store.all("SELECT * FROM functions WHERE project_id=? AND status='matched'", other["id"]):
            pa = theirs.get(a["id"])
            if pa is None or pa.asm:
                continue
            for b_id in wanted.get(pa.key, []):
                if job is not None and (job.stop.is_set() or (bounded and (
                        job.reuse_attempts >= job.reuse_limit or time.monotonic() >= job.reuse_deadline))):
                    return matched
                b = self.net.store.one("SELECT * FROM functions WHERE id=? AND status='open'", b_id)
                if b is None or self.net.store.one("SELECT 1 FROM claims WHERE function_id=?", b_id):
                    continue
                names = renames(pa, mine[b_id], pa.name or a["name"].split("@")[0], b["name"])
                if names is None or self.net.forks.is_asm(other, a):
                    continue
                original = self.net.forks._current_source(fp, a)
                if not original:
                    continue
                source = apply(original, names)
                if self._missed(b_id, source):
                    continue
                if job is not None:
                    job.cross_candidates += 1
                    job.cross_tried += 1
                if bounded:
                    job.reuse_attempts += 1
                outcome, why = self._attempt_twin(agent, proj, a, b, source, origin=f" in {other['name']}", mapping=names)
                with self.net.store.tx() as db:
                    db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,"
                               "source_function_id,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                               (proj["id"], b_id, other["id"], a["id"], outcome, why[:WHY_CHARS], time.time()))
                    if outcome == "miss":
                        db.execute("INSERT OR REPLACE INTO twin_misses(function_id,source_hash,project_id,at,why) "
                                   "VALUES(?,?,?,?,?)", (b_id, self._miss_key(source), proj["id"], time.time(), why))
                if outcome == "match":
                    if bounded:
                        job.reuse_matched += 1
                        job.reuse_bytes += b["size"]
                    matched += 1
                    if job is not None:
                        job.cross_matched += 1
        return matched

    def _try_pair(self, agent, proj, a, b) -> str:
        try:
            source = self.source_for(proj, a, b)
        except (NetworkError, OSError, ValueError) as e:
            log.info("twins: %s from %s not tried: %s", b["name"], a["name"], str(e)[:WHY_CHARS])
            return "skip"
        if not source:
            if self.net.fork_slug(proj["slug"]) and self.net.forks.is_asm(proj, a):
                self.net.forks.mark_asm(b, a["name"])
                return "asm"
            return "skip"
        if self._missed(b["id"], source):
            return "known"
        outcome, why = self._attempt_twin(agent, proj, a, b, source)
        if outcome == "miss":
            with self.net.store.tx() as db:
                db.execute("INSERT OR REPLACE INTO twin_misses(function_id, source_hash, project_id, at, why) "
                           "VALUES(?,?,?,?,?)", (b["id"], self._miss_key(source), proj["id"], time.time(), why))
        return outcome

    def miss_why(self, b_id: int) -> str | None:
        """What the last try of its twin's code said (the compiler's first error, or how close it came)."""
        row = self.net.store.one("SELECT why FROM twin_misses WHERE function_id=? ORDER BY at DESC LIMIT 1", b_id)
        return row["why"] if row else None

    @staticmethod
    def _why(res: dict) -> str:
        """The gist of a try that didn't match: the compiler's first error, or how close it came."""
        if res.get("score") is None:
            lines = [ln.strip(" #") for ln in (res.get("text") or "").splitlines()
                     if re.search(r"undefined|undeclared|expected|illegal|redefin|not a |declaration|conversion", ln)]
            return (lines[0] if lines else "it didn't build")[:WHY_CHARS]
        return (res.get("text") or "").splitlines()[0][:WHY_CHARS] if res.get("text") else f"score {res.get('score')}"

    def _attempt_twin(self, agent, proj, a, b, source: str, origin: str = "", theirs: bool = False,
                      similar: bool = False, feedback: dict | None = None, mapping: dict | None = None) -> tuple[str, str]:
        """match | miss | skip for a's code made into b's (`source`). `theirs`: a is another project's, so the tries
        that take more from a's file (its types, includes, declarations) don't apply: twin_source took its
        declarations already."""
        relation = "adapted from similar verified C in" if similar else "identical to"
        note = f"/* {relation} {a['name']}{origin} (matched), with its names changed by decompdecomp */\n"
        if mapping is not None and self.net.is_rom(proj) and not self.net.fork_slug(proj['slug']):
            from .repair_mappings import remember
            remember(self.net, a, b, note + source, mapping)
        try:
            if theirs:
                res = self.net.forks.attempt(agent, proj["slug"], b["name"], note + source, keep=False)
            elif self.net.fork_slug(proj["slug"]):
                forks, slug, src, prelude = self.net.forks, proj["slug"], source, ""
                res = forks.attempt(agent, slug, b["name"], note + src, keep=False)
                if res.get("score") is None and (types := forks.twin_types(proj, a, b, src)):  # a type a's file has
                    prelude = types
                    res = forks.attempt(agent, slug, b["name"], note + src, keep=False, prelude=prelude)
                if res.get("score") is None and (more := self.source_for(proj, a, b, includes=True)) and more != src:
                    src, prelude = more, forks.twin_types(proj, a, b, more)
                    res = forks.attempt(agent, slug, b["name"], note + src, keep=False, prelude=prelude)
                if res.get("score") is not None and not res.get("percent") and (c := forks.c_linkage(proj, a, b, src)):
                    src = c  # built, but nothing of it in the object: C code in a C++ file, its name mangled
                    res = forks.attempt(agent, slug, b["name"], note + src, keep=False, prelude=prelude)
                # the compiler names one undeclared name per build (a callee b's file declares further down, say):
                # declared as the files declare it, then built again
                prints, decls = self.prints(proj), []
                names = self._fork_names(prints[a["id"]], prints[b["id"]], a, b) if a["id"] in prints and b["id"] in prints else None
                for _ in range(UNDECLARED_RETRIES):
                    m = names is not None and res.get("score") is None and \
                        re.search(r"undefined identifier '(\w+)'", res.get("text") or "")
                    if not m:
                        break
                    decl = forks.twin_declaration(proj, a, b, m.group(1), names)
                    if not decl or decl in decls:
                        break
                    decls.append(decl)  # after the file's includes: also when the file declares it further down
                    res = forks.attempt(agent, slug, b["name"], note + src, keep=False,
                                        prelude="\n".join([prelude, *decls]).strip("\n"))
            else:
                res = self.net.try_quietly(agent, proj, b, note + source)
        except NetworkError as e:
            log.info("twins: %s from %s: %s", b["name"], a["name"], str(e)[:WHY_CHARS])
            return "skip", ""
        if feedback is not None:
            feedback.update(res)
        return ("match", "") if res.get("matched") else ("miss", self._why(res))

    def run(self, slug: str, job: Job | None = None, only: set[int] | None = None, workers: int = 3) -> Job:
        """Try matched code on every unmatched twin (or the twins of `only`), until a pass matches nothing new
        (a twin that matches can be the matched member of another group's pair next time)."""
        from concurrent.futures import ThreadPoolExecutor

        proj = self.net.project_row(slug)
        job = job or Job()
        if only is None:  # (a pass after a match runs beside the full one: the overview shows the full one)
            self.jobs[slug] = job
            job.ordered.set()
        agent, _ = self.net.local_agent("autopilot")
        if self.net.fork_slug(slug):
            workers = min(workers, 2)  # each try builds a file
        seen: set[tuple[int, int]] = set()
        try:
            for _ in range(6):
                tried = {q[1] for q in seen}
                pairs = [p for p in self.pairs(proj, only) if p not in seen and p[1] not in tried]
                if not pairs or job.stop.is_set():
                    break
                seen |= set(pairs)
                job.pairs += len(pairs)
                before = job.matched

                fresh: set[int] = set()

                def one(pair: tuple[int, int]) -> None:
                    if job.stop.is_set():
                        return
                    try:
                        outcome = self.try_pair(agent, proj, *pair)
                    except Exception as e:  # one odd function must not stop the rest
                        log.info("twins: %s failed: %s", pair, e)
                        outcome = "skip"
                    job.tried += outcome not in ("skip", "asm", "known")
                    job.matched += outcome == "match"
                    job.asm += outcome == "asm"
                    if outcome == "match":
                        fresh.add(pair[1])  # noqa: B023  called within this pass of the loop

                by_target: dict[int, tuple[int, int]] = {}
                for p in pairs:  # one try per unmatched function, unless its twin's code already failed on it
                    if p[1] not in by_target and not self.known_miss(proj, *p):
                        by_target[p[1]] = p
                with self.net.store.tx() as db:  # agents leave these for last meanwhile: they may be free in a minute
                    db.executemany("UPDATE functions SET boost=-1 WHERE id=? AND status='open'", [(b,) for b in by_target])
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    list(pool.map(one, by_target.values()))
                if job.matched == before:
                    break
                if only is not None:
                    only = fresh  # the next pass: twins of what this one matched (a whole run already covered all)
        finally:
            job.state = "stopped" if job.stop.is_set() else "done"
            if job.matched:
                with self.net.store.tx() as db:
                    self.net._event(db, proj["id"], agent.id, "autopilot",
                                    f"{job.matched} functions matched from identical code elsewhere in the game")
        return job

    def after_match(self, proj, fid: int) -> None:
        """A function just matched: try its code on its twins soon, in the background (debounced per project, so
        a burst of matches is one pass)."""
        if proj["slug"] in self.net.links:
            return
        with self._lock:
            self._queue[proj["id"]].add(fid)
            if proj["id"] in self._busy:
                return
            self._busy.add(proj["id"])

        def work() -> None:
            try:
                while True:
                    time.sleep(2)
                    with self._lock:
                        batch = self._queue.pop(proj["id"], set())
                        if not batch:
                            self._busy.discard(proj["id"])
                            return
                    if self.net.fork_slug(proj["slug"]):
                        self.forget(proj)  # matched code may have changed the objects' builds; targets stay
                    try:
                        if self.pairs(proj, batch):
                            self.run(proj["slug"], only=batch)
                            # twins that didn't match by themselves: back in line (unless a full pass is still on)
                            self.update_boosts(proj, pending=self._full_running(proj["slug"]))
                    except Exception as e:
                        log.info("twins after a match: %s", e)
            finally:
                with self._lock:
                    self._busy.discard(proj["id"])

        threading.Thread(target=work, daemon=True, name=f"twins-{proj['slug']}").start()
