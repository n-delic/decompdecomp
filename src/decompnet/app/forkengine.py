"""Fork projects: continue a public decomp with your agents, inside the project's own source tree.

Progress comes from objdiff reports. Agents claim functions (so parallel agents never collide), write them into
the project's real source files, build just that unit with the project's own build system and let objdiff judge.
A 100% match is committed to a local git branch, ready for the user to push or open a pull request with.
Guards from people who ran agents on decomps before: agents can only write C/C++ sources and headers (never
targets, assembly, configs or build scripts), inline assembly is refused, and a header edit is undone if it costs a
single match.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import threading
import time
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .. import arch, consoles, similarity, toolchains
from ..forks import csource, datadiff, drafts, dtksplits, ghidra, objdiff
from ..forks.objdiff import ObjdiffError
from ..forks.project import EDITABLE, GAME, PROGRAM, SCOPES, STATE, ForkError, ForkProject
from ..hostenv import NO_WINDOW
from ..network import (
    GAME_CODE,
    IN_BUILD,
    NOT_C_AGENTS,
    NOT_C_MODELS,
    PROGRESS_COLUMNS,
    Agent,
    Network,
    NetworkError,
    model_of,
    move_on_brief,
    set_aside,
)
from ..rom import RomError
from ..scoring import percent_text
from ..textedit import replace_once

_REPO = re.compile(r"^(https://(github\.com|gitlab\.com|codeberg\.org|git\.[\w.-]+)/[\w.-]+/[\w.-]+?)(\.git)?/?$")
# A match has to come from C/C++: no inline assembly, asm functions, asm includes or embedded binaries.
def _path_glob(glob: str) -> str:
    """A shell-style pattern an agent wrote, as pathlib takes it: `**` only as a whole path component
    ("src/**.cpp" -> "src/**/*.cpp", "src**/x.h" -> "src*/**/x.h"; pathlib raised ValueError on those)."""
    glob = glob.strip().lstrip("/")
    glob = re.sub(r"(?<=[^/])\*\*", "*/**", glob)
    return re.sub(r"\*\*(?=[^/])", "**/*", glob)


_CHEAT = re.compile(r"\b(__asm__|__asm|asm|_asm)\b|INCLUDE_ASM|GLOBAL_ASM|INCLUDE_RODATA|\.incbin|#\s*include\s*[\"<][^\">]*\.(s|S|asm|bin|inc\.c)[\">]")


# `register int x asm("r4");` binds a variable to a register: an accepted matching technique (pret's GBA
# projects use it), not assembly code, so it doesn't count as inline asm.
_REGVAR = re.compile(r"\b(?:asm|__asm__|__asm)\s*\(\s*\"\$?[a-z]{1,2}\d{0,2}\"\s*\)", re.I)


def _strip_comments(code: str) -> str:
    return _REGVAR.sub(" ", re.sub(r"//[^\n]*|/\*.*?\*/", " ", code, flags=re.S))


def _rewrite(path: Path, text: str) -> None:
    """`path.write_text(text, encoding="utf-8")`, the same bytes, writing only from the first 4 KB block that differs.
    A compile writes the agent's version of a unit's source and an undo writes the original back: a dtk unit can be
    a megabyte, and only the part from the changed function on is new. An unchanged file is only touched, so a build
    still sees it as new."""
    data = text.replace("\n", os.linesep).encode("utf-8")  # as text mode writes it
    try:
        with open(path, "r+b") as f:
            old = f.read()
            if old == data:
                os.utime(path)
                return
            at = 0
            while old[at:at + 4096] == data[at:at + 4096]:
                at += 4096
            f.seek(at)
            f.write(data[at:])
            f.truncate()
    except FileNotFoundError:
        path.write_bytes(data)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "fork"


def objdiff_diff(fp: ForkProject, unit, symbol: str, target_only: bool = False):
    from ..forks import objdiff

    if not target_only:
        return fp.diff(unit, symbol)
    return objdiff.diff(unit.target, None, symbol, cwd=fp.root, options=fp.diff_options(unit))


def scaled(fuzzy: float | None, n_insns: int) -> int:
    """objdiff's match percentage as a score on the engine's scale (0 = matched)."""
    if fuzzy is None:
        return n_insns * 100
    return 0 if fuzzy >= 100 else max(1, round((1 - fuzzy / 100) * n_insns * 100))


class _TreeLock:
    """Work on different source files runs side by side; a change any file may depend on (a header) runs alone.
    A thread that holds it exclusively may take it again either way."""

    def __init__(self):
        self._cond = threading.Condition()
        self._readers = 0
        self._writer: int | None = None
        self._waiting = 0
        self._mine = threading.local()

    @contextmanager
    def shared(self):
        me = threading.get_ident()
        depth = getattr(self._mine, "depth", 0)
        if self._writer == me or depth:
            self._mine.depth = depth + 1
            try:
                yield
            finally:
                self._mine.depth -= 1
            return
        with self._cond:
            while self._writer is not None or self._waiting:
                self._cond.wait()
            self._readers += 1
        self._mine.depth = 1
        try:
            yield
        finally:
            self._mine.depth = 0
            with self._cond:
                self._readers -= 1
                self._cond.notify_all()

    @contextmanager
    def exclusive(self):
        me = threading.get_ident()
        if self._writer == me:
            yield
            return
        if getattr(self._mine, "depth", 0):
            raise RuntimeError("a shared holder can't take the tree exclusively")
        with self._cond:
            self._waiting += 1
            while self._writer is not None or self._readers:
                self._cond.wait()
            self._waiting -= 1
            self._writer = me
        try:
            yield
        finally:
            with self._cond:
                self._writer = None
                self._cond.notify_all()


def _tail(log: str, lines: int = 14) -> str:
    """The end of a build log, where the reason is."""
    return "\n".join(log.strip().splitlines()[-lines:])


def with_constant_values(rows: list, console, fp) -> list:
    """A diff's differing rows with the values of the float constants the target loads by address (x86's
    `mulss xmm0, [_D_00412340]`: "; target's D_00412340 = 0.0174532924f"), read from the user's copy: the agent
    sees the literal to write instead of an address. Rows on processors without a reader stay as they are."""
    reader = arch.get(console.arch).float_operands
    if reader is None:
        return rows
    order = ">" if console.big_endian else "<"
    out = []
    for mark, target, yours in rows:
        if mark != " " and target:
            said = []
            for addr, kind in reader(target):
                raw = console.data_bytes(fp, addr, 4 if kind == "f" else 8)
                if raw:
                    value = struct.unpack(order + kind, raw)[0]
                    said.append(f"D_{addr:08X} = {value:.9g}f" if kind == "f" else f"D_{addr:08X} = {value:.17g}")
            if said:
                yours = f"{yours}   ; target's {', '.join(said)}"
        out.append((mark, target, yours))
    return out


def _floor_pct(pct: float, matched: bool) -> float:
    """For display: 100 only for a match (99.96 or code whose constants differ reads 99.9)."""
    return 100.0 if matched else min(99.9, int(pct * 10) / 10)


def _digest(code: str) -> str:
    """A function's text, whitespace aside."""
    return hashlib.sha1(re.sub(r"\s+", " ", code).strip().encode()).hexdigest()


def _set_aside(fp: ForkProject) -> dict[str, str]:
    """Symbol -> why it's set aside, for functions a project says aren't the game's own (library code, middleware,
    an SDK, the C runtime): config/<version>/set_aside.json, or the file the manifest names (`set_aside`)."""
    manifest = fp.manifest or {}
    rel = manifest.get("set_aside") or (f"config/{manifest['version']}/set_aside.json" if manifest.get("version") and (
        fp.root / "config" / str(manifest["version"]) / "set_aside.json").is_file() else None)
    if not rel:
        return {}
    try:
        data = json.loads((fp.root / rel).read_text(encoding="utf-8"))
        info = json.loads((fp.root / rel).with_name("functions.json").read_text(encoding="utf-8")) \
            if isinstance(data.get("ranges"), list) else {}
    except (OSError, ValueError, AttributeError):
        return {}
    return objdiff.expand_set_aside(data, info)


def _matched_in(rep: dict, broken: set[str] = frozenset()) -> set[tuple[str, str]]:
    """(unit, function) pairs a progress report counts as matched; units that don't build are measured from an old
    object, so nothing in them counts."""
    return {(u["name"], f["name"]) for u in rep.get("units", []) if u["name"] not in broken
            for f in u.get("functions", []) if (f.get("fuzzy_match_percent") or 0) >= 100}


_INCLUDE_LINE = re.compile(r'^#\s*include\s*"[^"]+"\s*$')
_ASM_KEYS = ("asm_twin", "asm_code")  # meta saying a function is hand-written assembly (kept by sync)
# kept by sync too: the agents' verdicts that no C makes it (red in the map), and the user giving it back to them
# (or the next check would set it aside again)
_KEPT_KEYS = (*_ASM_KEYS, "not_c", "reopened")
_ASM_DEF = re.compile(r"\s*(?:(?:static|inline|extern)\s+)*asm\b")
_MSVC_C = re.compile(r"[_@]([A-Za-z_]\w*?)(?:@\d+)?")  # how MSVC decorates a C function's name (PC, Xbox)
# a source file that includes another source file: Mario Party 6's REL runtime.c files are one line,
# #include "src/Runtime.PPCEABI.H/runtime.c" (its asm helpers, which every module links a copy of)
_SOURCE_INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"]+\.(?:c|cc|cp|cpp))"', re.M)
_PRAGMA = re.compile(r"^#\s*pragma\b")
TRY_ALL_SECONDS = 120  # try_compiler "all" stops trying more versions after this (Codex gives a tool call 180)


def _around(source: str, span: tuple[int, int], file_text: str, rel: str) -> tuple[list[str], list[str], list[str]]:
    """What an agent sends around the one definition, as (includes, lines before it, lines after it): project
    includes, prototypes and extern declarations, `#pragma` lines (push/pop wrappers). Lines the file has already
    are left out (a wrapper's pragmas aren't: they belong to this function). Anything else (a struct, a global,
    another function) isn't placed: edit_file is for that, and this says so."""
    have = {ln.strip() for ln in file_text.splitlines()}
    clean = csource._blank(source)

    def lines(lo: int, hi: int) -> list[str]:
        return [real.strip() for real, c in zip(source[lo:hi].splitlines(), clean[lo:hi].splitlines()) if c.strip()]

    includes, before, after, other = [], [], [], []
    for ln in lines(0, span[0]):
        if _INCLUDE_LINE.match(ln):
            if ln not in have:
                includes.append(ln)
        elif _PRAGMA.match(ln):
            before.append(ln)
        elif drafts._DECL.match(ln):
            if ln not in have:
                before.append(ln)
        elif ln not in have:
            other.append(ln)
    for ln in lines(span[1], len(source)):
        if _PRAGMA.match(ln):
            after.append(ln)
        elif ln not in have:
            other.append(ln)
    if other:
        listing = "\n".join(other[:6]) + ("\n..." if len(other) > 6 else "")
        raise NetworkError(f"compile_and_diff takes one function definition, plus any #include \"...\", one-line "
                           f"prototypes, extern declarations or #pragma lines it needs (those are placed for you). These "
                           f"lines aren't any of that and aren't in {rel}:\n{listing}\nHelper functions, structs and "
                           f"the prototypes you want shared go in through edit_file (a header, or the top of {rel}), not "
                           "inside compile_and_diff's source: edit_file them first, then compile_and_diff the function "
                           "alone. Nothing was compiled.")
    return includes, before, after


def _headers_defining(fp: ForkProject, rel: str, include_lines: list[str], names: set[str]) -> list[str]:
    """Of a file's #include "..." lines, those whose header defines one of `names` (a type, macro or function),
    looked up where the project's builds look: include/, the file's own folder, src/, the project root."""
    out = []
    for line in include_lines:
        m = re.search(r'"([^"]+)"', line)
        if not m:
            continue
        for base in (fp.root / "include", (fp.root / rel).parent, fp.root / "src", fp.root):
            h = base / m.group(1)
            if not h.is_file():
                continue
            try:
                text = csource._blank(h.read_text(errors="replace", encoding="utf-8"))
            except OSError:
                break
            if any(re.search(rf"(?:struct|union|enum)\s+{re.escape(n)}\b|\}}\s*{re.escape(n)}\s*;|#\s*define\s+{re.escape(n)}\b|"
                             rf"\b{re.escape(n)}\s*\(", text) for n in names):
                out.append(line)
            break
    return out


def _data_symbols(target: Path | None) -> set[str]:
    """The data symbols of a target object: objdiff's report counts data in a code section as a function (a DS
    program's BuildInfo, the SDK's build information with its RSA text, sits in .text), and agents tried to write it
    as one. Empty when it can't be read (a COFF object: PC, Xbox)."""
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile

    try:
        with open(target, "rb") as f:
            symtab = ELFFile(f).get_section_by_name(".symtab")
            return {s.name for s in symtab.iter_symbols() if s["st_info"]["type"] == "STT_OBJECT"} if symtab else set()
    except (OSError, TypeError, ELFError):
        return set()


_QUOTED_INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)
MAX_HEADERS = 64  # headers permuter_header follows from one file


def _without_bodies(text: str) -> str:
    """A C file with each function's body cut to `;` (its definition becomes its prototype): its declarations, as
    a parser needs them to read one of its functions."""
    clean, out, i, depth, start = csource._blank(text), [], 0, 0, None
    for k, c in enumerate(clean):
        if c == "{":
            if depth == 0 and clean[:k].rstrip().endswith(")"):  # a body, not a struct's or an initializer's
                start = k
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                out += [text[i:start], ";"]
                i, start = k + 1, None
    return "".join(out) + text[i:]


def _type_blocks(text: str) -> list[tuple[set[str], str]]:
    """The file-scope type definitions of a C file: (the names they define, their text)."""
    clean = csource._blank(text)
    out, i = [], 0
    pattern = re.compile(r"^[ \t]*(typedef\b|struct\s+\w+\s*\{|union\s+\w+\s*\{|enum\s*\w*\s*\{)", re.M)
    depth_at = []
    depth = 0
    for k, c in enumerate(clean):  # depth before each character
        depth_at.append(depth)
        depth += (c == "{") - (c == "}")
    for m in pattern.finditer(clean):
        if m.start() < i or depth_at[m.start()] != 0:
            continue
        j, d = m.start(), 0
        while j < len(clean):  # to the `;` that ends it at depth 0
            c = clean[j]
            d += (c == "{") - (c == "}")
            if c == ";" and d == 0:
                break
            j += 1
        block = text[m.start():j + 1]
        names = set(re.findall(r"(?:struct|union|enum)\s+(\w+)", block[:block.find("{")] if "{" in block else block))
        tail = block[block.rfind("}") + 1:] if "}" in block else block
        names |= {n for n in re.findall(r"\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*[;,)]", tail)
                  if n not in ("typedef", "struct", "union", "enum", "const", "volatile")}
        if names:
            out.append((names, block.strip()))
        i = j + 1
    return out


def _file_scope_lines(text: str) -> list[str]:
    """The lines of a C file outside every function body and struct (brace depth 0), stripped."""
    clean, depth, out = csource._blank(text), 0, []
    for real, c in zip(text.splitlines(), clean.splitlines()):
        if depth == 0 and c.strip() and "{" not in c and "}" not in c:
            out.append(real.strip())
        depth = max(0, depth + c.count("{") - c.count("}"))
    return out


def _pragmas_around(text: str, lo: int, hi: int) -> tuple[int, int]:
    """Widen a definition's span over the #pragma lines right above and below it (its wrapper), and the comment
    between them and the definition (a polish puts one there: stopping at it left the file's #pragma push behind, never
    popped, when the next rewrite came without the comment)."""
    start = above = csource.comment_above(text, lo)
    while start > 0:
        prev = text.rfind("\n", 0, start - 1) + 1
        line = text[prev:start].strip()
        if not _PRAGMA.match(line):
            break
        start = prev
    start = start if start < above else lo
    end = hi
    while end < len(text):
        nl = text.find("\n", end)
        nl = len(text) if nl < 0 else nl
        line = text[end:nl].strip()
        if line and not _PRAGMA.match(line):
            break
        if not line and end != hi:
            break
        end = nl + 1 if nl < len(text) else nl
    return start, end


def _with_includes(text: str, includes: list[str], prelude: str = "") -> str:
    """The file with new #include lines after its last one (or at the top), and `prelude` (type definitions)
    after those."""
    if not includes and not prelude:
        return text
    last = None
    for m in re.finditer(r"^[ \t]*#\s*include\b[^\n]*\n", text, re.M):
        last = m
    block = "".join(ln + "\n" for ln in includes) + (prelude.rstrip("\n") + "\n\n" if prelude else "")
    return text[:last.end()] + block + text[last.end():] if last else block + text


def _renamed_note(d) -> str:
    """objdiff's rules accept a reference to a differently named symbol; the game's link doesn't."""
    if not d.renamed:
        return ""
    pairs = ", ".join(f"`{mine}` (the original uses `{orig}`)" for orig, mine in d.renamed[:5])
    return (f"\n⚠️ Some references use other names than the original: {pairs}. objdiff counts the match, but the "
            "game links by name: use the original's names (or rename the symbol everywhere) so the code links.")


class ForkEngine:
    def __init__(self, net):
        self.net = net
        self._trees: dict[int, _TreeLock] = defaultdict(_TreeLock)  # project id -> its source tree's lock
        self._unit_locks: dict[tuple[int, str], threading.RLock] = defaultdict(threading.RLock)
        self._build_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)  # one build at a time per tree
        self._git_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
        self._mlock = threading.Lock()
        # (project id, unit) -> (sha1 of its source, base object mtime, {function: match %}): what objdiff said
        # the last time it measured the unit, so a compile doesn't need a second objdiff run to know the baseline
        self._scores: dict[tuple[int, str], tuple[str, int | None, dict]] = {}
        self._index: dict[int, dict] = {}  # project id -> {unit: {"mtime", "order", "ops"}}
        self._indexing: set[int] = set()
        self._index_dirty: set[int] = set()  # projects whose index has entries index.json doesn't have yet
        self._ilock = threading.Lock()
        self._linking: dict[str, dict] = {}  # slug -> background link run
        self._preflight_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
        self._preflighted: set[int] = set()  # projects checked since the app started (overview's check)

    # ------------------------------------------------------------------ pre-flight

    def preflight(self, slug: str, log=lambda *_: None) -> str | None:
        """Whether the project builds on this computer before helpers spend compiles on it: its setup finished and
        build.ninja's programs exist (ForkProject.build_problem). When they don't, the setup's configure step runs
        again (a project folder moved from another home); what's still wrong is kept on the project (its overview
        shows it) and returned. None: it builds."""
        proj, fp, cfg = self.get(slug)
        if not cfg.get("ready"):
            return None  # not set up yet: Setup says so
        with self._preflight_locks[proj["id"]]:
            self._preflighted.add(proj["id"])
            why = fp.build_problem()
            if why is not None and (fp.root / "objdiff.json").exists():
                log(f"{slug}: {why}; running its setup's configure step again")
                ok, tail = fp.reconfigure(cfg.get("version"), log=log)
                why = fp.build_problem() or (None if ok else f"its setup failed: {_tail(tail, 4)}")
                if why is None:
                    self.net.bump_generation(proj["id"])
            if self.get(slug)[2].get("build_problem") != why:
                self.net._update_cfg(slug, lambda c: c.__setitem__("build_problem", why))
        return why

    def _preflight_once(self, slug: str) -> None:
        """The first look at a project in this run of the app (its page opened): pre-flight it in the background."""
        try:
            pid = self.net._project(slug)["id"]
        except NetworkError:
            return
        if pid in self._preflighted:
            return
        self._preflighted.add(pid)

        logger = logging.getLogger("decompdecomp")

        def run() -> None:
            try:
                self.preflight(slug, log=lambda m: logger.info("%s", m))
            except Exception:  # the page works without it
                logger.exception("pre-flight of %s failed", slug)
        threading.Thread(target=run, daemon=True, name=f"preflight-{slug}").start()

    # ------------------------------------------------------------------ locks, builds, baselines

    def _tree(self, proj) -> _TreeLock:
        with self._mlock:
            return self._trees[proj["id"]]

    def _unit_lock(self, proj, unit: str) -> threading.RLock:
        with self._mlock:
            return self._unit_locks[(proj["id"], unit)]

    @contextmanager
    def _units_locked(self, proj, names: list[str]):
        with ExitStack() as stack:
            for n in sorted(set(names)):
                stack.enter_context(self._unit_lock(proj, n))
            yield

    def _build_unit(self, proj, fp: ForkProject, unit) -> tuple[bool, str]:
        """Build one unit's object. Builds share the tree's build files (ninja's log), so they take turns."""
        with self._build_locks[proj["id"]]:
            return fp.build_unit(unit)

    def _build_all(self, proj, fp: ForkProject, failed: list[str] | None = None) -> tuple[bool, str]:
        with self._build_locks[proj["id"]]:
            return fp.build_all(failed=failed)

    def _keep_scores(self, proj, unit, text: str, scores: dict) -> None:
        mtime = unit.base.stat().st_mtime_ns if unit.base is not None and unit.base.exists() else None
        self._scores[(proj["id"], unit.name)] = (hashlib.sha1(text.encode()).hexdigest(), mtime, dict(scores))

    def _last_scores(self, proj, unit) -> dict | None:
        hit = self._scores.get((proj["id"], unit.name))
        return hit[2] if hit else None

    def _baseline(self, proj, fp: ForkProject, unit, text: str) -> tuple[dict | None, bool]:
        """({function: match %} for the unit as its source file is now, whether that builds). Scores are None when
        the file doesn't build or objdiff can't measure it."""
        hit = self._scores.get((proj["id"], unit.name))
        if (hit and unit.base is not None and unit.base.exists() and hit[0] == hashlib.sha1(text.encode()).hexdigest()
                and hit[1] == unit.base.stat().st_mtime_ns):
            return hit[2], True
        ok, _ = self._build_unit(proj, fp, unit)  # the object may be older than the file
        if not ok or unit.base is None or not unit.base.exists():
            return None, False
        try:
            d = fp.diff(unit, None)
        except (ObjdiffError, ForkError):
            return None, True
        self._keep_scores(proj, unit, text, d.unit_scores)
        return d.unit_scores, True

    def _commit(self, proj, fp: ForkProject, rel: str, message: str) -> None:
        """Commit one file (agents work side by side: never sweep up another one's change)."""
        with self._git_locks[proj["id"]]:
            fp.git("add", "--", rel)
            fp.git("commit", "-q", "-m", message, "--", rel)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def is_fork(proj) -> bool:
        return json.loads(proj["config"]).get("kind") == "fork"

    def get(self, slug: str):
        proj = self.net._project(slug)
        if not self.is_fork(proj):
            raise NetworkError(f"{slug} isn't a fork")
        return proj, ForkProject(Path(proj["root"])), json.loads(proj["config"])

    def _save_cfg(self, proj, cfg: dict) -> None:
        with self.net.store.tx() as db:
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))

    @staticmethod
    def _meta(fn) -> dict:
        return json.loads(fn["meta"]) if fn["meta"] else {}

    # ------------------------------------------------------------------ creating a fork

    def policy(self, repo: str) -> dict:
        """What a public decomp says about AI and contributions (forks/policy.py), before it's forked."""
        from ..forks import policy

        m = _REPO.match(repo.strip())
        if not m:
            raise NetworkError("give a GitHub/GitLab/Codeberg repository URL (https://github.com/owner/repo)")
        return {"repo": m.group(1), **policy.check(m.group(1))}

    def clone(self, repo: str, *, name: str = "", platform: str = "", version: str | None = None, log=print) -> str:
        repo = repo.strip()
        local = Path(repo).expanduser()
        if not (_REPO.match(repo) or (local.is_dir() and (local / ".git").exists())):
            raise NetworkError("give a GitHub/GitLab/Codeberg repository URL (https://github.com/owner/repo)")
        if not shutil.which("git"):
            raise NetworkError("forking needs git: install it from https://git-scm.com")
        url = _REPO.match(repo).group(1) if _REPO.match(repo) else str(local.resolve())
        for p in self.net.store.all("SELECT slug, config FROM projects"):
            if json.loads(p["config"]).get("repo_url") == url:
                log(f"already forked as {p['slug']}")
                return p["slug"]
        base = _slug(name or url.rstrip("/").split("/")[-1])
        slug = self._free_slug(base)
        dest = self.net.workspace / slug
        log(f"cloning {url}")
        res = subprocess.run(["git", "clone", "--depth", "1", "--recurse-submodules", "--shallow-submodules", url, str(dest)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                             creationflags=NO_WINDOW)
        if res.returncode != 0:
            from ..forks import policy
            shutil.rmtree(dest, ignore_errors=True)
            raise NetworkError(policy.git_trouble(res.stderr))
        fp = ForkProject(dest)
        fp.git("checkout", "-q", "-b", "decomp-wtf")
        kind = fp.kind()
        versions = fp.versions()
        version = version if version in versions else (versions[0] if versions else version)
        cfg = {"kind": "fork", "repo_url": url, "platform": platform, "version": version, "fork_kind": kind,
               "ready": False, "legal": {"basis": "personal-copy", "license": "the project's own license"}}
        self._register(slug, name or url.rstrip("/").split("/")[-1], f"fork of {url}", dest, cfg, f"cloned {url}")
        log(f"cloned into {dest} ({kind} project)")
        return slug

    def _free_slug(self, base: str) -> str:
        slug, n = base, 2
        while self.net.store.one("SELECT 1 FROM projects WHERE slug=?", slug) or (self.net.workspace / slug).exists():
            slug, n = f"{base}-{n}", n + 1
        return slug

    def _register(self, slug: str, title: str, description: str, root: Path, cfg: dict, event: str) -> None:
        with self.net.store.tx() as db:
            pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                             (slug, title, description, "", 0, str(root), json.dumps(cfg))).lastrowid
            db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                       "VALUES(?,?,?,?,?,?,?)", (pid, 1, "", None, "projects keep their headers in the tree", time.time(), ""))
            self.net._event(db, pid, None, "fork", event)

    def create_scratch(self, game: Path, console: consoles.Console, *, compiler: str | None = None, log=print,
                       scope: str | None = None) -> str:
        """A game of a PROJECT-mode console (GameCube, Wii, PC, DS), from scratch: the console generates the
        community's kind of project for it (Console.create_project: dtk-template; for PC, decompdecomp's own from
        Ghidra's analysis; for DS, ds-decomp's), opened like a fork. `scope`: what the project covers (SCOPES), for
        a console whose import sets library code aside (Console.sets_aside); kept in the project's manifest."""
        if scope is not None and scope not in SCOPES:
            raise NetworkError(f"a project covers the game's own code ({GAME!r}) or the whole program, not {scope!r}")
        scoped = {"scope": scope or GAME} if console.sets_aside else {}
        info = console.game_info(game)
        sha1 = info["sha1"]
        for p in self.net.store.all("SELECT slug, config FROM projects"):
            if json.loads(p["config"]).get("program_sha1") == sha1:
                log(f"already set up as {p['slug']}")
                return p["slug"]
        slug = self._free_slug(_slug(f"{info['title']} {info['id']}" if info.get("kind") != "dol" else info["title"]))
        dest = self.net.workspace / slug
        try:
            facts = console.create_project(dest, game, compiler=compiler, log=log, **scoped)
            if scoped:  # (before the first measuring, which reads it)
                ForkProject(dest).update_manifest(**scoped)
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        cfg = {"kind": "fork", "origin": "scratch", "repo_url": None, "platform": facts["platform"],
               "version": facts["id"], "fork_kind": "manifest", "ready": False, "compiler": facts["compiler"],
               "program_sha1": sha1,
               "legal": {"basis": "personal-copy", "license": "personal use only",
                         "statement": "imported by the owner of this copy of the game, who confirmed it was legally obtained",
                         "confirmed_at": time.time()}}
        self._register(slug, facts["title"], f"{consoles.name(facts['platform'])} game {facts['id']}, from scratch",
                       dest, cfg, f"started {facts['id']} from scratch")
        self.check(slug, log=log)
        return slug

    def needs(self, slug: str) -> dict:
        _, fp, cfg = self.get(slug)
        return fp.needs(cfg.get("version")) | {"kind": fp.kind(), "versions": fp.versions(),
                                                "version": cfg.get("version"), "readme": fp.readme()}

    def setup(self, slug: str, game: bytes | Path | None, filename: str = "", log=print, move: bool = False) -> dict:
        """Add the user's game (bytes, or a file on disk) and run the project's setup, then measure it."""
        proj, fp, cfg = self.get(slug)
        if game is not None:
            if isinstance(game, (bytes, bytearray)):
                tmp = fp.root / STATE / "upload.tmp"
                tmp.write_bytes(game)
                game, move = tmp, True
            self._match_version(proj, fp, cfg, Path(game), log)
            if not cfg.get("program_sha1"):  # which game it is: members of a room on it need the same one
                from .forkroom import game_identity

                try:
                    cfg["game_sha1"] = game_identity(Path(game))
                    self._save_cfg(proj, cfg)
                except (OSError, ForkError):
                    pass
            dest = fp.place_game(Path(game), filename or Path(game).name, cfg.get("version"), move=move)
            log(f"your copy of the game is at {dest.relative_to(fp.root)}")
        if not fp.needs(cfg.get("version"))["auto"]:
            raise NetworkError("this project has its own setup: follow its README, then click Check again")
        fp.setup(cfg.get("version"), log=log)
        return self.check(slug, log=log)

    def _match_version(self, proj, fp: ForkProject, cfg: dict, game: Path, log) -> None:
        """A GameCube/Wii disc names its version (GP6E01, GP6P01...): use the project's matching version, or say
        which ones the project has, instead of failing deep in the build."""
        if fp.kind() != "dtk":
            return
        versions = fp.versions()
        from ..consoles.gc_wii import scratch as dtk  # dtk projects are GameCube and Wii ones

        gid = dtk.disc_id(game)
        if not gid or not versions or gid == cfg.get("version"):
            return
        if gid in versions:
            log(f"your disc is {gid}: using the project's {gid} version")
            cfg["version"] = gid
            self._save_cfg(proj, cfg)
        elif all(re.fullmatch(r"[A-Z0-9]{6}", v) for v in versions):
            raise NetworkError(f"this disc is {gid}, but the project decompiles {', '.join(versions)}: "
                               "pick your copy of one of those versions")

    def check_rom(self, slug: str, log=print) -> dict:
        """A game started from scratch: rebuild its program and compare it with the user's copy (its console's
        check_rebuild); what it says is kept in the project's .decomp.wtf.json and shown on the project."""
        proj, fp, cfg = self.get(slug)
        manifest = fp.manifest or {}
        console = consoles.get(manifest.get("platform"))
        if cfg.get("origin") != "scratch" or consoles.REBUILD_CHECK not in console.features:
            raise NetworkError(f"{console.name or 'this'} projects started from scratch don't rebuild here")
        with self._build_locks[proj["id"]]:  # not while an agent's compile builds in the same tree
            result = console.check_rebuild(fp, manifest["version"], log=log)
        manifest["rebuild"] = result | {"t": time.time()}
        (fp.root / ".decomp.wtf.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return result

    def set_scope(self, slug: str, scope: str, log=print) -> dict:
        """What the project covers (forks/project.py SCOPES): the game's own code, or the whole program. Kept in its
        manifest, so it travels with the project's folder; measuring again then opens its set-aside functions to
        agents and counts them (or keeps them out again). Hand-written assembly stays assembly either way. The whole
        program also brings in what the project's import left unanalysed (Console.unanalysed), once. Returns the new
        measures."""
        if scope not in SCOPES:
            raise NetworkError(f"a project covers the game's own code ({GAME!r}) or the whole program, not {scope!r}")
        proj, fp, cfg = self.get(slug)
        if not fp.manifest:
            raise NetworkError("this project has no decompdecomp manifest, so it has no set-aside code to choose for")
        if fp.scope != scope:
            fp.update_manifest(scope=scope)
            with self.net.store.tx() as db:
                self.net._event(db, proj["id"], None, "scope", "this project covers the whole program now"
                                if scope != GAME else "this project covers the game's own code now")
        if scope == PROGRAM and self._unanalysed(fp, cfg) and self.analyse_unanalysed(slug, log=log) is not None:
            return self.get(slug)[2].get("measures") or {}  # (measured again with what it brought in)
        return self.sync(slug, log=log)

    def _unanalysed(self, fp: ForkProject, cfg: dict) -> str | None:
        """What the project's import left unanalysed that a new analysis can bring in (Console.unanalysed), or None.
        A console that can't tell (its program missing, say) has nothing to offer."""
        try:
            return consoles.get(cfg.get("platform")).unanalysed(fp) if fp.manifest else None
        except Exception:  # noqa: BLE001  Settings shows without it rather than failing
            return None

    def analyse_unanalysed(self, slug: str, log=print) -> str | None:
        """Bring in what the project's import left unanalysed (Console.analyse_unanalysed: a new analysis of that code
        alone, through reanalyse, keeping every match, note and name). Returns what changed (None: nothing)."""
        proj, fp, cfg = self.get(slug)
        if not self._unanalysed(fp, cfg):
            return None
        return consoles.get(cfg.get("platform")).analyse_unanalysed(self.net, slug, log=log)

    def reanalyse(self, slug: str, step, paths=("config",), fix=False, log=print) -> str | None:
        """A console's improved analysis of its games, applied to a project made before it (Console.on_start): `step`
        changes the project's `paths` (its config, its build tools; its source files keep their ranges, so the work in
        them stays) and may add files (source files for code it found), and says what it found, as a sentence, or None
        when there's nothing to change. The project is set up again and, where the
        console checks its rebuild, the game rebuilt: a rebuild identical before and not after takes the change back,
        and so does one that differs otherwise than before, unless `fix` (a fix to its build, which may mend part).
        The project's feed says what happened. Returns the sentence (None: nothing changed)."""
        proj, fp, cfg = self.get(slug)
        console = consoles.get(cfg.get("platform"))
        # the manifest's, as check_rom: the config may hold the game's id ("C32P"), and the build's folder is "c32p"
        version = (fp.manifest or {}).get("version") or cfg.get("version")
        checks = cfg.get("origin") == "scratch" and consoles.REBUILD_CHECK in console.features
        paths = [p for p in paths if (fp.root / p).exists()]  # git refuses a path the project hasn't
        def untracked() -> set[str]:
            ok, out = fp.git("ls-files", "-z", "--others", "--exclude-standard")
            return set(out.split("\0")) - {""} if ok else set()

        def undo() -> None:  # the files the step added go too (agents can't add any: the tree is the step's)
            fp.git("checkout", "HEAD", "--", *paths)
            for rel in untracked() - there:
                (fp.root / rel).unlink(missing_ok=True)

        with self._tree(proj).exclusive(), self._build_locks[proj["id"]]:
            before = (fp.manifest or {}).get("rebuild") or {}
            there = untracked()
            try:
                said = step(fp)
            except (ForkError, NetworkError, OSError) as e:
                undo()
                said, kept = f"a new analysis of the game couldn't be applied, and the project stays as it was: {e}", False
            else:
                if said is None:
                    return None
                setup = [fp.run(cmd, log=log)[0] for cmd in fp.setup_commands(version)]
                after = console.check_rebuild(fp, version, log=log) if checks and all(setup) else {"identical": None}
                # the config says more about the same bytes: a rebuild that differs where it didn't is its doing
                kept = all(setup) and (not checks or after.get("identical") or not before.get("identical") and (
                    fix or after.get("detail") == before.get("detail")))
                if not kept:
                    undo()
                    for cmd in fp.setup_commands(version):
                        fp.run(cmd, log=log)
                    said = (f"a new analysis of the game ({said}) was taken back: the game "
                            f"{'no longer rebuilt byte for byte' if all(setup) else 'no longer built'} with it")
                else:
                    if checks:  # what the project shows (check_rom)
                        manifest = json.loads((fp.root / ".decomp.wtf.json").read_text(encoding="utf-8"))
                        manifest["rebuild"] = after | {"t": time.time()}
                        (fp.root / ".decomp.wtf.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                    if added := paths + sorted(untracked() - there):  # (none: `git commit --` takes what's staged)
                        self._commit_paths(proj, fp, added, f"Analyse the game again: {said}\n\nBy decompdecomp.")
        if kept:
            self.sync(slug, log=log)
        with self.net.store.tx() as db:
            self.net._event(db, proj["id"], None, "analysis", said)
        log(said)
        return said if kept else None

    def check(self, slug: str, log=print) -> dict:
        """After a manual setup (or to refresh): pick the project up once it has an objdiff.json."""
        proj, fp, cfg = self.get(slug)
        if not (fp.root / "objdiff.json").exists():
            raise NetworkError("no objdiff.json yet. The project's build creates it (often `python configure.py` "
                               "or `make`); see its README")
        cfg["ready"] = True
        self._save_cfg(proj, cfg)
        measures = self.sync(slug, log=log)
        if fp.linkable():
            self.link_soon(slug)  # files that are finished but not linked yet (in the project as it came, too)
        return measures

    def sync(self, slug: str, log=print, rep: dict | None = None, broken: set[str] = frozenset(),
             agent: Agent | None = None) -> dict:
        """Measure the whole project with objdiff (or take a report just made) and update every function's state.
        Functions in `broken` units (sources that don't build) don't count as matched. Functions that match now and
        didn't before are recorded as matches (credited to `agent`, whose change it was)."""
        proj, fp, cfg = self.get(slug)
        if rep is None:
            log("measuring progress with objdiff")
            try:
                with self._tree(proj).exclusive():  # never read an object while a build writes it
                    rep = fp.report()
            except (ObjdiffError, ForkError) as e:
                raise NetworkError(f"objdiff couldn't measure the project: {e}") from None
        units = {u.name: u for u in fp.units()}
        data = {n: _data_symbols(u.target) for n, u in units.items()}
        rows = [(u["name"], f) for u in rep.get("units", []) for f in u.get("functions", [])
                if f["name"] not in data.get(u["name"], ())]
        listed = _set_aside(fp)
        aside = listed if fp.scope == GAME else {}  # the whole program: set-aside code is worked on and counted
        # real names a report doesn't give (a PC game's stay _func_0041B470@8): how the source spells them
        reals = consoles.get(cfg.get("platform")).real_names(fp, (fp.manifest or {}).get("version") or cfg.get("version"))
        counts: dict[str, int] = defaultdict(int)
        for _, f in rows:
            counts[f["name"]] += 1
        all_rows = self.net.store.all("SELECT * FROM functions WHERE project_id=?", proj["id"])
        existing = {(r["unit"], self._meta(r).get("symbol"), self._meta(r).get("nth", 0)): r for r in all_rows}
        used = {r["name"] for r in all_rows}
        nth: dict[tuple[str, str], int] = defaultdict(int)  # a unit can hold two functions of the same name
        held = {u: self._held(proj, u) for u in {r["unit"] for r in all_rows if '"held"' in (r["meta"] or "")}}
        texts: dict[str, str | None] = {}  # unit -> its source, for functions that match now
        # a function whose code moved to another file of its module (a split) keeps its row: attempts, notes, claims
        here: set[tuple[str, str, int]] = set()
        seen: dict[tuple[str, str], int] = defaultdict(int)
        for uname, f in rows:
            here.add((uname, f["name"], seen[(uname, f["name"])]))
            seen[(uname, f["name"])] += 1
        moved: dict[tuple[str, str], list] = defaultdict(list)  # (module, symbol) -> rows no unit has any more
        for key, r in existing.items():
            if key not in here and key[0]:
                moved[(key[0].split("/", 1)[0], key[1])].append(r)
        with self.net.store.tx() as db:
            for uname, f in rows:
                sym = f["name"]
                k = nth[(uname, sym)]
                nth[(uname, sym)] += 1
                fuzzy = f.get("fuzzy_match_percent") if uname not in broken else None
                size = int(f.get("size", 0))
                n = max(1, size // 4)
                u = units.get(uname)
                md = f.get("metadata") or {}
                meta = {"symbol": sym, "demangled": md.get("demangled_name"), "source": u.source if u else None}
                if k:
                    meta["nth"] = k
                if real := reals.get(sym):
                    meta["real"] = real
                old = existing.get((uname, sym, k))
                if old is None and moved.get((uname.split("/", 1)[0], sym)):
                    old = moved[(uname.split("/", 1)[0], sym)].pop(0)
                    name = sym if counts[sym] == 1 else f"{sym}@{uname}" + (f"#{k + 1}" if k else "")
                    used.discard(old["name"])
                    while name in used:
                        name += "'"
                    used.add(name)
                    db.execute("UPDATE functions SET unit=?, name=? WHERE id=?", (uname, name, old["id"]))
                if old is not None and "held" in self._meta(old):
                    meta["held"] = self._meta(old)["held"]
                    if sym in held.get(uname, ()):
                        fuzzy = None if fuzzy is None else min(fuzzy, 99.99)
                if old is not None:  # its code grew (pieces joined to it): why it was set aside was about a piece
                    was = self._meta(old)
                    kept = _KEPT_KEYS if old["size"] == size else ("reopened",)
                    meta.update({k: v for k, v in was.items() if k in kept
                                 and not (k == "asm_code" and was.get("set_aside"))})  # (set aside: decided below)
                if sym in aside:  # code the project says isn't the game's own (library code, middleware)
                    meta["asm_code"] = aside[sym]
                    meta["set_aside"] = True  # and progress takes it off, as no C code is (network.SET_ASIDE)
                status = "matched" if fuzzy is not None and fuzzy >= 100 else "open" if u and u.source else "nosource"
                if status == "open" and any(meta.get(k) for k in _ASM_KEYS):
                    status = "asm"  # hand-written assembly (here or in its twin elsewhere): not for agents
                score = None if fuzzy is None else scaled(fuzzy, n)
                vram = md.get("virtual_address")
                if old is None:
                    name = sym if counts[sym] == 1 else f"{sym}@{uname}" + (f"#{k + 1}" if k else "")
                    while name in used:
                        name += "'"
                    used.add(name)
                    db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,best_score,unit,vram,meta,asm) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?)", (proj["id"], name, size, n, status, score, uname,
                                                              int(vram) if vram else None, json.dumps(meta), ""))
                else:
                    # a better attempt kept elsewhere stays the best; a 0 (identical) of a function that no longer
                    # matches doesn't: the queue and briefs would say it's done
                    if status != "matched" and old["best_source"] and old["best_score"] and old["status"] != "matched":
                        score = old["best_score"] if score is None else min(score, old["best_score"])
                    # only what changed: SQLite writes a row's pages even when its values are the same, and a
                    # GameCube game's 34,000 functions rewritten on every sync were most of what the app wrote
                    if (old["size"], old["n_insns"], old["status"], old["best_score"], self._meta(old)) != \
                            (size, n, status, score, meta):
                        db.execute("UPDATE functions SET size=?, n_insns=?, status=?, best_score=?, meta=? WHERE id=?",
                                   (size, n, status, score, json.dumps(meta), old["id"]))
                    if old["status"] == "matched" and status != "matched":
                        self._reopened(db, proj, old, "full report" + (", the unit doesn't build" if uname in broken
                                                                         else ""), fuzzy,
                                       held=fuzzy is not None and sym in held.get(uname, ())
                                       and float(f.get("fuzzy_match_percent") or 0) >= 100)
                    if status == "matched" and old["status"] != "matched":
                        if uname not in texts:
                            try:
                                texts[uname] = (fp.root / u.source).read_text(errors="replace",
                                                                              encoding="utf-8") if u and u.source else None
                            except OSError:
                                texts[uname] = None
                        self._matched(db, proj, old, agent, texts[uname])
            gone = [r["id"] for rs in moved.values() for r in rs]  # functions no unit has any more
            if gone:
                with_history = {r["function_id"] for r in db.execute(
                    f"SELECT DISTINCT function_id FROM attempts WHERE function_id IN ({','.join('?' * len(gone))})", gone)}
                for fid in gone:
                    if fid in with_history:
                        db.execute("UPDATE functions SET status='nosource', meta=json_set(COALESCE(meta, '{}'), "
                                   "'$.gone', 1) WHERE id=?", (fid,))  # (network.IN_BUILD)
                    else:
                        for table in ("claims", "notes"):
                            db.execute(f"DELETE FROM {table} WHERE function_id=?", (fid,))
                        db.execute("DELETE FROM functions WHERE id=?", (fid,))
            m = objdiff.game_measures(rep, set(aside))  # % of the game's own code: set-aside code doesn't count
            self.net._event(db, proj["id"], None, "sync", f"{float(m.get('matched_code_percent', 0)):.2f}% of code matches")
        log(f"{m.get('matched_functions', 0)}/{m.get('total_functions', 0)} functions match, "
            f"{float(m.get('matched_code_percent', 0)):.2f}% of code")
        cfg["measures"] = m
        cfg["uncovered"] = self._count_uncovered(rep)
        # what the project's set-aside list names, whatever its scope: the Settings panel's choice says how many
        mine = [uname for uname, f in rows if f["name"] in listed]
        cfg["set_aside"] = {"functions": len(mine), "without_source": sum(
            1 for uname in mine if not (units.get(uname) and units[uname].source))} if mine else None
        self._save_cfg(proj, cfg)
        if cfg.get("ready"):
            self.start_indexing(slug)
        return m

    # ------------------------------------------------------------------ linking
    # The end of a decomp is not a matching function but a linked file: configure.py marks it Matching, the game is
    # built from its C and checked against its checksums. decompdecomp does that for every file the moment its code and
    # data match, and keeps it only when the whole game still builds byte for byte.

    @staticmethod
    def _finished(unit: dict) -> bool:
        m = unit.get("measures") or {}
        tc, td = int(m.get("total_code") or 0), int(m.get("total_data") or 0)
        return bool(tc or td) and int(m.get("matched_code") or 0) == tc and int(m.get("matched_data") or 0) == td

    def link_candidates(self, proj, fp: ForkProject, rep: dict) -> list[str]:
        """Source files not linked yet whose every unit matches in code and data (and loads the original's constants)."""
        states = fp.link_states()
        units: dict[str, list[dict]] = defaultdict(list)
        for u in rep.get("units", []):
            src = (u.get("metadata") or {}).get("source_path")
            if src:
                units[src].append(u)
        return sorted(src for src, us in units.items() if states.get(src, ("",))[0] == "NonMatching"
                      and all(self._finished(u) and not self._held(proj, u["name"]) for u in us))

    def link(self, slug: str, log=print, agent: Agent | None = None) -> dict:
        """Link every finished file into the game, keeping only what the project's checksum check accepts."""
        proj, fp, cfg = self.get(slug)
        if not fp.linkable():
            return {"linked": [], "failed": {}, "note": "this project has no link step decompdecomp knows (dtk-template)"}
        with self._tree(proj).exclusive(), self._build_locks[proj["id"]]:
            fp.build_all()
            rep = fp.report()
            todo = self.link_candidates(proj, fp, rep)
            if not todo:
                return {"linked": [], "failed": {}}
            conf = fp.root / "configure.py"
            original = conf.read_text(errors="replace", encoding="utf-8")
            ok, out = fp.verify()
            if not ok:
                return {"linked": [], "failed": {}, "error": "the project's checksum check fails before linking "
                        f"anything, so nothing was linked:\n{_tail(out)}"}

            def build_with(sources: list[str]) -> tuple[bool, str]:
                conf.write_text(fp.marked_matching(original, sources, cfg.get("version")), encoding="utf-8")
                return fp.verify()

            log(f"linking {len(todo)} finished file(s)")
            linked: list[str] = []
            failed: dict[str, str] = {}
            ok, out = build_with(todo)
            if ok:
                linked = todo
            else:  # one at a time, so one file that doesn't link keeps none of the others out
                for src in todo[:40]:
                    ok, out = build_with([*linked, src])
                    if ok:
                        linked.append(src)
                    else:
                        failed[src] = _tail(out)
                ok, out = build_with(linked)
                if not ok:
                    linked = []
            self._remember_link_failures(proj, linked, failed)
            if not linked:
                conf.write_text(original, encoding="utf-8")
                fp.verify()
                return {"linked": [], "failed": failed}
            names = ", ".join(Path(s).name for s in linked[:6]) + (f" and {len(linked) - 6} more" if len(linked) > 6 else "")
            who = f"\n\nLinked by decompdecomp after {agent.name}'s match." if agent else "\n\nLinked by decompdecomp."
            self._commit(proj, fp, "configure.py", f"Link {names}\n\nEvery function and all data match, and the game "
                                                   f"still builds byte for byte from the C.{who}")
            with self.net.store.tx() as db:
                for src in linked:
                    self.net._event(db, proj["id"], agent.id if agent else None, "link", src)
            self._scores = {k: v for k, v in self._scores.items() if k[0] != proj["id"]}
            self.sync(slug, log=lambda *_: None, rep=fp.report())
        log(f"linked {len(linked)} file(s)" + (f"; {len(failed)} didn't link" if failed else ""))
        return {"linked": linked, "failed": failed}

    def _verify(self, proj, fp: ForkProject) -> tuple[bool, str]:
        with self._build_locks[proj["id"]]:
            return fp.verify()

    def _file_done(self, slug: str, proj, fp: ForkProject, unit, scores: dict, agent: Agent | None) -> str:
        """When every function of a file matches: link it (in the background), or say what its data still needs."""
        if (unit.complete or not unit.source or not fp.linkable() or not scores
                or any(v is None or v < 100 for v in scores.values())):
            return ""
        data = self._data_status(unit)
        self.link_soon(slug, agent)  # the checksum decides; the comparison below is what to fix if it fails
        if not data:
            return (f"\n🔗 Every function and all data in {unit.source} match: decompdecomp is linking the file into the "
                    "game now (it stays linked if the game still builds byte for byte).")
        return (f"\n🔗 Every function in {unit.source} matches. To link the file into the game (the goal: the game "
                f"built from this C), its data must match too. Fix it with edit_file, then file_status to check:\n{data}")

    def _data_status(self, unit, max_rows: int = 40) -> str:
        """The file's data sections that differ from the original's ("" when all match)."""
        if unit.target is None or not unit.target.exists():
            return ""
        try:
            return datadiff.render(datadiff.compare(unit.target, unit.base), max_rows)
        except Exception as e:  # an object pyelftools can't read: say so rather than fail the tool
            return f"(couldn't compare the data: {e})"

    def file_status(self, slug: str, path: str) -> str:
        """A source file on its way to being linked: its functions, its data next to the original's, and why the last
        link attempt failed."""
        proj, fp, cfg = self.get(slug)
        rel = path.strip().lstrip("./")
        units = fp.units_for(rel)
        if not units:  # a function name: its file
            try:
                fn = self._fn(slug, path)
                units, rel = [fp.unit(fn["unit"])], fp.unit(fn["unit"]).source or ""
            except (NetworkError, ForkError):
                raise NetworkError(f"{path} isn't a source file of a unit (or a function) in this project") from None
        state = fp.link_states().get(rel, ("",))[0]
        lines = [f"## {rel}"]
        lines.append("🔗 LINKED: the game is built from this file's C and checked byte for byte." if state == "Matching"
                     else f"not linked yet ({state or 'not in configure.py'})" if fp.linkable()
                     else "this project has no link step decompdecomp knows")
        for u in units:
            rows = self.net.store.all("SELECT name, status, n_insns FROM functions WHERE project_id=? AND unit=? "
                                      "ORDER BY vram, name", proj["id"], u.name)
            left = [r for r in rows if r["status"] != "matched"]
            lines.append(f"unit {u.name}: " + ("no functions (data only)" if not rows else
                         f"{len(rows) - len(left)}/{len(rows)} functions match")
                         + (": still to do " + ", ".join(f"{r['name']} ({r['n_insns']} insns)" for r in left[:12])
                            if left else ""))
            if state != "Matching":
                data = self._data_status(u)
                lines.append(f"data: {'matches' if not data else 'differs'}" + (f"\n{data}" if data else ""))
        try:
            mods = self._modules(fp, cfg)
            src = fp.src_dir()
            name = rel[len(src) + 1:] if rel.startswith(src + "/") else rel
            for module, mod in mods.items():
                for f in dtksplits.Splits.parse(mod.splits.read_text(encoding="utf-8")).files:
                    if f.name == name:
                        lines.append(f"{module} sections: " + ", ".join(f"{k} 0x{a:X}-0x{b:X}" for k, (a, b) in f.sections.items())
                                     + " (split_file divides it)")
        except (NetworkError, OSError):
            pass
        why = (cfg.get("link_failed") or {}).get(rel)
        if why and state != "Matching":
            lines += ["", "The last try to link it failed (the game didn't build identically):", why]
        return "\n".join(lines)

    def _remember_link_failures(self, proj, linked: list[str], failed: dict[str, str]) -> None:
        cfg = json.loads(self.net.store.one("SELECT config FROM projects WHERE id=?", proj["id"])["config"])
        known = cfg.get("link_failed") or {}
        for src in linked:
            known.pop(src, None)
        known.update(failed)
        cfg["link_failed"] = known
        self._save_cfg(proj, cfg)

    def link_soon(self, slug: str, agent: Agent | None = None) -> None:
        """Link in the background (one run per project at a time; another request while it runs runs it again)."""
        with self._mlock:
            state = self._linking.setdefault(slug, {"running": False, "again": False})
            if state["running"]:
                state["again"] = True
                return
            state["running"] = True

        def run() -> None:
            while True:
                try:
                    self.link(slug, log=lambda *_: None, agent=agent)
                except Exception:  # a background tidy-up: the next match tries again
                    pass
                with self._mlock:
                    if not state["again"]:
                        state["running"] = False
                        return
                    state["again"] = False

        threading.Thread(target=run, daemon=True, name=f"link-{slug}").start()

    # ------------------------------------------------------------------ source files for all the code
    # Code no source file covers (dtk's auto_..._text units) can't be decompiled: there's nowhere to write it. Real
    # projects split it into files by hand. decompdecomp gives each such stretch of code a file of its own, the way
    # contributors start (split_file divides one further), and keeps the change only if the game still builds byte
    # for byte. A file listed in configure.py that doesn't exist yet gets created too.

    def _modules(self, fp: ForkProject, cfg: dict) -> dict:
        if fp.kind() not in ("dtk", "manifest") or not fp.linkable():
            raise NetworkError("giving code source files works on dtk-template projects (GameCube, Wii)")
        version = cfg.get("version") or (fp.versions() or [None])[0]
        try:
            return dtksplits.modules(fp.root, version)
        except (OSError, ValueError) as e:
            raise NetworkError(f"can't read the project's config for {version}: {e}") from None

    @staticmethod
    def _text_extent(unit, syms: dict) -> tuple[int, int] | None:
        """(start, end) in .text of an auto unit's code: its first symbol's address, its object's .text size."""
        from io import BytesIO

        from elftools.elf.elffile import ELFFile

        try:
            elf = ELFFile(BytesIO(unit.target.read_bytes()))
        except (OSError, AttributeError):
            return None
        sec = elf.get_section_by_name(".text")
        tab = elf.get_section_by_name(".symtab")
        if sec is None or tab is None or not sec["sh_size"]:
            return None
        idx = next(i for i, x in enumerate(elf.iter_sections()) if x.name == ".text")
        first = min(((x["st_value"], x.name) for x in tab.iter_symbols()
                     if x["st_shndx"] == idx and x.name and x["st_info"]["type"] == "STT_FUNC"), default=None)
        if first is None or first[1] not in syms or syms[first[1]][0] != ".text":
            return None
        start = syms[first[1]][1] - first[0]
        return start, start + sec["sh_size"]

    def code_without_files(self, slug: str, rep: dict | None = None) -> dict:
        """What start_files would do: {"create": [source paths of listed files that don't exist],
        "split": [(module, source path, start, end, functions)]}."""
        proj, fp, cfg = self.get(slug)
        mods = self._modules(fp, cfg)
        src = fp.src_dir()
        if rep is None:
            try:
                rep = json.loads((fp.root / STATE / "report.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                rep = {}
        rep_fns = {u["name"]: len(u.get("functions", [])) for u in rep.get("units", [])}
        cpp = {u["name"] for u in rep.get("units", [])  # code with C++ names goes in a C++ file
               if any((f.get("metadata") or {}).get("demangled_name") not in (None, f["name"]) for f in u.get("functions", []))}
        create, split, taken = [], [], set()
        listed = fp.link_states()
        for u in fp.units():
            module, _, rest = u.name.partition("/")
            if u.source or module not in mods:
                continue
            if not u.auto:
                for f in dtksplits.Splits.parse(mods[module].splits.read_text(encoding="utf-8")).files:
                    if str(Path(f.name).with_suffix("")) == rest and f"{src}/{f.name}" in listed:
                        create.append(f"{src}/{f.name}")
                continue
            if not rest.endswith("_text") or not rep_fns.get(u.name, 1):
                continue
            extent = self._text_extent(u, dtksplits.symbols(mods[module].symbols))
            if extent is None:
                continue
            folder = "auto" if module == "main" else f"REL/{module}"
            ext = ".cpp" if u.name in cpp else ".c"
            name = f"{folder}/text_{extent[0]:08X}{ext}"
            while name in taken or (fp.root / src / name).exists() or f"{src}/{name}" in listed:
                name = name[:-len(ext)] + "_" + ext
            taken.add(name)
            split.append((module, name, extent[0], extent[1], rep_fns.get(u.name, 0)))
        return {"create": sorted(set(create)), "split": sorted(split)}

    def start_files(self, slug: str, log=print, agent: Agent | None = None) -> dict:
        """Give all code a source file: create files configure.py lists, split off a file for every stretch of
        code no file covers. Verified by the game's checksum, committed, measured."""
        proj, fp, cfg = self.get(slug)
        mods = self._modules(fp, cfg)
        src = fp.src_dir()
        conf = fp.root / "configure.py"
        with self._tree(proj).exclusive(), self._build_locks[proj["id"]]:
            fp.build_all()
            plan = self.code_without_files(slug, fp.report())
            if not plan["create"] and not plan["split"]:
                return {"created": [], "split": [], "functions": 0}
            saved = {conf: conf.read_text(encoding="utf-8")}
            made: list[Path] = []
            try:
                for path in plan["create"]:
                    p = fp.root / path
                    if not p.exists():
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_text("", encoding="utf-8")
                        made.append(p)
                by_module: dict[str, list] = defaultdict(list)
                for item in plan["split"]:
                    by_module[item[0]].append(item)
                text = saved[conf]
                for module, items in by_module.items():
                    sp_path = mods[module].splits
                    saved.setdefault(sp_path, sp_path.read_text(encoding="utf-8"))
                    sp = dtksplits.Splits.parse(saved[sp_path])
                    for _, name, start, end, _n in items:
                        sp.files.append(dtksplits.FileSplit(name, {".text": (start, end)}))
                        p = fp.root / src / name
                        p.parent.mkdir(parents=True, exist_ok=True)
                        common = '#include "common.h"\n' if (fp.root / "include" / "common.h").is_file() else ""
                        p.write_text(f"// Code of {module} that had no source file yet: decompdecomp gave it this one.\n"
                                     "// split_file divides it into the game's real files (file_status shows its range).\n"
                                     + common, encoding="utf-8")
                        made.append(p)
                    sp_path.write_text(sp.render(), encoding="utf-8")
                    text = dtksplits.add_objects(text, module, [name for _, name, _, _, _ in items])
                conf.write_text(text, encoding="utf-8")
                log(f"giving {len(plan['split'])} stretch(es) of code a source file; the build splits the game again")
                ok, out = fp.verify()
                if not ok:
                    raise ForkError(f"the game didn't build identically with the new files:\n{_tail(out)}")
            except (ForkError, OSError, ValueError) as e:
                for path, old in saved.items():
                    path.write_text(old, encoding="utf-8")
                for p in made:
                    p.unlink(missing_ok=True)
                fp.verify()
                raise NetworkError(f"nothing was changed: {e}") from None
            fp.build_all()
            rep = fp.report()
            paths = [str(p.relative_to(fp.root)) for p in [*saved, *made]]
            n = sum(item[4] for item in plan["split"])
            self._commit_paths(proj, fp, paths, f"Give code without a source file one ({len(made)} files, {n} functions)\n\n"
                               "Each stretch of code no file covered gets a file of its own, so it can be decompiled; "
                               "the game still builds byte for byte.\n\nSplit by decompdecomp.")
            self._scores = {k: v for k, v in self._scores.items() if k[0] != proj["id"]}
            self.sync(slug, log=lambda *_: None, rep=rep)
            with self.net.store.tx() as db:
                self.net._event(db, proj["id"], agent.id if agent else None, "files",
                                f"{len(made)} new source files for {n} functions")
        log(f"{len(made)} new source files; {n} more functions can be decompiled")
        return {"created": plan["create"], "split": [i[1] for i in plan["split"]], "functions": n}

    def split_file(self, agent: Agent | None, slug: str, path: str, at: str, new_name: str = "", data: str = "") -> str:
        """Divide a source file in two at a function: it and everything after it in the file's code go to a new file
        (with their C), and so do the parts of the data sections named in `data` (".rodata:0x88 .data:0x28": where the
        new file's part of each section starts). Kept only if the game still builds byte for byte and every function
        that matched still matches."""
        if agent is not None and (link := self.net.links.get(slug)) is not None:  # the host splits it; this copy follows
            text = link.call("/collab/split_file", agent, path=path, at=at, new_name=new_name, data=data)
            if text.startswith("✅"):
                link.sync()
            return text
        proj, fp, cfg = self.get(slug)
        if (console := consoles.get(cfg.get("platform"))).splits_sources:
            return self._split_source(agent, slug, console, path, at, new_name)
        mods = self._modules(fp, cfg)
        src = fp.src_dir()
        rel = path.strip().lstrip("./")
        if not fp.units_for(rel):
            try:  # a function: its file
                rel = fp.unit(self._fn(slug, path)["unit"]).source or ""
            except (NetworkError, ForkError):
                raise NetworkError(f"{path} isn't a source file (or a function) of this project") from None
        name = rel[len(src) + 1:] if rel.startswith(src + "/") else rel
        owners = []
        for module, mod in mods.items():
            sp = dtksplits.Splits.parse(mod.splits.read_text(encoding="utf-8"))
            if any(f.name == name for f in sp.files):
                owners.append((module, mod, sp))
        if len(owners) != 1:
            raise NetworkError(f"{rel} is {'not in any' if not owners else 'shared by several'} module's splits; "
                               "split_file divides a file one module owns")
        module, mod, sp = owners[0]
        if fp.link_states().get(rel, ("",))[0] != "NonMatching":
            raise NetworkError(f"{rel} is linked (or not in configure.py): a linked file is finished, don't split it")
        f = next(x for x in sp.files if x.name == name)
        if ".text" not in f.sections:
            raise NetworkError(f"{rel} has no code to split")
        syms = dtksplits.symbols(mod.symbols)
        lo, hi = f.sections[".text"]
        where = at.strip()
        if where in syms and syms[where][0] == ".text":
            addr = syms[where][1]
        elif re.fullmatch(r"(?:0x)?[0-9A-Fa-f]+", where):
            addr = int(where, 16)
        else:
            try:
                fn_row = self._fn(slug, at)
                addr = syms[self._meta(fn_row).get("symbol")][1]
            except (NetworkError, KeyError):
                raise NetworkError(f"`{at}` isn't a function of {rel}: give its name or .text offset") from None
        starts = sorted(a for sec, a, _ in syms.values() if sec == ".text" and lo < a < hi)
        if addr not in starts:
            raise NetworkError(f"`{at}` (0x{addr:X}) isn't the start of a function in {rel} (after its first one); "
                               f"its code is .text 0x{lo:X}-0x{hi:X}")
        ext = Path(name).suffix or ".c"
        new = (new_name.strip().lstrip("./") or f"{Path(name).parent.as_posix()}/text_{addr:08X}{ext}")
        new = new[len(src) + 1:] if new.startswith(src + "/") else new
        if not fp.safe_path(f"{src}/{new}", write=True) or (fp.root / src / new).exists() or \
                any(x.name == new for x in sp.files):
            raise NetworkError(f"{src}/{new} exists already: pick another new_name")
        moved_sections = {".text": (addr, hi)}
        kept = dict(f.sections, **{".text": (lo, addr)})
        for part in data.replace(",", " ").split():
            sec, _, start = part.partition(":")
            if sec not in f.sections or sec == ".text":
                raise NetworkError(f"{rel} has no {sec} part to split (it has {', '.join(f.sections)})")
            a, b = f.sections[sec]
            cut = int(start, 16)
            if not a <= cut <= b:
                raise NetworkError(f"{sec}:{start} is outside {rel}'s {sec} (0x{a:X}-0x{b:X})")
            moved_sections[sec] = (cut, b)
            kept[sec] = (a, cut)
        kept = {k: v for k, v in kept.items() if v[1] > v[0]}

        old_path, new_path = fp.root / src / name, fp.root / src / new
        conf = fp.root / "configure.py"
        unit = fp.units_for(rel)[0]
        rows = self.net.store.all("SELECT id, name, meta FROM functions WHERE project_id=? AND unit=?", proj["id"], unit.name)
        with self._tree(proj).exclusive():  # no other build runs meanwhile
            text = old_path.read_text(errors="replace", encoding="utf-8") if old_path.exists() else ""
            going = {s for r in rows if (s := self._meta(r).get("symbol")) in syms and addr <= syms[s][1] < hi}
            new_text, rest, moved = self._divide(text, rows, going)
            scores_before = self._baseline(proj, fp, unit, text)[0] or {}
            saved = {conf: conf.read_text(encoding="utf-8"), mod.splits: mod.splits.read_text(encoding="utf-8"), old_path: text}
            protected = {n for n, pct in scores_before.items() if pct is not None and pct >= 100}
            try:
                f.sections = kept
                sp.files.append(dtksplits.FileSplit(new, moved_sections))
                mod.splits.write_text(sp.render(), encoding="utf-8")
                ctext = saved[conf]
                states = dtksplits.libs(ctext)
                entry = next(((s0, e0) for lib in states for p_, s0, e0 in lib.objects if p_ == name), None)
                if entry is None:
                    raise ForkError(f"{name} isn't in configure.py")
                old_entry = ctext[entry[0]:entry[1]]
                new_entry = re.sub(r'^(Object\(\s*)\w+(\s*,\s*)"[^"]+"', lambda mm: f'{mm.group(1)}NonMatching{mm.group(2)}"{new}"',
                                   old_entry, count=1)
                indent = dtksplits.indent_of(ctext, entry[0])
                conf.write_text(ctext[:entry[1]] + f",\n{indent}{new_entry}" + ctext[entry[1]:], encoding="utf-8")
                new_path.parent.mkdir(parents=True, exist_ok=True)
                new_path.write_text(new_text, encoding="utf-8")
                old_path.write_text(rest, encoding="utf-8")
                ok, out = fp.verify()
                if not ok:
                    raise ForkError(f"the game didn't build identically with the files divided there:\n{_tail(out)}")
                after_units = [u for u in fp.units() if u.source in (rel, f"{src}/{new}")]
                now: dict[str, float | None] = {}
                for u in after_units:
                    built, log_ = fp.build_unit(u)
                    if not built:
                        raise ForkError(f"{u.source} doesn't compile after the split (move the declarations it needs with "
                                        f"edit_file first):\n{_tail(log_)}")
                    now.update(fp.diff(u, None).unit_scores)
                lost = sorted(n for n in protected if (now.get(n) or 0) < 100)
                if lost:
                    raise ForkError(f"these functions would stop matching: {', '.join(lost[:8])}")
            except (ForkError, ObjdiffError, OSError, ValueError) as e:
                for p_, old in saved.items():
                    p_.write_text(old, encoding="utf-8")
                new_path.unlink(missing_ok=True)
                fp.verify()
                for u in fp.units_for(rel):
                    fp.build_unit(u)
                raise NetworkError(f"not split, nothing changed: {e}") from None
            fp.build_all()
            rep = fp.report()
            self._commit_paths(proj, fp, [str(p_.relative_to(fp.root)) for p_ in saved] + [str(new_path.relative_to(fp.root))],
                               f"Split {Path(name).name} at 0x{addr:X}: {Path(new).name}\n\n"
                               f"Divided by {agent.name if agent else 'decompdecomp'} with decompdecomp; the game still builds "
                               "byte for byte.")
            self._scores = {k: v for k, v in self._scores.items() if k[0] != proj["id"]}
            self.sync(slug, log=lambda *_: None, rep=rep)
            with self.net.store.tx() as db:
                self.net._event(db, proj["id"], agent.id if agent else None, "split", f"{rel} → {src}/{new}")
        moved_text = ", ".join(f"{k} 0x{a:X}-0x{b:X}" for k, (a, b) in moved_sections.items())
        return (f"✅ {rel} is divided: {src}/{new} now holds {moved_text} ({moved} function definitions moved with "
                "it); the game still builds byte for byte and every match still matches.")

    def _divide(self, text: str, rows, going: set[str]) -> tuple[str, str, int]:
        """(the new file's C, the old file's, how many definitions moved) when the functions `going` (by symbol)
        leave a source file: they go with every declaration the file has, wherever it stands between definitions
        (a DS game's moved function used data declared just before it, far below the file's top), and leave it."""
        def without(spans):
            out = text
            for a, b in sorted(spans, reverse=True):
                out = out[:a].rstrip("\n") + "\n\n" + out[b:].lstrip("\n")
            return out

        spans = {}
        for r in rows:
            if (span := self._find(text, self._names(self._meta(r), r))[1]):
                spans[span] = (self._meta(r).get("symbol") or r["name"]) in going
        moving = sorted(s for s, g in spans.items() if g)
        declarations = without(spans).strip()
        code = "\n\n".join(text[a:b].strip("\n") for a, b in moving)
        return (declarations + "\n\n" if declarations else "") + code + "\n", without(moving), len(moving)

    def _split_source(self, agent: Agent | None, slug: str, console, path: str, at: str, new_name: str) -> str:
        """split_file for a console that divides source files in its config (Console.split_source: DS's delinks.txt):
        the function `at` and those after it in the file go to a new one, with their C and the file's compiler. Kept
        only if the game still builds as it did (byte for byte, where the console checks) and every match holds. On a
        DS game agents found a function built by another CodeWarrior version than its file's other functions: in a
        file of its own, it can have its own (try_compiler)."""
        proj, fp, cfg = self.get(slug)
        version = (fp.manifest or {}).get("version") or cfg.get("version")
        rel = path.strip().lstrip("./")
        if not fp.units_for(rel):
            try:  # a function: its file
                rel = fp.unit(self._fn(slug, path)["unit"]).source or ""
            except (NetworkError, ForkError):
                raise NetworkError(f"{path} isn't a source file (or a function) of this project") from None
        units = fp.units_for(rel)
        if not units:
            raise NetworkError(f"{path} isn't a source file of this project")
        unit = units[0]
        fn = self._fn(slug, at)
        if fn["unit"] != unit.name:
            raise NetworkError(f"`{at}` isn't a function of {rel}: give the name of the first one the new file gets")
        sym = self._meta(fn).get("symbol") or fn["name"]
        try:  # the file's functions in the order of their code, from its target object
            order = fp.diff(unit, None).order
        except (ObjdiffError, ForkError) as e:
            raise NetworkError(f"objdiff couldn't read {rel}'s functions: {e}") from None
        if sym not in order or order.index(sym) == 0:
            raise NetworkError(f"{fn['name']} is {rel}'s first function (or not in it): split at one after it")
        going = set(order[order.index(sym):])
        rows = [r for r in self.net.store.all("SELECT id, name, meta FROM functions WHERE project_id=? AND unit=? AND "
                                              "status != 'nosource'", proj["id"], unit.name)]
        stem = re.sub(r"_[0-9A-Fa-f]{8}$", "", Path(rel).stem)
        tail = re.search(r"([0-9A-Fa-f]{8})$", sym)
        new = new_name.strip().lstrip("./") or (Path(rel).parent / f"{stem}_{(tail.group(1) if tail else sym).upper()}"
                                                f"{Path(rel).suffix}").as_posix()
        old_path, new_path = fp.root / rel, fp.root / new
        if not fp.safe_path(new, write=True) or new_path.exists() or fp.units_for(new):
            raise NetworkError(f"{new} exists already: pick another new_name")
        text = old_path.read_text(errors="replace", encoding="utf-8") if old_path.exists() else ""
        new_text, rest, moved = self._divide(text, rows, going)
        protected = {n for n, pct in (self._baseline(proj, fp, unit, text)[0] or {}).items() if pct is not None and pct >= 100}
        checks = consoles.REBUILD_CHECK in console.features
        with self._tree(proj).exclusive(), self._build_locks[proj["id"]]:
            before = (fp.manifest or {}).get("rebuild") or {}
            saved = {old_path: text}
            try:
                saved.update(console.split_source(fp, version, rel, sym, new))
                new_path.write_text(new_text, encoding="utf-8")
                old_path.write_text(rest, encoding="utf-8")
                for cmd in fp.setup_commands(version):
                    ok, out = fp.run(cmd, log=lambda *_: None)
                    if not ok:  # (dsd's warnings drown what failed)
                        said = "\n".join(ln for ln in out.splitlines() if not ln.lstrip().startswith("[WARN"))
                        raise ForkError(f"the project didn't build with the files divided there (the moved C may need "
                                        f"declarations the file before has: move them with edit_file first):\n{_tail(said)}")
                if checks and before.get("identical") and not console.check_rebuild(fp, version).get("identical"):
                    raise ForkError("the game no longer rebuilt byte for byte with the files divided there")
                now: dict[str, float | None] = {}
                for u in [u for u in fp.units() if u.source in (rel, new)]:
                    built, log_ = fp.build_unit(u)
                    if not built:
                        raise ForkError(f"{u.source} doesn't compile after the split (move the declarations it needs "
                                        f"with edit_file first):\n{_tail(log_)}")
                    now.update(fp.diff(u, None).unit_scores)
                if lost := sorted(n for n in protected if (now.get(n) or 0) < 100):
                    raise ForkError(f"these functions would stop matching: {', '.join(lost[:8])}")
            except (ForkError, RomError, ObjdiffError, OSError, ValueError) as e:
                for p_, old in saved.items():
                    p_.write_text(old, encoding="utf-8")
                new_path.unlink(missing_ok=True)
                for cmd in fp.setup_commands(version):
                    fp.run(cmd, log=lambda *_: None)
                raise NetworkError(f"not split, nothing changed: {e}") from None
            self._commit_paths(proj, fp, [str(p_.relative_to(fp.root)) for p_ in saved] + [new],
                               f"Split {Path(rel).name} at {fn['name']}: {Path(new).name}\n\nDivided by "
                               f"{agent.name if agent else 'decompdecomp'} with decompdecomp; the game still builds as "
                               "it did and every match still matches.")
        self._scores = {k: v for k, v in self._scores.items() if k[0] != proj["id"]}
        self.sync(slug, log=lambda *_: None)
        with self.net.store.tx() as db:
            self.net._event(db, proj["id"], agent.id if agent else None, "split", f"{rel} → {new}")
        return (f"✅ {rel} is divided: {new} now holds {fn['name']} and what follows it ({moved} function "
                "definitions moved with them), with the file's compiler (try_compiler switches the new file's); the "
                "game still builds as it did and every match still matches.")

    SOURCE_SUFFIXES = (".c", ".h", ".cpp", ".hpp", ".cp", ".cc", ".inc")

    def rename_symbol(self, agent: Agent | None, slug: str, old: str, new: str, message: str = "") -> str:
        """Give one of the game's symbols (a function, data) a real name: in the console's config, where its name
        comes from (Console.rename_symbol), and as a whole word in every source and header file. Kept only when the
        project sets up again, the game still rebuilds as it did and every match still matches; else every file is
        put back. Polishing in project mode had local names only: functions and globals stayed func_0205d0f4 and
        data_0222f194. The function's row keeps its history under the new name. Where the report keeps the original's
        symbol (Console.renames_in_report: a PC game's _func_0041B470@8), the row keeps that name too, and the new one
        is the function's real name (Console.real_names), how its source spells it."""
        # (`old` as the report gives a PC function: _func_0041B470@8, @func_0041B470@8)
        if not re.fullmatch(r"[A-Za-z_]\w*", new) or not re.fullmatch(r"@?[A-Za-z_]\w*(?:@\d+)?", old) or old == new:
            raise NetworkError("rename_symbol takes two different C names: old and new")
        proj, fp, cfg = self.get(slug)
        if slug in self.net.links:
            raise NetworkError("in a room, the host's project is the one to rename in: say it in a note")
        console = consoles.get(cfg.get("platform"))
        version = (fp.manifest or {}).get("version") or cfg.get("version")
        word, taken = re.compile(console.rename_pattern(old)), re.compile(rf"\b{re.escape(new)}\b")
        checks = consoles.REBUILD_CHECK in console.features
        with self._tree(proj).exclusive(), self._build_locks[proj["id"]]:
            # read under the lock: eight polishing helpers renamed at once, each wrote back files it had read before
            # the others' renames, and half of an Xbox game's renames were undone in some files and not others
            files = sorted(p for d in ("src", "include") if (fp.root / d).is_dir() for p in (fp.root / d).rglob("*")
                           if p.is_file() and p.suffix in self.SOURCE_SUFFIXES)
            texts = {p: p.read_text(errors="replace", encoding="utf-8") for p in files}
            if any(taken.search(t) for t in texts.values()):
                raise NetworkError(f"{new} is used in the project already: pick another name")
            matched = {self._meta(r).get("symbol") or r["name"] for r in self.net.store.all(
                "SELECT name, meta FROM functions WHERE project_id=? AND status='matched'", proj["id"])}
            before = (fp.manifest or {}).get("rebuild") or {}
            saved: dict[Path, str] = {}
            try:
                saved.update(console.rename_symbol(fp, version, old, new))
                for p, t in texts.items():
                    if word.search(t):
                        saved[p] = t
                        p.write_text(word.sub(new, t), encoding="utf-8")
                for cmd in fp.setup_commands(version):
                    ok, out = fp.run(cmd, log=lambda *_: None)
                    if not ok:
                        raise ForkError(f"the project didn't build with the new name:\n{_tail(out)}")
                if checks and before.get("identical") and not console.check_rebuild(fp, version).get("identical"):
                    raise ForkError("the game no longer rebuilt byte for byte with the new name")
                rep = fp.report()
                now = {f["name"] for u in rep.get("units", []) for f in u.get("functions", [])
                       if (f.get("fuzzy_match_percent") or 0) >= 100}
                renamed = new if console.renames_in_report else old
                if lost := sorted(n for n in matched if (renamed if n == old else n) not in now):
                    raise ForkError(f"these functions would stop matching: {', '.join(lost[:8])}")
            except (ForkError, RomError, ObjdiffError, OSError, ValueError) as e:
                for p, t in saved.items():
                    p.write_text(t, encoding="utf-8")
                for cmd in fp.setup_commands(version):
                    fp.run(cmd, log=lambda *_: None)
                raise NetworkError(f"not renamed, nothing changed: {e}") from None
            with self.net.store.tx() as db:  # the function keeps its row, its matches and notes, under its new name
                if console.renames_in_report:  # (else sync gives it its real name, from the console's config)
                    db.execute("UPDATE functions SET name=?, meta=json_set(COALESCE(meta, '{}'), '$.symbol', ?) WHERE "
                               "project_id=? AND (name=? OR json_extract(meta, '$.symbol')=?)",
                               (new, new, proj["id"], old, old))
                self.net._event(db, proj["id"], agent.id if agent else None, "rename", f"{old} → {new}")
            self._commit_paths(proj, fp, [str(p.relative_to(fp.root)) for p in saved],
                               f"Rename {old} to {new}\n\n{message.strip() or 'Named'} by "
                               f"{agent.name if agent else 'decompdecomp'} with decompdecomp; the game still builds as it "
                               "did and every match still matches.")
        self.sync(slug, log=lambda *_: None, rep=rep)
        return f"✅ {old} is {new} now, in the game's symbols and in {len(saved)} file(s); every match still matches."

    def _commit_paths(self, proj, fp: ForkProject, paths: list[str], message: str) -> None:
        with self._git_locks[proj["id"]]:
            fp.git("add", "--", *paths)
            fp.git("commit", "-q", "-m", message, "--", *paths)

    @staticmethod
    def _count_uncovered(rep: dict) -> dict:
        """Code no source file covers (dtk's auto_..._text units): agents can't reach it until it has a file."""
        autos = [u for u in rep.get("units", []) if (u.get("metadata") or {}).get("auto_generated")
                 and u["name"].endswith("_text") and u.get("functions")]
        return {"stretches": len(autos), "functions": sum(len(u["functions"]) for u in autos)}

    def uncovered(self, slug: str) -> dict | None:
        """{"stretches", "functions"} without a source file, from the last measurement (a project measured by an
        older decompdecomp: from its saved report)."""
        proj, fp, cfg = self.get(slug)
        if "uncovered" in cfg:
            return cfg["uncovered"]
        try:
            counted = self._count_uncovered(json.loads((fp.root / STATE / "report.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None
        self.net._update_cfg(slug, lambda c: c.setdefault("uncovered", counted))
        return counted

    # ------------------------------------------------------------------ look-alike index

    def _index_of(self, proj, fp: ForkProject) -> dict:
        if proj["id"] not in self._index:
            p = fp.root / STATE / "index.json"
            try:
                self._index[proj["id"]] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            except ValueError:
                self._index[proj["id"]] = {}
        return self._index[proj["id"]]

    def _save_index(self, proj, fp: ForkProject) -> None:
        """Write index.json, only when something new was indexed: every sync (each pull from a room, each edit)
        ends an indexing pass, and a GameCube game's 11 MB index rewritten five times a second wrote 50 MB/s."""
        with self._ilock:
            if proj["id"] not in self._index_dirty:
                return
            self._index_dirty.discard(proj["id"])
            snapshot = dict(self._index.get(proj["id"], {}))
        try:
            (fp.root / STATE / "index.json").write_text(json.dumps(snapshot), encoding="utf-8")
        except OSError:
            with self._ilock:
                self._index_dirty.add(proj["id"])

    def _remember(self, proj, fp: ForkProject, unit, d) -> None:
        if unit.target is None or not unit.target.exists():
            return
        entry = {"mtime": unit.target.stat().st_mtime, "order": d.order, "ops": d.ops}
        idx = self._index_of(proj, fp)
        with self._ilock:
            if idx.get(unit.name) == entry:  # every compile measures its unit again: mostly nothing new
                return
            idx[unit.name] = entry
            self._index_dirty.add(proj["id"])

    def _unit_index(self, proj, fp: ForkProject, unit, save: bool = True) -> dict | None:
        """Target order and mnemonics of a unit's functions, from its target object only (never races a build)."""
        idx = self._index_of(proj, fp)
        if unit.target is None or not unit.target.exists():
            return None
        hit = idx.get(unit.name)
        if hit and hit.get("mtime") == unit.target.stat().st_mtime:
            return hit
        row = self.net.store.one("SELECT meta FROM functions WHERE project_id=? AND unit=? LIMIT 1", proj["id"], unit.name)
        if row is None:
            return None
        try:
            d = objdiff_diff(fp, unit, self._meta(row).get("symbol"), target_only=True)
        except (ObjdiffError, ForkError):
            return None
        self._remember(proj, fp, unit, d)
        if save:
            self._save_index(proj, fp)
        return idx.get(unit.name)

    def start_indexing(self, slug: str) -> None:
        """Index every unit in the background so find_similar covers the whole project."""
        proj, fp, cfg = self.get(slug)
        if proj["id"] in self._indexing or not cfg.get("ready"):
            return
        self._indexing.add(proj["id"])

        def run():
            try:
                for n, unit in enumerate(fp.units()):
                    if self.net.store.one("SELECT 1 FROM projects WHERE id=?", proj["id"]) is None:
                        return
                    self._unit_index(proj, fp, unit, save=False)
                    if n % 50 == 49:
                        self._save_index(proj, fp)
                self._save_index(proj, fp)
            finally:
                self._indexing.discard(proj["id"])
        threading.Thread(target=run, daemon=True, name=f"index-{slug}").start()

    def similar(self, proj, fp: ForkProject, fn, top: int = 5) -> list[tuple[dict, float]]:
        """Functions that look like `fn`, best first: [(row, similarity)]."""
        meta = self._meta(fn)
        unit = fp.unit(fn["unit"])
        own = self._unit_index(proj, fp, unit)
        if not own or meta.get("symbol") not in own["ops"]:
            return []
        target = own["ops"][meta["symbol"]]
        cands = {}
        with self._ilock:
            entries = list(self._index_of(proj, fp).items())
        for uname, entry in entries:
            for sym, ops in entry["ops"].items():
                if (uname, sym) != (fn["unit"], meta.get("symbol")):
                    cands[f"{uname}\0{sym}"] = ops
        out = []
        rows = {(r["unit"], self._meta(r).get("symbol")): r for r in self.net.store.all(
            "SELECT id, name, unit, status, best_score, meta, n_insns FROM functions WHERE project_id=?", proj["id"])}
        for key, sim in similarity.rank(target, cands, top=top * 2):
            uname, sym = key.split("\0", 1)
            if (uname, sym) in rows:
                out.append((rows[(uname, sym)], sim))
        out.sort(key=lambda x: (x[0]["status"] != "matched", -x[1]))
        return out[:top]

    def find_similar(self, slug: str, function: str, limit: int = 5) -> str:
        proj, fp, cfg = self.get(slug)
        fn = self._fn(slug, function)
        hits = self.similar(proj, fp, fn, top=max(1, min(int(limit), 15)))
        covered = len(self._index_of(proj, fp))
        total = len(fp.units())
        note = f"(index covers {covered} of {total} source files{'; still indexing' if proj['id'] in self._indexing else ''})"
        if not hits:
            return f"No look-alikes found for {fn['name']} {note}."
        lines = [f"Functions that look like {fn['name']} {note}:"]
        for r, sim in hits:
            state = "matched" if r["status"] == "matched" else f"best {r['best_score']}" if r["best_score"] is not None else "not started"
            lines.append(f"- {r['name']}  {sim * 100:.0f}% similar  {state}  ({r['unit']}, {r['n_insns']} insns)")
        best = next((r for r, _ in hits if r["status"] == "matched"), None)
        if best is not None:
            src = self._current_source(fp, best)
            if src:
                lines += ["", f"Source of {best['name']} (matched):", "```c", src.strip()[:4000], "```"]
        return "\n".join(lines)

    def _current_source(self, fp: ForkProject, fn, comment: bool = False) -> str | None:
        """fn's definition as its file has it now, also when the file gets it by including another source file.
        `comment`: with the comment right above it (polishing's brief shows it)."""
        unit = fp.unit(fn["unit"])
        if not unit.source or not (fp.root / unit.source).exists():
            return None
        names = self._names(self._meta(fn), fn)
        todo, seen = [fp.root / unit.source], set()
        while todo and len(seen) < 6:
            path = todo.pop(0)
            if path in seen:
                continue
            seen.add(path)
            try:
                text = path.read_text(errors="replace", encoding="utf-8")
            except OSError:
                continue
            _, span = self._find(text, names)
            if span:
                return text[csource.comment_above(text, span[0]) if comment else span[0]:span[1]]
            for inc in _SOURCE_INCLUDE.findall(text):
                todo += [p for p in (fp.root / inc, fp.root / "src" / inc, path.parent / inc) if p.is_file()][:1]
        return None

    def twin_source(self, proj, a, b, names: dict[str, str], includes: bool = False) -> str | None:
        """a's matched definition, from its source file, written for its twin b: names swapped (see app/twins.py),
        with the includes and declarations it relies on from a's file that b's file doesn't have (compile_and_diff
        places those in b's file). None for a definition in assembly (then b is assembly too)."""
        from . import twins

        fp = ForkProject(Path(proj["root"]))
        ua, ub = fp.unit(a["unit"]), fp.unit(b["unit"])
        if not ua.source or not ub.source:
            return None
        try:
            text_a = (fp.root / ua.source).read_text(errors="replace", encoding="utf-8")
            text_b = (fp.root / ub.source).read_text(errors="replace", encoding="utf-8") if (fp.root / ub.source).exists() else ""
        except OSError:
            return None
        _, span = self._find(text_a, self._names(self._meta(a), a))
        if span is None or _ASM_DEF.match(text_a[span[0]:span[1]]):
            return None
        code = twins.apply(text_a[span[0]:span[1]], names)
        used = set(re.findall(r"\b\w+\b", code))
        lines_b = {ln.strip() for ln in text_b.splitlines()}
        clean_b = csource._blank(text_b)
        declared_b = {m.group(1) for ln in text_b.splitlines() if drafts._DECL.match(ln.strip())
                      for m in [re.search(r"(\w+)\s*(?:\(|\[|;)", ln)] if m}
        head, seen = [], set()
        if includes:  # only the headers that define a name b's file doesn't know (more can clash with b's own)
            wanted = {n for n in used if not re.search(rf"\b{re.escape(n)}\b", clean_b)}
            head += _headers_defining(fp, ua.source, [ln for ln in _file_scope_lines(text_a)
                                                      if _INCLUDE_LINE.match(ln) and ln not in lines_b], wanted)
        for line in _file_scope_lines(text_a[:span[0]] + "\n" + text_a[span[1]:]):
            if _INCLUDE_LINE.match(line):
                continue
            if not drafts._DECL.match(line) or line.startswith("#"):
                continue
            renamed = twins.apply(line, names)
            name = re.search(r"(\w+)\s*(?:\(|\[|;)", renamed)
            if name and name.group(1) in used and name.group(1) not in declared_b and name.group(1) not in seen \
                    and renamed not in lines_b and not re.search(rf"\b{re.escape(name.group(1))}\b", clean_b):
                seen.add(name.group(1))
                head.append(renamed)
        if self._find(code, self._names(self._meta(b), b))[1] is None:
            return None  # a's definition isn't written under its symbol (a macro, another spelling): can't rename it
        return "\n".join(head) + ("\n" if head else "") + code

    def c_linkage(self, proj, a, b, source: str) -> str | None:
        """a's code made into b's, given C linkage (`extern "C"` on its definition and prototypes) when it goes
        from a C file into a C++ one: there its names would be mangled (fn_1_BB34 becomes fn_1_BB34__Fis), so
        the build has no fn_1_BB34 at all (0%). None when that isn't the case."""
        fp = ForkProject(Path(proj["root"]))
        src_a, src_b = fp.unit(a["unit"]).source or "", fp.unit(b["unit"]).source or ""
        cpp = (".cpp", ".cp", ".cc", ".cxx")
        sym = self._meta(b).get("symbol") or b["name"]
        if not src_b.endswith(cpp) or src_a.endswith(cpp) or "__" in sym.strip("_"):
            return None
        _, span = self._find(source, self._names(self._meta(b), b))
        if span is None or re.match(r"\s*(?:static|inline)\b", source[span[0]:span[1]]):
            return None
        head = []
        for line in source[:span[0]].splitlines():
            if drafts._DECL.match(line.strip()) and "(" in line and 'extern "C"' not in line:
                line = 'extern "C" ' + re.sub(r"^\s*extern\s+", "", line.strip())
            head.append(line)
        return "".join(line + "\n" for line in head) + 'extern "C" ' + source[span[0]:]

    def twin_declaration(self, proj, a, b, missing: str, names: dict[str, str]) -> str | None:
        """A declaration of `missing`, a name the code made from a's for b uses where b's file doesn't declare it yet
        (the compiler's "undefined identifier"): b's file's own (its prototype, or the head of its definition, both
        may come further down), else a's file's for the same name, renamed. Data only as extern: b's file keeps the
        data it defines."""
        from . import twins

        fp = ForkProject(Path(proj["root"]))
        ua, ub = fp.unit(a["unit"]), fp.unit(b["unit"])
        try:
            text_a = (fp.root / ua.source).read_text(errors="replace", encoding="utf-8")
            text_b = (fp.root / ub.source).read_text(errors="replace", encoding="utf-8") if (fp.root / ub.source).exists() else ""
        except (OSError, TypeError):
            return None
        a_name = {v: k for k, v in names.items()}.get(missing, missing)

        def declared(text: str, name: str) -> str | None:
            for line in _file_scope_lines(text):
                if line.startswith("#") or not re.search(rf"(?<![\w:]){re.escape(name)}\s*[(\[;=]", line):
                    continue
                if "(" in line.split("=", 1)[0] and line.endswith(";"):
                    return line  # a prototype
                if drafts._DECL.match(line) or "=" in line:  # data: refer to it, never define it again
                    left = re.sub(r"\bstatic\s+", "", line.split("=", 1)[0].rstrip(" ;"))
                    return left + ";" if left.startswith("extern") else f"extern {left};"
            span = csource.find(text, name)
            if span is not None:
                head = text[span[0]:span[1]].split("{", 1)[0].strip()
                if "(" in head:
                    return head + ";"
            return None

        if (own := declared(text_b, missing)) is not None:
            return own
        theirs = declared(text_a, a_name)
        if theirs is None:
            return None
        return re.sub(r"^(?:static\s+|inline\s+)+", "", twins.apply(theirs, names))  # b's file doesn't define it

    def twin_types(self, proj, a, b, source: str) -> str:
        """The file-scope type definitions (struct, union, enum, typedef) of a's file that `source` (a's code made
        into b's) relies on and b's file doesn't have, in a's order: for code that uses a type a's file defines."""
        fp = ForkProject(Path(proj["root"]))
        ua, ub = fp.unit(a["unit"]), fp.unit(b["unit"])
        try:
            text_a = (fp.root / ua.source).read_text(errors="replace", encoding="utf-8")
            text_b = (fp.root / ub.source).read_text(errors="replace", encoding="utf-8") if (fp.root / ub.source).exists() else ""
        except (OSError, TypeError):
            return ""
        blocks = _type_blocks(text_a)
        clean_b = csource._blank(text_b)
        want, out = set(re.findall(r"\b\w+\b", source)), []
        for _ in range(4):  # types used by the types taken
            added = False
            for names, block in blocks:
                if block in out or not names & want or any(re.search(rf"\b{re.escape(n)}\b", clean_b) for n in names):
                    continue
                out.append(block)
                want |= set(re.findall(r"\b\w+\b", block))
                added = True
            if not added:
                break
        return "\n".join(b for _, b in blocks if b in out)

    def one_definition(self, slug: str, source: str, name: str, others: list[str]) -> str:
        """A source holding several functions' definitions, with the definitions of `others` (and the #pragma lines
        right around them) taken out: what compile_and_diff for `name` alone gets."""
        for other in others:
            try:
                fn = self._fn(slug, other)
            except NetworkError:
                continue
            _, span = self._find(source, self._names(self._meta(fn), fn))
            if span:
                lo, hi = _pragmas_around(source, *span)
                source = source[:lo] + source[hi:]
        return source

    def is_asm(self, proj, fn) -> bool:
        """Whether a matched function's definition in its file is hand-written assembly (an asm function)."""
        src = self._current_source(ForkProject(Path(proj["root"])), fn)
        return bool(src and _ASM_DEF.match(src))

    def mark_asm(self, fn, twin_name: str | None = None, reason: str | None = None) -> None:
        """fn's code is a matched assembly function's (runtime helpers each module links its own copy of), or code
        no compiler makes (`reason`: Console.asm_reason, arch/): agents can't write it in C, so it's out of their way."""
        meta = self._meta(fn)
        if meta.get("reopened"):  # the user gave it back to the agents (Network.reopen)
            return
        if twin_name:
            meta["asm_twin"] = twin_name
        if reason:
            meta["asm_code"] = reason
        with self.net.store.tx() as db:
            db.execute("UPDATE functions SET status='asm', meta=? WHERE id=? AND status='open'", (json.dumps(meta), fn["id"]))

    # ------------------------------------------------------------------ for agents

    def info(self, slug: str) -> str:
        proj, fp, cfg = self.get(slug)
        m = cfg.get("measures") or {}
        units = fp.units() if cfg.get("ready") else []
        scratch = next((u.scratch for u in units if u.scratch), None) or {}
        platform = consoles.name(cfg.get("platform")) or "unknown platform"
        console = consoles.get(cfg.get("platform"))
        per_file = (f"each source file can have its own compiler (try_compiler switches it, \"all\" tries each); "
                    f"this platform's: {', '.join(c.id for c in console.compilers)}\n"
                    if console.file_compiler(fp, "") else "")
        what = ("started from scratch from your copy of the game" if cfg.get("origin") == "scratch"
                else f"a fork of {cfg['repo_url']}")
        return (f"{proj['name']}: {what} ({platform}{', ' + cfg['version'] if cfg.get('version') else ''})\n"
                f"progress: {m.get('matched_functions', '?')}/{m.get('total_functions', '?')} functions, "
                f"{float(m.get('matched_code_percent', 0) or 0):.2f}% of code, in {len(units)} source files\n"
                f"compiler: {scratch.get('compiler', 'set by the project build')} {scratch.get('c_flags', '')}\n"
                + per_file +
                "You work in the project's real source tree. claim_function, then compile_and_diff writes your function "
                "into its source file, builds that file with the project's build system and diffs it with objdiff. "
                "read_file and search_code show how the codebase is written; edit_file changes headers and file-level "
                "declarations (undone if it breaks any match). Guides: guide()."
                + "".join(f"\nThe project has notes for contributors: read_file(\"{n}\") before you start."
                          for n in ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md") if (fp.root / n).exists())[:400])

    def _fn(self, slug: str, name: str):
        return self.net._function(name, slug)

    @staticmethod
    def _names(meta: dict, fn) -> list[str]:
        """Ways the function may be spelled in source: its real name (a PC game's, Console.real_names), full C++
        name, Class::method, then the plain symbol."""
        out = [meta["real"]] if meta.get("real") else []
        dem = (meta.get("demangled") or "").split("(", 1)[0].strip()
        if dem:
            dem = dem.split(" ")[-1] if " " in dem and "operator" not in dem else dem
            out.append(dem)
            parts = dem.split("::")
            if len(parts) > 2:
                out.append("::".join(parts[-2:]))
            if len(parts) > 1:
                out.append(parts[-1])
        out.append(meta.get("symbol") or fn["name"].split("@")[0])
        if (c := _MSVC_C.fullmatch(meta.get("symbol") or "")):  # _f, _f@8 (__stdcall), @f@8 (__fastcall): f in C
            out.append(c.group(1))
        return list(dict.fromkeys(out))

    def _find(self, text: str, names: list[str]):
        for n in names:
            span = csource.find(text, n)
            if span:
                return n, span
        return None, None

    def _held(self, proj, unit: str, text: str | None = None) -> set[str]:
        """Functions of a unit whose source is still the very text judged to load the wrong constants: objdiff's
        progress rules count them, decompdecomp doesn't (see forks/literals.py)."""
        rows = self.net.store.all("SELECT name, meta FROM functions WHERE project_id=? AND unit=? AND meta LIKE ?",
                                  proj["id"], unit, '%"held"%')
        if not rows:
            return set()
        if text is None:
            try:
                u = ForkProject(Path(proj["root"])).unit(unit)
                text = (Path(proj["root"]) / u.source).read_text(errors="replace", encoding="utf-8") if u.source else ""
            except (ForkError, OSError):
                return set()
        out = set()
        for r in rows:
            meta = self._meta(r)
            _, span = self._find(text, self._names(meta, r))
            if span and _digest(text[span[0]:span[1]]) == meta.get("held"):
                out.add(meta.get("symbol"))
        return out

    def _hold(self, fn, code: str | None) -> None:
        """Remember (code) or forget (None) that this text of the function loads constants the original doesn't."""
        meta = self._meta(fn)
        if code is None and "held" not in meta:
            return
        if code is None:
            meta.pop("held", None)
        else:
            meta["held"] = _digest(code)
        with self.net.store.tx() as db:
            db.execute("UPDATE functions SET meta=? WHERE id=?", (json.dumps(meta), fn["id"]))

    def _reopened(self, db, proj, r, via: str, pct: float | None, held: bool = False) -> None:
        """Record a matched function going back to open, with what measured it and how (`via`: a unit's diff, the
        full report...), so a function that keeps flipping shows why in the events."""
        why = ("matches but is held (its constants or references differ)" if held else
               "isn't in the build's object" if pct is None else f"{pct:.2f}%")
        self.net._event(db, proj["id"], None, "reopen", f"{r['name']} ({via}: {why})")

    @staticmethod
    def _missing_note(unit, fn) -> str:
        """What to tell an agent whose compile built, but whose unit's object has no such function."""
        return (f"\n\nNOT IN YOUR BUILD: {unit.name} compiled, but its object doesn't define {fn['name']}, so there "
                "is nothing to compare yet.")

    def _apply_scores(self, proj, unit: str, scores: dict[str, float | None], skip: int | None = None,
                      agent: Agent | None = None, text: str | None = None, via: str = "unit diff") -> None:
        """Bring every function of a unit up to date with a fresh diff (cheaper than a full report). A function
        that matches now is a match like any other, whichever change made it: `agent` (whose edit it was) is
        credited, and `text` (the unit's source) gives the matching code. `via` says which measure it was, for the
        reopen events."""
        rows = self.net.store.all("SELECT id, name, status, n_insns, best_score, best_source, meta FROM functions "
                                  "WHERE project_id=? AND unit=?", proj["id"], unit)
        held = self._held(proj, unit, text)
        with self.net.store.tx() as db:
            for r in rows:
                sym = self._meta(r).get("symbol")
                if r["id"] == skip or sym not in scores or r["status"] == "nosource":
                    continue
                pct = scores[sym]
                if pct is not None and pct >= 100 and sym not in held:
                    if r["status"] != "matched":
                        self._matched(db, proj, r, agent, text)
                elif r["status"] == "matched":  # no longer matches: its best is what it scores now, not 0 (identical)
                    db.execute("UPDATE functions SET status='open', best_score=? WHERE id=?",
                               (None if pct is None else scaled(pct, r["n_insns"]), r["id"]))
                    self._reopened(db, proj, r, via, pct, held=pct is not None and pct >= 100)
                elif pct is not None and r["best_score"] == 0:  # reopened before this was fixed: still says identical
                    db.execute("UPDATE functions SET best_score=? WHERE id=?", (scaled(pct, r["n_insns"]), r["id"]))
                elif pct is not None and not r["best_source"] and r["best_score"] != scaled(pct, r["n_insns"]):
                    db.execute("UPDATE functions SET best_score=? WHERE id=?", (scaled(pct, r["n_insns"]), r["id"]))

    def _matched(self, db, proj, r, agent: Agent | None, text: str | None) -> None:
        """Record a function that matches now (outside compile_and_diff, which records its own)."""
        source = None
        if text:
            _, span = self._find(text, self._names(self._meta(r), r))
            source = text[span[0]:span[1]] if span else None
        db.execute("UPDATE functions SET status='matched', best_score=0, matched_at=?, matched_agent_id=?, "
                   "matched_source=COALESCE(?, matched_source, best_source) WHERE id=?",
                   (time.time(), agent.id if agent else None, source, r["id"]))
        db.execute("DELETE FROM claims WHERE function_id=?", (r["id"],))
        self.net._event(db, proj["id"], agent.id if agent else None, "match", r["name"])
        self.net.on_match(proj, r["id"], agent)

    DATA_LISTED = 12  # data symbols a brief describes

    def permuter_header(self, slug: str, function: str) -> str:
        """What decomp-permuter must read before a function of a fork: the headers its source file includes, then
        the file's own declarations. It got none, so it refused every DS function ("Syntax error in base.c": `u16`
        was no type it knew) and only the simple rewrites ran, there and in every fork."""
        _, fp, _ = self.get(slug)
        unit = fp.unit(self._fn(slug, function)["unit"])
        if not unit.source or not (fp.root / unit.source).is_file():
            return ""
        seen: set[Path] = set()

        def headers(text: str, folder: Path) -> list[str]:  # what `text` includes, each header after its own
            out = []
            for name in _QUOTED_INCLUDE.findall(text):
                for base in (fp.root / "include", folder, fp.root / "src", fp.root):
                    h = (base / name).resolve()
                    if h.is_file():
                        if h not in seen and len(seen) < MAX_HEADERS and h.is_relative_to(fp.root.resolve()):
                            seen.add(h)
                            own = h.read_text(errors="replace", encoding="utf-8")
                            out += [*headers(own, h.parent), own]
                        break
            return out
        source = (fp.root / unit.source).read_text(errors="replace", encoding="utf-8")
        return "\n".join([*headers(source, (fp.root / unit.source).parent), _without_bodies(source)])

    def _data_used(self, proj, refs: list[str]) -> list[str]:
        """A from-scratch project's brief: the data the function uses, where it is and how big, with how to declare
        it. Its headers start empty, and on a DS game a model looked for that in config/symbols.txt for minutes."""
        if not refs:
            return []
        marks = ",".join("?" * len(refs))
        funcs = {r["name"] for r in self.net.store.all(
            f"SELECT name FROM functions WHERE project_id=? AND name IN ({marks})", proj["id"], *refs)}
        out = []
        for name in [r for r in refs if r not in funcs][:self.DATA_LISTED]:
            try:
                said = self.read_data(None, proj["slug"], name, 4).split("\n", 1)[0]
            except NetworkError:
                said = ""
            m = re.search(r", (\.?[\w.]+)\+0x[0-9A-Fa-f]+, (\S+) bytes\)", said)
            out.append(f"- {name}: {m.group(1)}, {m.group(2)} bytes" if m else f"- {name}")
        if not out:
            return []
        return ["", "### Data it uses", *out,
                "Declare what isn't declared yet once, with edit_file at the top of the source file: `extern` and the "
                "type its loads and stores use (2 bytes: `extern u16 name;`; bigger ones: an array, or a struct whose "
                "fields are the offsets the code uses). That's all there is to know about them: nothing in config/ "
                "says more."]

    def brief(self, fn, agent: Agent | None, header: str) -> str:
        proj = self.net.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])
        _, fp, cfg = self.get(proj["slug"])
        meta = self._meta(fn)
        unit = fp.unit(fn["unit"])
        lines = [f"## {header}: {fn['name']}  ({proj['name']})", f"unit {unit.name}, {fn['size']} bytes"]
        if fn["status"] != "matched":
            lines.append(move_on_brief(fn))
        if meta.get("demangled"):
            lines.append(f"C++ name: {meta['demangled']}")
        if unit.scratch:
            lines.append(f"compiler: {unit.scratch.get('compiler', '?')} {unit.scratch.get('c_flags', '')}")
        src_text = None
        if unit.source:
            lines.append(f"source file: {unit.source}")
            if own := consoles.get(cfg.get("platform")).file_compiler(fp, unit.source):
                lines.append(f"this file's compiler: {own} (try_compiler(\"{fn['name']}\", \"all\") tries the others)")
            p = fp.root / unit.source
            src_text = p.read_text(errors="replace", encoding="utf-8") if p.exists() else None
        else:
            lines.append("this unit has no source file yet, so it can't be decompiled from here")
        try:
            d = fp.diff(unit, meta.get("symbol") or fn["name"])
            pct = ("not in your build yet" if d.match_percent is None else
                   "every instruction matches, but loaded constants differ" if d.literals else
                   f"{percent_text(d.match_percent)} match")
            asm = d.target_asm().splitlines()
            if len(asm) > 500:
                asm = asm[:500] + [f"... ({len(asm) - 500} more instructions)"]
            lines += [f"current state: {pct}", "", "### Target assembly", "```asm", *asm, "```"]
            if cfg.get("origin") == "scratch":
                lines += self._data_used(proj, d.refs)
            if d.literals:
                lines += ["", d.literals_note()]
            elif d.match_percent is not None and not d.matched:
                d.rows = with_constant_values(d.rows, consoles.get(cfg.get("platform")), fp)
                lines += ["", "### Diff against the current build", d.render()]
        except (ObjdiffError, ForkError) as e:
            lines.append(f"(objdiff couldn't diff this function: {e})")
        _, span = self._find(src_text, self._names(meta, fn)) if src_text else (None, None)
        if span:
            lines += ["", "### Current source", "```c", src_text[span[0]:span[1]].strip(), "```"]
        else:
            lines += ["", f"### Not in {unit.source or 'the source'} yet: compile_and_diff adds it at the end."]
        if fn["best_source"] and fn["status"] != "matched":
            lines += ["", f"### Best attempt so far (score {fn['best_score']})", "```c", fn["best_source"].strip(), "```"]
        elif fn["status"] != "matched" and (span is None or csource.is_stub(src_text[span[0]:span[1]])):
            seed = self._draft(proj, fp, cfg, fn, unit)
            if seed:
                lines += ["", "### Machine draft: a starting point, often wrong in types and details", "```c",
                          seed.strip(), "```"]
        twin = self._twin_brief(proj, fn) if fn["status"] != "matched" else []
        lines += twin
        try:
            alike = [] if twin else [(r, sim) for r, sim in self.similar(proj, fp, fn, top=3) if r["status"] == "matched"]
        except (ObjdiffError, ForkError):
            alike = []
        if alike:
            r, sim = alike[0]
            src = self._current_source(fp, r)
            lines += ["", f"### Looks like {r['name']} ({sim * 100:.0f}% similar, already matched)"]
            if src and len(src) < 3000:
                lines += ["```c", src.strip(), "```"]
            if len(alike) > 1:
                lines.append("also similar and matched: " + ", ".join(f"{x['name']} ({s * 100:.0f}%)" for x, s in alike[1:]))
        done = self.net.store.all("SELECT name FROM functions WHERE project_id=? AND unit=? AND status='matched' "
                                  "AND id != ? ORDER BY n_insns DESC LIMIT 12", fn["project_id"], fn["unit"], fn["id"])
        if done:
            lines += ["", "Already matched in this file (read them for the codebase's style): "
                      + ", ".join(r["name"] for r in done)]
        why = (cfg.get("link_failed") or {}).get(unit.source or "")
        if why and not unit.complete:
            lines += ["", f"### {unit.source} didn't link yet (file_status shows its data)", why]
        lines += self.net.dependency_brief(fn)
        best = fn["best_score"] if fn["status"] != "matched" else None
        best_text = (f"{percent_text(max(0.0, 100.0 * (1 - best / (fn['n_insns'] * 100))))} (score {best})"
                     if best is not None and fn["n_insns"] else "")
        notes = self.net.notes_for_brief(fn, best_text, show=6, when=False)
        if notes:
            lines += ["", "### Notes", *notes]
        lines += ["", "Send compile_and_diff the complete function definition (same name and signature as the original), "
                      "with any #include \"...\", prototypes or #pragma lines it needs. Structs, globals and helper "
                      "functions go in with edit_file."]
        return "\n".join(lines)

    def _twin_brief(self, proj, fn) -> list[str]:
        """For a brief: the matched function with the same code (relocations aside), and its code made into this
        one's when the names can be swapped."""
        try:
            twin_id = self.net.twins.twin_of(proj, fn["id"])
        except Exception:
            return []
        if twin_id is None:
            return []
        a = self.net.store.one("SELECT * FROM functions WHERE id=?", twin_id)
        try:
            source = self.net.twins.source_for(proj, a, fn, includes=True)
        except (NetworkError, ForkError, OSError, ValueError):
            source = None
        out = ["", f"### Identical code: {a['name']} ({a['unit']}) is the same machine code and already matches"]
        if source and len(source) < 6000:
            why = self.net.twins.miss_why(fn["id"])
            out += ["Its code with this function's names: start from it. "
                    + (f"decompdecomp tried it as is: {why}. Fix that (this file lacks something the other one has: a "
                       "type, a declaration, an include, or declares a callee differently)." if why else
                       "If it doesn't build or match as is, this file lacks something that one has (a type, a "
                       "declaration, an include)."), "```c", source.strip(), "```"]
        elif self.is_asm(proj, a):
            out.append("It's hand-written assembly there, so this one is too: release it with a note saying so.")
        else:
            src = self._current_source(ForkProject(Path(proj["root"])), a)
            if src and len(src) < 4000:
                out += ["Its source (its names differ from this one's: use the ones in this function's assembly):",
                        "```c", src.strip(), "```"]
        return out

    def _draft(self, proj, fp: ForkProject, cfg: dict, fn, unit) -> str | None:
        """m2c draft from the project's assembly for this function, cached in the database."""
        if fn["seed"] is not None:
            return fn["seed"] or None
        hint = (unit.scratch or {}).get("compiler", "") or json.dumps(fp.needs(cfg.get("version")))
        meta = self._meta(fn)
        try:
            text = drafts.draft(fp.root, cfg.get("platform") or "", unit.name, meta.get("symbol") or fn["name"],
                                unit.source, compiler_hint=hint)
            text = text and "/* m2c */\n" + text
            if not text and unit.target is not None and unit.target.exists() and ghidra.available():
                found = ghidra.decompile_object(unit.target, fp.root / STATE / "ghidra")
                names = [meta.get("symbol") or fn["name"], *self._names(meta, fn)]
                body = next((found[n] for n in names if n in found), None)
                text = body and "/* Ghidra */\n" + body.strip()
        except OSError:
            text = None
        with self.net.store.tx() as db:
            db.execute("UPDATE functions SET seed=? WHERE id=?", (text or "", fn["id"]))
        return text

    def claim(self, agent: Agent, slug: str, function: str | None = None, strategy: str = "easiest") -> str:
        if (link := self.net.links.get(slug)) is not None:
            return self._claim_in_room(link, agent, slug, function, strategy)
        still = self.net.still_holding(agent, function)
        fn = self.net.claim_row(agent, slug, function, strategy)
        if fn is not None and still:
            return still + self.brief(fn, agent, "CLAIMED")
        if fn is None:
            left = (self.get(slug)[2].get("uncovered") or {}).get("functions")
            return (self.net.nothing_to_claim(agent, self.net._project(slug), " with a source file")
                    + (f"\n{left} more functions are in code no source file covers yet. The user can give them files "
                       "(the project's overview in decompdecomp: Give them source files); then there's more to claim."
                       if left else ""))
        return self.brief(fn, agent, "CLAIMED")

    def _claim_in_room(self, link, agent: Agent, slug: str, function: str | None, strategy: str) -> str:
        """A member of a room: the host hands out functions (nobody does one twice); the brief comes from this copy."""
        name = link.claim(agent, self._fn(slug, function)["name"] if function else None, strategy)
        if name is None:
            return "Nothing left to claim in this room: every function with a source file matches or someone is on it. 🎉"
        try:
            fn = self.net.claim_row(agent, slug, name, strategy)
        except NetworkError:  # the host has a newer tree (new files, a split): follow it, then claim here
            link.sync()
            fn = self.net.claim_row(agent, slug, name, strategy)
        return self.brief(fn, agent, "CLAIMED")

    def get_function(self, agent: Agent | None, slug: str, function: str) -> str:
        return self.brief(self._fn(slug, function), agent, "VIEW")

    def list_functions(self, slug: str, status: str = "open", limit: int = 40) -> str:
        proj, _, _ = self.get(slug)
        where = {"open": "status='open'", "matched": "status='matched'", "all": "1"}.get(status)
        if where is None:
            raise NetworkError("status must be open, matched or all")
        rows = self.net.store.all(f"SELECT name, unit, n_insns, status, best_score FROM functions WHERE project_id=? "
                                  f"AND {where} ORDER BY n_insns LIMIT ?", proj["id"], max(1, min(int(limit), 200)))
        out = [f"{r['name']:<40} {r['unit']:<36} {r['n_insns']:>5} insns  "
               f"{'matched' if r['status'] == 'matched' else 'best ' + str(r['best_score']) if r['best_score'] is not None else 'new'}"
               for r in rows]
        return "\n".join(out) or "none"

    def attempt(self, agent: Agent, slug: str, function: str, source: str, keep: bool = True, prelude: str = "",
                quiet: int | None = None, comment: bool = False) -> dict:
        """`keep`: whether a version that doesn't match stays in the source file (an agent's work in progress) or
        is undone and kept only as the function's best attempt (autopilot's drafts). `prelude`: (decompdecomp's own
        tries) type definitions the code needs, placed after the file's includes. `quiet`: (the permuter's
        variants) recorded only when better than this score. A try that isn't kept holds the function only while
        it builds (twins' tries come unclaimed: a 30-minute claim each kept agents off them, then counted as given up).
        `comment`: (polishing) the comment right above the definition goes into the file, in place of the file's."""
        held = None if keep else self._hold_for_try(slug, function, agent)
        try:
            res = self._attempt(agent, slug, function, source, keep, prelude, quiet, comment)
        finally:
            if held is not None:
                with self.net.store.tx() as db:
                    db.execute("DELETE FROM claims WHERE function_id=? AND agent_id=?", (held, agent.id))
        link = self.net.links.get(slug)
        if link is None or res["score"] is None:
            return res
        name = self._fn(slug, function)["name"]
        if res["matched"]:  # the host builds it in its own tree, against its own copy, and commits it for everyone
            try:
                verdict = link.attempt(agent, name, source)
            except NetworkError as e:
                res["text"] += f"\n⚠️ Not shared with the room yet: {e}"
                return res
            if verdict.get("matched"):
                res["text"] += "\nShared with the room: the host confirmed the match."
                threading.Thread(target=self._follow, args=(link,), daemon=True).start()
            else:
                res["text"] += ("\n⚠️ The room's host could not confirm this match in its tree (score "
                                f"{verdict.get('score')}): it may have changed since; this copy follows the host's.")
        else:
            fn = self._fn(slug, function)
            if fn["best_score"] is None or res["score"] <= fn["best_score"]:
                threading.Thread(target=self._report_best, args=(link, agent, name, source, res["score"]), daemon=True).start()
        return res

    def _hold_for_try(self, slug: str, function: str, agent: Agent) -> int | None:
        """Claim an open function nobody holds for one quick try; its id, or None (held already, or matched)."""
        fn = self._fn(slug, function)
        now = time.time()
        with self.net.store.tx() as db:
            self.net._expire_claims(db)
            if fn["status"] != "open" or db.execute("SELECT 1 FROM claims WHERE function_id=?", (fn["id"],)).fetchone():
                return None
            db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + 300))
        return fn["id"]

    @staticmethod
    def _follow(link) -> None:
        try:
            link.sync()
        except NetworkError:
            pass

    @staticmethod
    def _report_best(link, agent: Agent, name: str, source: str, score: int) -> None:
        try:
            link.call("/collab/best", agent, function=name, source=source, score=score)
        except NetworkError:
            pass

    def _attempt(self, agent: Agent, slug: str, function: str, source: str, keep: bool = True, prelude: str = "",
                 quiet: int | None = None, comment: bool = False) -> dict:
        proj, fp, cfg = self.get(slug)
        fn = self._fn(slug, function)
        meta = self._meta(fn)
        if len(source) > 64 * 1024:
            raise NetworkError("source too large")
        if _CHEAT.search(_strip_comments(source)):
            raise NetworkError("inline assembly, asm functions and asm includes aren't allowed: a match has to come from C/C++")
        names = self._names(meta, fn)
        _, span = self._find(source, names)
        if span is None:
            raise NetworkError(f"send the complete definition of {names[0]} (with its body)")
        # with the comment right above it (what it does, from polishing): the file kept the definition only
        # Polishing's only: a twin's note or a decompiling agent's scratch comment replaced a forked decomp's own
        # doc comments, and piled up in its files
        if comment:
            span = (csource.comment_above(source, span[0]), span[1])
        code = source[span[0]:span[1]]
        commented = code.lstrip().startswith(("//", "/*"))
        unit = fp.unit(fn["unit"])
        if not unit.source:
            raise NetworkError(f"unit {unit.name} has no source file, so there's nowhere to write it")
        try:
            have = (fp.root / unit.source).read_text(errors="replace", encoding="utf-8")
        except OSError:
            have = ""
        includes, before_lines, after_lines = _around(source, span, have, unit.source)
        with self.net.store.tx() as db:  # claim bookkeeping, as for ROM projects
            self.net._expire_claims(db)
            claim = db.execute("SELECT agent_id FROM claims WHERE function_id=?", (fn["id"],)).fetchone()
            if claim is not None and claim["agent_id"] != agent.id:
                raise NetworkError(f"{fn['name']} is claimed by {self.net._agent_name(claim['agent_id'])}")
            now = time.time()
            if claim is None and fn["status"] != "matched":
                db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + self.net.claim_ttl))
            elif claim is not None:
                db.execute("UPDATE claims SET expires_at=? WHERE function_id=?", (now + self.net.claim_ttl, fn["id"]))
        path = fp.safe_path(unit.source, write=True)
        symbol = meta.get("symbol") or fn["name"]
        linked = unit.complete and fp.linkable()  # the game is built from this file: changes must keep it identical
        with self._tree(proj).exclusive() if linked else self._tree(proj).shared(), self._unit_lock(proj, unit.name):
            before = path.read_text(errors="replace", encoding="utf-8") if path.exists() else ""
            # What matches in the file as it is now is what this change must keep (not the database's idea of it,
            # which a change outside decompdecomp or a different measurement can leave behind)
            scores, builds = self._baseline(proj, fp, unit, before)
            held = self._held(proj, unit.name, before)
            if scores is not None:
                self._apply_scores(proj, unit.name, scores, text=before, via="unit diff before a compile")
                protected = {n for n, pct in scores.items()
                             if n != symbol and n not in held and pct is not None and pct >= 100}
                was_matched = (scores.get(symbol) or 0) >= 100 and symbol not in held
            else:
                protected = {n for n, pct in (self._last_scores(proj, unit) or {}).items()
                             if n != symbol and n not in held and pct is not None and pct >= 100}
                was_matched = fn["status"] == "matched"
            hit, old_span = self._find(before, names)
            pre, post = "".join(ln + "\n" for ln in before_lines), "".join(ln + "\n" for ln in after_lines)
            if old_span:
                lo, hi = old_span
                kept = ""  # the file's comment, when none was sent
                if commented:  # a new comment replaces the old one; without one, the file's stays
                    lo = csource.comment_above(before, lo)
                if any(_PRAGMA.match(ln) for ln in before_lines + after_lines):  # a new wrapper replaces the old one
                    above = csource.comment_above(before, lo)
                    wrapped, hi = _pragmas_around(before, lo, hi)
                    if not commented and wrapped < above:
                        kept = before[above:lo]
                    lo = wrapped
                after = before[:lo] + pre + kept + code.rstrip("\n") + "\n" + post + before[hi:]
            else:
                after = self._insert_in_order(proj, fp, unit, fn, before, pre + code.rstrip("\n") + "\n" + post)
            after = _with_includes(after, includes, prelude)
            # the same file as an earlier compile of this function made, nothing it depends on changed since: that
            # compile's result, not another build
            agents_try = keep and quiet is None and not was_matched
            key = self.net.compile_key(fn, unit.name, after)
            if agents_try and (again := self.net.repeat_of(key, agent, fn)) is not None:
                return again
            _rewrite(path, after)

            def remembered(text: str, score: int | None, kept: bool = True) -> str:
                """A result as repeat_of gives it back later (agents' tries only), plus the note when this variant
                only reproduces an earlier score of the claim."""
                if not agents_try:
                    return text
                if kept:
                    self.net.remember_compile(key, agent, fn, text, score)
                return text + self.net.same_score_note(fn["id"], agent.id, score, code)

            def undo() -> None:
                _rewrite(path, before)
                if self._build_unit(proj, fp, unit)[0] and scores is not None:
                    self._keep_scores(proj, unit, before, scores)

            ok, log = self._build_unit(proj, fp, unit)
            if not ok and (broken := fp.build_problem()) is not None:
                # the build itself is broken, not the agent's code: nothing recorded or counted, and the project
                # is set up again in the background (one project spent five compiles on a build.ninja whose python
                # was in another home, then the sessions quit)
                _rewrite(path, before)
                threading.Thread(target=self.preflight, args=(slug,), daemon=True, name=f"preflight-{slug}").start()
                return {"text": f"❌ The project's build is broken, not your code: {broken}. Your change was undone, "
                                "and nothing was recorded or counted. decompdecomp is setting the project up again; "
                                "don't compile until it's fixed: end your session here.", "score": None,
                        "matched": False}
            if not ok:
                undo()
                if quiet is None:
                    self.net._record(fn, agent, None, code, 0)
                why = "" if builds else ("\n(The file didn't build before your change either, so the error may be in "
                                         "other code of the file: fix that with edit_file.)")
                return {"text": f"❌ BUILD ERROR (your change was undone){why}\n{log[-3000:]}", "score": None,
                        "matched": False}
            try:
                d = fp.diff(unit, symbol)
            except (ObjdiffError, ForkError) as e:
                undo()
                return {"text": f"❌ objdiff couldn't compare (your change was undone): {e}", "score": None, "matched": False}
            if quiet is None:
                d.rows = with_constant_values(d.rows, consoles.get(cfg.get("platform")), fp)
                self.net.keep_diff(agent.id, fn["id"], d.rows, objdiff.LEGEND)
            broken = sorted(n for n in protected if n and (d.unit_scores.get(n) or 0) < 100)
            if broken:
                undo()
                if quiet is None:
                    self.net._record(fn, agent, None, code, 0, compiled=True)
                return {"text": remembered(f"❌ REJECTED and undone: your change breaks matched function(s) "
                                           f"{', '.join(broken[:8])}. Check you replaced the right definition "
                                           "(overloads?) and didn't change shared code.", None),
                        "score": None, "matched": False, "broken": broken}
            if linked and d.matched and not (ok := self._verify(proj, fp))[0]:
                undo()
                if quiet is None:
                    self.net._record(fn, agent, None, code, 0, compiled=True)
                return {"text": f"❌ REJECTED and undone: {unit.source} is linked into the game, and with your change "
                                f"the game no longer builds byte for byte:\n{_tail(ok[1])}", "score": None,
                        "matched": False}
            pct = d.match_percent if d.match_percent is not None else 0.0
            score = max(1, len(d.literals)) if d.literals else scaled(pct, fn["n_insns"])
            matched = d.matched
            if not matched and not keep and not was_matched:  # tried, not kept: the file stays as it was
                undo()
                if quiet is None or score < quiet:
                    self.net._record(fn, agent, score, code, 0, compiled=True)
                return {"text": f"{percent_text(pct)} match (score {score}); not kept in {unit.source}", "score": score,
                        "matched": False, "percent": pct}
            best = fn["best_score"]
            if (not matched and not was_matched and d.match_percent is not None and best is not None
                    and fn["best_source"] and score > best):
                # worse than the best so far: the file keeps what it had, so it never ends on a worse variant than
                # the one the notes and the brief call the best (an agent found its 91.8% try there, not its 92.3%)
                undo()
                if quiet is None:
                    self.net._record(fn, agent, score, code, 0, compiled=True)
                stall = self.net.stall_note(fn["id"], agent.id)
                text = remembered(f"{percent_text(pct)} match (score {score}), worse than the best so far (score "
                                  f"{best}), so {unit.source} keeps what it had; your attempt is recorded.\n"
                                  f"{d.render()}", score)
                return {"text": text + stall, "score": score, "matched": False, "percent": pct}
            self._keep_scores(proj, unit, after, d.unit_scores)
            self._hold(fn, code if d.literals else None)
            if was_matched:  # a rewrite of a matched function must keep matching
                if quiet is None:  # a polishing helper's compiles count too (not as a match)
                    self.net._record(fn, agent, score, code, 0)
                if not matched:
                    undo()
                    why = "loads other constants" if d.literals else f"is {percent_text(pct)}"
                    return {"text": f"❌ {fn['name']} already matches; your version {why}, so the matching one was "
                                    "kept. Call claim_function for a function that still needs work.",
                            "score": score, "matched": False}
                self._commit(proj, fp, unit.source, f"Tidy {meta.get('demangled') or symbol} (still matches)\n\n"
                                                    f"Edited by {agent.name} with decompdecomp.")
                return {"text": f"✅ {fn['name']} still matches; your tidier version is committed.{_renamed_note(d)}",
                        "score": 0, "matched": True}
            self.net._record(fn, agent, score, code, 0, matched=matched)
            self._apply_scores(proj, fn["unit"], d.unit_scores, skip=fn["id"], agent=agent, text=after,
                               via=f"unit diff after a compile of {fn['name']}")
            self._remember(proj, fp, unit, d)
            if matched:
                self._commit(proj, fp, unit.source, f"Match {meta.get('demangled') or symbol}\n\n"
                                                    f"Decompiled by {agent.name} with decompdecomp.")
                return {"text": f"✅ MATCH! {fn['name']} is 100% identical to the original. Committed to git in the "
                                f"project. Call claim_function for the next one.{_renamed_note(d)}"
                                f"{self.net.dependents_note(fn['id'])}"
                                f"{self._file_done(slug, proj, fp, unit, d.unit_scores, agent)}", "score": 0,
                        "matched": True}
        stall = self.net.stall_note(fn["id"], agent.id)
        missing = self._missing_note(unit, fn) if d.match_percent is None else ""
        if d.literals:
            text = f"score {score} (0 = identical). {d.literals_note()}{missing}"
        else:
            text = f"{percent_text(pct)} match (score {score}; 0 = identical).\n{d.render()}{missing}"
        return {"text": remembered(text, score) + stall, "score": score, "matched": False}

    def try_compiler(self, agent: Agent, slug: str, function: str, compiler: str, flags: str = "",
                     source: str = "") -> str:
        """A function's C built with another of its console's compilers, where the project lets each source file have
        its own (Console.file_compiler: a DS game mixes CodeWarrior versions, issue #23). Judged as an attempt that
        isn't kept: a version it matches with stays the file's, committed, and only when the file's other matched
        functions still match (the attempt's own rule); otherwise the file keeps its compiler and nothing is
        recorded. "all": the file's own compiler first (C that matches with it needs no switch), then the others,
        nearest versions first, until one matches or TRY_ALL_SECONDS pass (on the DS demo, C that matched with its
        own 2.0sp1p5 moved its file to 2.0/base when the own one was skipped and the oldest went first)."""
        proj, fp, cfg = self.get(slug)
        console = consoles.get(cfg.get("platform"))
        fn = self._fn(slug, function)
        unit = fp.unit(fn["unit"])
        own = console.file_compiler(fp, unit.source) if unit.source else None
        if own is None:
            raise NetworkError("a fork builds with its project's own compilers")
        if self.net.links.get(slug) is not None:  # a guest's tree follows the host's: its build is the host's to change
            raise NetworkError("in a room, the host's project decides each file's compiler: try_compiler works there")
        if flags.strip():
            raise NetworkError("this project's flags are its build's own: try_compiler changes only the file's compiler")
        if fn["status"] == "matched":
            raise NetworkError(f"{fn['name']} already matches with {own}")
        known = [c.id for c in console.compilers]
        if compiler.strip().lower() == "all":
            wanted = sorted(known, key=lambda c: abs(known.index(c) - known.index(own))) if own in known else known
        else:
            wanted = [compiler.strip()]
        if wanted[0] not in known:
            raise NetworkError(f"{wanted[0]!r} isn't one of this platform's compilers: {', '.join(known)}")
        if wanted == [own]:
            raise NetworkError(f"{unit.source} is built with {own} already")
        source = source.strip() or self.net._source_of(fn) or ""
        if not source:
            raise NetworkError(f"{fn['name']} has no attempt yet: pass the C as source")
        linked = unit.complete and fp.linkable()
        tried, kept, changed = [], None, False
        until = time.monotonic() + TRY_ALL_SECONDS
        held = self._hold_for_try(slug, function, agent)  # a function nobody holds is held only while it's tried
        try:
            with self._tree(proj).exclusive() if linked else self._tree(proj).shared(), self._unit_lock(proj, unit.name):
                try:
                    # what matches with the file's own compiler, measured before it changes: what every try must keep
                    self._baseline(proj, fp, unit, (fp.root / unit.source).read_text(errors="replace", encoding="utf-8"))
                    for c in wanted:
                        if tried and time.monotonic() > until:
                            break
                        if c != own:
                            with self._build_locks[proj["id"]]:  # (a build reads the map)
                                rel = console.set_file_compiler(fp, unit.source, c)
                            changed = True
                        res = self._attempt(agent, slug, function, source, keep=False, quiet=0)
                        if res["matched"] and c == own:
                            return f"{c}: {res['text']}\nThat's {unit.source}'s own compiler: nothing to switch."
                        if res["matched"]:
                            kept = c
                            self.net.bump_generation(proj["id"])  # (earlier compiles of the file aren't repeats)
                            self._commit(proj, fp, rel, f"Build {unit.source} with {c}\n\n{fn['name']} matches with "
                                                        f"it (found by {agent.name} with decompdecomp).")
                            return f"{c}: {res['text']}\n{unit.source} is built with {c} from now on."
                        if res.get("broken"):  # the file's other functions need its own compiler
                            res["text"] = (f"the file's matched {', '.join(res['broken'][:8])} no longer match with it. "
                                           f"If {fn['name']} needs it, give it a file of its own first (split_file)")
                        tried.append(f"{c}: {res['text']}" if len(wanted) == 1 else f"{c}: {res['text'].split(chr(10))[0]}")
                finally:
                    if changed and kept is None:
                        with self._build_locks[proj["id"]]:
                            console.set_file_compiler(fp, unit.source, own)
                        self._build_unit(proj, fp, unit)  # the file's object is its own compiler's again
        finally:
            if held is not None:
                with self.net.store.tx() as db:
                    db.execute("DELETE FROM claims WHERE function_id=? AND agent_id=?", (held, agent.id))
        if len(wanted) == 1:
            return f"{tried[0]}\n{unit.source} stays with {own}."
        left = wanted[len(tried):]
        return (f"No compiler matches {fn['name']}; {unit.source} stays with {own}.\n" + "\n".join(tried)
                + (f"\nNot tried yet (time ran out): {', '.join(left)}. Try them by name." if left else ""))

    # ------------------------------------------------------------------ autopilot

    def autopilot_prepare(self, slug: str) -> list[int]:
        """The open functions autopilot tries in a game started from scratch, smallest first. Raises NetworkError
        when it can't run here."""
        proj, fp, cfg = self.get(slug)
        if cfg.get("origin") != "scratch":
            raise NetworkError("autopilot works on games started from scratch: a public decomp's headers don't have "
                               "what machine drafts need, and its easy functions are done already")
        if not cfg.get("ready"):
            raise NetworkError("finish setting the project up first")
        if not drafts.supported(cfg.get("platform") or "") or toolchains.installed("m2c") is None:
            raise NetworkError("autopilot needs m2c, the draft decompiler: install it in Setup")
        if drafts.add_m2c_macros(fp.root / "include" / "common.h"):  # projects made before autopilot ran on them
            self._commit(proj, fp, "include/common.h", "Add the macros machine drafts use\n\nWritten by decompdecomp's autopilot.")
        rows = self.net.store.all("SELECT id FROM functions WHERE project_id=? AND status='open' ORDER BY n_insns, id",
                                  proj["id"])
        return [r["id"] for r in rows]

    def autopilot_one(self, agent: Agent, proj, fid: int) -> str:
        """match | near | far | fail | skip, for one function's machine draft, built and diffed like an agent's."""
        now = time.time()
        with self.net.store.tx() as db:
            self.net._expire_claims(db)
            fn = db.execute("SELECT * FROM functions WHERE id=?", (fid,)).fetchone()
            if fn is None or fn["status"] != "open" or db.execute("SELECT 1 FROM claims WHERE function_id=?", (fid,)).fetchone():
                return "skip"
            db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fid, agent.id, now, now + 300))
        try:
            _, fp, cfg = self.get(proj["slug"])
            unit = fp.unit(fn["unit"])
            if not unit.source:
                return "skip"
            meta = self._meta(fn)
            hint = (unit.scratch or {}).get("compiler", "") or json.dumps(fp.needs(cfg.get("version")))
            text = drafts.draft(fp.root, cfg.get("platform") or "", unit.name, meta.get("symbol") or fn["name"],
                                unit.source, compiler_hint=hint, valid=True)
            _, span = self._find(text, self._names(meta, fn)) if text else (None, None)
            if span is None:
                return "fail"
            try:
                current = (fp.root / unit.source).read_text(errors="replace", encoding="utf-8")
            except OSError:
                current = ""
            code = "/* machine draft (m2c), tried by autopilot */\n" + drafts.self_contained(text, span, current)
            res = self.attempt(agent, proj["slug"], fn["name"], code, keep=False)
            if res["matched"]:
                return "match"
            if res["score"] is None:
                why = [ln for ln in res["text"].splitlines() if re.search(r"error|Error", ln) and "ninja" not in ln]
                logging.getLogger("decompdecomp").info("autopilot: %s's draft didn't build: %s", fn["name"],
                                                     " | ".join(why[:4])[:600] or res["text"][-300:])
            return "fail" if res["score"] is None else "near" if (res.get("percent") or 0) >= 90 else "far"
        except (NetworkError, OSError, subprocess.SubprocessError) as e:
            logging.getLogger("decompdecomp").info("autopilot: %s not tried: %s", fn["name"], str(e)[:500])
            return "fail"
        finally:
            with self.net.store.tx() as db:
                db.execute("DELETE FROM claims WHERE function_id=? AND agent_id=?", (fid, agent.id))

    def _insert_in_order(self, proj, fp: ForkProject, unit, fn, text: str, code: str) -> str:
        """Place a new function where it was in the original: after the closest earlier function present in the
        file, else before the closest later one, else at the end."""
        code = code.strip("\n") + "\n"
        entry = self._unit_index(proj, fp, unit)
        sym = self._meta(fn).get("symbol")
        order = entry["order"] if entry else []
        if sym in order:
            rows = {self._meta(r).get("symbol"): r for r in self.net.store.all(
                "SELECT name, meta FROM functions WHERE project_id=? AND unit=?", proj["id"], unit.name)}
            k = order.index(sym)
            for other in reversed(order[:k]):
                if other in rows:
                    _, span = self._find(text, self._names(self._meta(rows[other]), rows[other]))
                    if span:
                        return text[:span[1]].rstrip("\n") + "\n\n" + code + "\n" + text[span[1]:].lstrip("\n")
            for other in order[k + 1:]:
                if other in rows:
                    _, span = self._find(text, self._names(self._meta(rows[other]), rows[other]))
                    if span:
                        return text[:span[0]] + code + "\n" + text[span[0]:]
        return text.rstrip("\n") + "\n\n" + code

    def propose_join(self, agent: Agent, slug: str, function: str, with_function: str, reason: str) -> str:
        """An agent says two functions are one the analysis cut (a piece, a switch's cases in the next symbol): what
        the rules (arch/, a console's own) missed. Once NOT_C_AGENTS agents of NOT_C_MODELS models say so of the same
        two, the console joins them (Console.join_functions), checked as an analysis is (reanalyse: the game rebuilt
        byte for byte, else taken back), never touching a matched function; a join that doesn't hold is remembered,
        so it isn't proposed again. What agents joined stays: Check again only ever joins, never splits."""
        if not reason.strip():
            raise NetworkError("say why they're one function (what the code shows): the next agents read it")
        proj, fp, cfg = self.get(slug)
        if slug in self.net.links:
            raise NetworkError("in a room, the host's project is the one to join functions in: say it in a note")
        a, b = self._fn(slug, function), self._fn(slug, with_function)
        if a["id"] == b["id"]:
            raise NetworkError("name two different functions")
        if any(f["status"] == "matched" for f in (a, b)):
            raise NetworkError(f"{next(f['name'] for f in (a, b) if f['status'] == 'matched')} is matched: a join "
                               "would change it, so it can't be one")
        first, last = sorted((a, b), key=lambda f: (f["vram"] or 0, f["id"]))
        key = f"{first['name']}+{last['name']}"
        refused = (cfg.get("joins_refused") or {}).get(key)
        if refused:
            return f"Joining {first['name']} and {last['name']} was tried, and refused: {refused}. Work on them as they are."
        with self.net.store.tx() as db:
            meta = json.loads(db.execute("SELECT meta FROM functions WHERE id=?", (first["id"],)).fetchone()["meta"] or "{}")
            said = meta.setdefault("joins", {}).setdefault(last["name"], {})
            said[agent.name] = {"model": model_of(agent.name), "why": reason.strip()[:2000], "t": time.time()}
            db.execute("UPDATE functions SET meta=? WHERE id=?", (json.dumps(meta), first["id"]))
            self.net._event(db, proj["id"], agent.id, "join", f"{first['name']} + {last['name']}: {reason.strip()[:200]}")
        models = {v["model"] for v in said.values()}
        if len(said) < NOT_C_AGENTS or len(models) < NOT_C_MODELS:
            return (f"Your proposal is counted ({len(said)} of {NOT_C_AGENTS} agents, {len(models)} of {NOT_C_MODELS} "
                    f"models): {first['name']} and {last['name']} are joined once enough agree and the game still "
                    "builds byte for byte. Meanwhile, claim another function.")
        console = consoles.get(cfg.get("platform"))
        matched = {r["name"] for r in self.net.store.all("SELECT name FROM functions WHERE project_id=? AND "
                                                         "status='matched'", proj["id"])}
        why = None

        def step(fp):
            nonlocal why
            try:
                return console.join_functions(fp, self._meta(first).get("symbol") or first["name"],
                                              self._meta(last).get("symbol") or last["name"], matched)
            except (RomError, ForkError) as e:
                why = str(e)
                raise ForkError(why) from None
        done = self.reanalyse(slug, step, log=lambda *_: None)
        if not done:
            why = why or "the game no longer rebuilt byte for byte with it"
            self.net._update_cfg(slug, lambda c: c.setdefault("joins_refused", {}).update({key: why}))
            return f"{len(said)} agents agreed, but joining {first['name']} and {last['name']} was refused: {why}."
        self.net.joined(first["name"], slug)  # (what set it aside was about a piece of it)
        try:
            self.net.add_note(self.net.local_agent("decompdecomp")[0], slug,
                              f"Agents ({', '.join(sorted(said))}) found this was cut by the analysis, and decompdecomp "
                              f"joined it: {done}. It's the whole function now, whatever older notes say.",
                              first["name"])
        except NetworkError:
            pass
        return f"With yours, {len(said)} agents of {len(models)} models agree: {done}. The game still builds byte for byte."

    def release(self, agent: Agent, slug: str, function: str, notes: str, not_c: bool = False) -> str:
        fn = self._fn(slug, function)
        if (link := self.net.links.get(slug)) is not None:
            link.call("/collab/release", agent, function=fn["name"], notes=notes, not_c=not_c)
        latest = self.net.store.one("SELECT score FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT 1",
                                    fn["id"], agent.id)
        if (fn["best_source"] and fn["status"] != "matched" and latest and latest["score"] is not None
                and fn["best_score"] is not None and latest["score"] > fn["best_score"]):
            try:  # leave the file at its best state for the next agent
                self.attempt(agent, slug, function, fn["best_source"])
            except NetworkError:
                pass
        return Network.release(self.net, agent, function, notes, slug, not_c)

    def release_exempt(self, slug: str, fn) -> str | None:
        """Why compiling can't help with this function (release_function lets such a release through at once), or
        None: its unit has no source file, or its identical twin elsewhere is hand-written assembly."""
        proj, fp, _ = self.get(slug)
        if not fp.unit(fn["unit"]).source:
            return "its unit has no source file"
        try:
            twin = self.net.twins.twin_of(proj, fn["id"])
            if twin is not None and self.is_asm(proj, self.net.store.one("SELECT * FROM functions WHERE id=?", twin)):
                return "its twin is hand-written assembly"
        except Exception:  # a heuristic: never in the way of a release
            return None
        return None

    def release_hints(self, slug: str, fn) -> list[str]:
        """For a release pushed back: what this project knows about what to do next with the function (nothing yet
        in general)."""
        return []

    def read_file(self, slug: str, path: str, start: int = 1, count: int = 200) -> str:
        _, fp, _ = self.get(slug)
        p = fp.root if path.strip().strip("/") in ("", ".") else fp.safe_path(path)
        if p.is_dir():  # a folder: what's in it (agents look around this way)
            hidden = {".git", "build", STATE, "orig", "__pycache__", "node_modules"}
            items = sorted((x for x in p.iterdir() if x.name not in hidden and not x.name.startswith(".")),
                           key=lambda x: (not x.is_dir(), x.name.lower()))
            listing = [f"  {x.name}/" if x.is_dir() else f"  {x.name}" for x in items[:300]]
            more = f"\n  ... and {len(items) - 300} more" if len(items) > 300 else ""
            return f"{path.strip() or '.'} is a folder ({len(items)} entries):\n" + "\n".join(listing) + more
        if not p.is_file():
            raise NetworkError(f"{path} isn't a file{self._did_you_mean(fp, path)}")
        with open(p, "rb") as f:
            head = f.read(8192)
        if b"\0" in head or p.stat().st_size > 4 * 1024 * 1024:
            raise NetworkError(f"{path} isn't a source or text file")  # never the user's game data
        lines = p.read_text(errors="replace", encoding="utf-8").splitlines()
        start = max(1, int(start))
        chunk = lines[start - 1:start - 1 + max(1, min(int(count), 600))]
        out = [f"{path} (lines {start}-{start + len(chunk) - 1} of {len(lines)})"]
        out += [f"{start + i:5d}  {line}" for i, line in enumerate(chunk)]
        return "\n".join(out)

    def read_data(self, agent: Agent | None, slug: str, what: str, length: int = 64) -> str:
        """A data symbol's bytes in the original game (from the target objects): a string, a table, a constant,
        with the symbols its words point to."""
        from elftools.elf.elffile import ELFFile
        from elftools.elf.relocation import RelocationSection

        proj, fp, _ = self.get(slug)
        name = what.strip().strip("`'\"")
        if (text := consoles.get((fp.manifest or {}).get("platform")).read_data(fp, name, length)) is not None:
            return text  # the console reads it (PC: the program's data isn't in the target objects)
        held = agent and self.net.store.one("SELECT f.unit FROM claims c JOIN functions f ON f.id=c.function_id "
                                            "WHERE c.agent_id=?", agent.id)
        units = [held["unit"]] if held else []
        units += [u for u in self._data_index(proj, fp).get(name, []) if u not in units]
        for uname in units:
            try:
                unit = fp.unit(uname)
            except ForkError:
                continue
            if unit.target is None or not unit.target.exists():
                continue
            with open(unit.target, "rb") as f:
                elf = ELFFile(f)
                symtab = elf.get_section_by_name(".symtab")
                sym = next((x for x in symtab.iter_symbols() if x.name == name and isinstance(x["st_shndx"], int)
                            and x["st_info"]["type"] != "STT_FUNC"), None) if symtab else None
                if sym is None:
                    continue
                sec = elf.get_section(sym["st_shndx"])
                head = f"{name} ({uname}, {sec.name}+0x{sym['st_value']:X}, {sym['st_size'] or '?'} bytes)"
                if sec["sh_type"] == "SHT_NOBITS":
                    return f"{head}: uninitialized data ({sec.name}), zeros at boot."
                n = max(4, min(sym["st_size"] or 512, int(length or 64), 512))
                data = sec.data()[sym["st_value"]:sym["st_value"] + n]
                if n < (sym["st_size"] or 0):
                    head += f"; the first {n} (length up to 512 shows more)"
                ptrs = []
                for rs in elf.iter_sections():
                    if isinstance(rs, RelocationSection) and rs["sh_info"] == sym["st_shndx"]:
                        table = elf.get_section(rs["sh_link"])
                        for r in rs.iter_relocations():
                            at = r["r_offset"] - sym["st_value"]
                            if 0 <= at < n:
                                t = table.get_symbol(r["r_info_sym"])
                                tname = elf.get_section(t["st_shndx"]).name if t["st_info"]["type"] == "STT_SECTION" else t.name
                                addend = r["r_addend"] if r.is_RELA() else 0
                                ptrs.append((at, tname + (f"+0x{addend:X}" if addend else "")))
                out = [head]
                for i in range(0, len(data), 16):
                    chunk = data[i:i + 16]
                    out.append(f"  +0x{i:03X}: {chunk.hex(' '):<47}  {''.join(chr(c) if 32 <= c < 127 else '.' for c in chunk)}")
                if ptrs:
                    out.append("pointers in it: " + ", ".join(f"+0x{at:X} -> {t}" for at, t in sorted(ptrs)[:24]))
                e = ">" if not elf.little_endian else "<"
                words = [w for (w,) in struct.iter_unpack(e + "I", data[:len(data) // 4 * 4])]
                floats = [struct.unpack(e + "f", struct.pack(e + "I", w))[0] for w in words[:8]]
                if words and any(1e-6 < abs(x) < 1e7 for x in floats):
                    out.append("as f32: " + ", ".join(f"{x:g}" for x in floats))
                text = data.split(b"\0", 1)[0]
                if len(text) >= 3 and all(32 <= c < 127 or c in (9, 10) for c in text):
                    out.append(f"as a string: {json.dumps(text.decode('ascii'))}")
                return "\n".join(out)
        raise NetworkError(f"no data symbol `{name}` in the game's objects (read_file and search_code find it in "
                           "the source; a function is shown by get_function)")

    def _data_index(self, proj, fp: ForkProject) -> dict[str, list[str]]:
        """Data symbol -> units whose target object defines it (built once per objdiff.json)."""
        from elftools.elf.elffile import ELFFile

        key = (proj["id"], (fp.root / "objdiff.json").stat().st_mtime_ns if (fp.root / "objdiff.json").exists() else 0)
        cached = getattr(self, "_data_idx", None)
        if cached and cached[0] == key:
            return cached[1]
        index: dict[str, list[str]] = defaultdict(list)
        for unit in fp.units():
            if unit.target is None or not unit.target.exists():
                continue
            try:
                with open(unit.target, "rb") as f:
                    symtab = ELFFile(f).get_section_by_name(".symtab")
                    for x in symtab.iter_symbols() if symtab else ():
                        if x.name and x["st_info"]["type"] in ("STT_OBJECT", "STT_NOTYPE") and isinstance(x["st_shndx"], int):
                            index[x.name].append(unit.name)
            except Exception:
                continue
        self._data_idx = (key, dict(index))
        return self._data_idx[1]

    @staticmethod
    def _did_you_mean(fp: ForkProject, path: str) -> str:
        """Files of the project with the name the agent guessed a path for."""
        name = Path(path).name
        if not name or not EDITABLE.search(name):
            return ""
        root = fp.root.resolve()
        found = []
        for hit in root.rglob(name):
            rel = hit.relative_to(root)
            if hit.is_file() and not {".git", "build", STATE} & set(rel.parts):
                found.append(rel.as_posix())
                if len(found) >= 5:
                    break
        return ("; did you mean " + " or ".join(found)) if found else ""

    def search(self, slug: str, pattern: str, glob: str = "") -> str:
        _, fp, _ = self.get(slug)
        try:
            rx = re.compile(pattern)
        except re.error:  # e.g. "func(": search for the text itself
            rx = re.compile(re.escape(pattern))
        root = fp.root.resolve()
        if glob:
            glob = _path_glob(glob)
            try:
                files = sorted(fp.root.glob(glob))
            except ValueError as e:
                raise NetworkError(f"glob {glob!r}: {e} (e.g. src/**/*.cpp, include/*.h)") from None
        else:
            files = sorted(p for ext in ("c", "cc", "cpp", "h", "hpp", "inc") for p in fp.root.rglob(f"*.{ext}"))
        hits = []
        for p in files:
            rp = p.resolve()
            if root not in rp.parents or not rp.is_file():
                continue
            rel = rp.relative_to(root)
            if {".git", "build", STATE} & set(rel.parts):
                continue
            for i, line in enumerate(rp.read_text(errors="replace", encoding="utf-8").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel.as_posix()}:{i}: {line.strip()[:160]}")
                    if len(hits) >= 80:
                        return "\n".join(hits + ["... (more matches; narrow the pattern)"])
        return "\n".join(hits) or "no matches"

    def edit_file(self, agent: Agent, slug: str, path: str, old: str, new: str, message: str = "") -> str:
        # without a message the commit says what changed where: agents left it out hundreds of times a night, and
        # each refusal cost a turn
        message = message.strip() or f"Edit {Path(path).as_posix()}"
        if (link := self.net.links.get(slug)) is not None:  # the host makes and checks it; this copy follows
            text = link.call("/collab/edit", agent, path=path, old=old, new=new, message=message)
            if text.startswith(("✅", "⚠️")):
                link.sync()
            return text
        proj, fp, cfg = self.get(slug)
        p = fp.safe_path(path, write=True)
        self.net.bump_generation(proj["id"])  # (a header or shared file: compiles may come out differently now)
        if _CHEAT.search(_strip_comments(new)):
            raise NetworkError("inline assembly, asm functions and asm includes aren't allowed")
        rel = Path(os.path.relpath(p, fp.root)).as_posix()
        units = [u for u in fp.units_for(rel) if u.base is not None]
        if units:  # a unit's own source file: only that unit can change
            return self._edit_source(agent, proj, fp, p, rel, units, old, new, message)
        return self._edit_shared(agent, proj, fp, slug, p, rel, old, new, message)

    @staticmethod
    def _edited(p: Path, path: str, old: str, new: str) -> tuple[bool, str, str]:
        existed = p.exists()
        text = p.read_text(errors="replace", encoding="utf-8") if existed else ""
        if old:
            return existed, text, replace_once(text, old, new, path)
        return existed, text, text.rstrip("\n") + ("\n" if text else "") + new.strip("\n") + "\n"

    def _edit_source(self, agent: Agent, proj, fp: ForkProject, p: Path, rel: str, units: list, old: str, new: str,
                     message: str) -> str:
        """Edit a unit's source file: rebuild and measure just that unit, keep everything that matched before."""
        linked = any(u.complete for u in units) and fp.linkable()
        with self._tree(proj).exclusive() if linked else self._tree(proj).shared(), \
                self._units_locked(proj, [u.name for u in units]):
            existed, text, updated = self._edited(p, rel, old, new)
            bases = {u.name: self._baseline(proj, fp, u, text) for u in units}
            p.parent.mkdir(parents=True, exist_ok=True)
            _rewrite(p, updated)

            def undo() -> None:
                if existed:
                    _rewrite(p, text)
                else:
                    p.unlink(missing_ok=True)
                for u in units:
                    if self._build_unit(proj, fp, u)[0] and bases[u.name][0] is not None:
                        self._keep_scores(proj, u, text, bases[u.name][0])

            after: dict[str, dict | None] = {}
            for u in units:
                ok, log = self._build_unit(proj, fp, u)
                if not ok:
                    if bases[u.name][1]:
                        undo()
                        return f"❌ {rel} doesn't compile after your edit, so it was undone:\n{log[-2500:]}"
                    after[u.name] = None  # it didn't build before the edit either
                    continue
                try:
                    after[u.name] = fp.diff(u, None).unit_scores
                except (ObjdiffError, ForkError) as e:
                    undo()
                    return f"❌ objdiff couldn't measure {rel} after your edit, so it was undone: {e}"
            lost = []
            for u in units:
                before = bases[u.name][0] if bases[u.name][0] is not None else self._last_scores(proj, u)
                now = after[u.name] or {}
                lost += [n for n, pct in (before or {}).items() if pct is not None and pct >= 100 and (now.get(n) or 0) < 100]
            if lost:
                undo()
                return f"❌ REJECTED and undone: the edit breaks {len(lost)} matched function(s): {', '.join(sorted(lost)[:10])}"
            if linked and not (ok := self._verify(proj, fp))[0]:
                undo()
                return (f"❌ REJECTED and undone: {rel} is linked into the game, and with your edit the game no longer "
                        f"builds byte for byte:\n{_tail(ok[1])}")
            for u in units:
                if after[u.name] is not None:
                    self._keep_scores(proj, u, updated, after[u.name])
                    self._apply_scores(proj, u.name, after[u.name], agent=agent, text=updated,
                                       via=f"unit diff after an edit of {rel}")
            self._commit(proj, fp, rel, f"{message.strip()}\n\nEdited by {agent.name} with decompdecomp.")
            with self.net.store.tx() as db:
                # "source", not "context": one unit's file changed, not what every function compiles against, so what
                # a model family gave up on stays at the back of its queue (claim_row). As "context", the thousands of
                # unit edits of a night's work put it back in front every few seconds.
                self.net._event(db, proj["id"], agent.id, "source", f"{rel}: {message.strip()[:120]}")
        if any(v is None for v in after.values()):
            return f"⚠️ {rel} updated, but it still doesn't compile (it didn't before your edit either): fix it so its functions can be measured."
        done = "".join(self._file_done(proj["slug"], proj, fp, u, after[u.name] or {}, agent) for u in units[:1])
        return f"✅ {rel} updated; every matched function still matches.{done}"

    def _edit_shared(self, agent: Agent, proj, fp: ForkProject, slug: str, p: Path, rel: str, old: str, new: str,
                     message: str) -> str:
        """Edit a header (or any file several units may use): rebuild and measure the whole project."""
        with self._tree(proj).exclusive():
            existed, text, updated = self._edited(p, rel, old, new)
            failed_before: list[str] = []
            self._build_all(proj, fp, failed=failed_before)  # what already doesn't build, so it isn't blamed on the edit
            game_ok = fp.linkable() and fp.verify()[0]
            broken_before = fp.failed_units(failed_before)
            protected = {(r["unit"], self._meta(r).get("symbol")) for r in self.net.store.all(
                "SELECT unit, meta FROM functions WHERE project_id=? AND status='matched'", proj["id"])}
            p.parent.mkdir(parents=True, exist_ok=True)
            _rewrite(p, updated)

            def undo() -> None:
                if existed:
                    _rewrite(p, text)
                else:
                    p.unlink(missing_ok=True)
                self._build_all(proj, fp)

            failed_after: list[str] = []
            _, log = self._build_all(proj, fp, failed=failed_after)
            broken_after = fp.failed_units(failed_after)
            newly = sorted(broken_after - broken_before)
            if not newly and game_ok and not (ok := fp.verify())[0]:
                undo()
                return (f"❌ after your edit the game no longer builds byte for byte from its linked files, so it was "
                        f"undone:\n{_tail(ok[1])}")
            if newly:
                undo()
                return (f"❌ after your edit {len(newly)} file(s) no longer compile ({', '.join(newly[:5])}), so it was "
                        f"undone. Build output:\n{log[-2500:]}")
            try:
                rep = fp.report()
            except (ObjdiffError, ForkError) as e:
                undo()
                return f"❌ the project couldn't be measured after your edit, so it was undone: {e}"
            lost = protected - _matched_in(rep, broken_after)
            if lost:  # was each of them really matched before the edit? (the database can be behind the tree)
                undo()
                try:
                    rep_before = fp.report()
                except (ObjdiffError, ForkError) as e:
                    return f"❌ the project couldn't be measured, so the edit was undone: {e}"
                truly = sorted(sym for unit, sym in lost & _matched_in(rep_before, broken_before))
                self.sync(slug, log=lambda *_: None, rep=rep_before, broken=broken_before)
                if truly:
                    return f"❌ REJECTED and undone: the edit breaks {len(truly)} matched function(s): {', '.join(truly[:10])}"
                p.parent.mkdir(parents=True, exist_ok=True)
                _rewrite(p, updated)
                self._build_all(proj, fp)
            self._scores = {k: v for k, v in self._scores.items() if k[0] != proj["id"]}
            self._commit(proj, fp, rel, f"{message.strip()}\n\nEdited by {agent.name} with decompdecomp.")
            with self.net.store.tx() as db:
                self.net._event(db, proj["id"], agent.id, "context", f"{rel}: {message.strip()[:120]}")
            self.sync(slug, log=lambda *_: None, rep=rep, broken=broken_after, agent=agent)
        if fp.linkable() and self.link_candidates(proj, fp, rep):
            self.link_soon(slug, agent)
        return f"✅ {rel} updated; every matched function still matches."

    # ------------------------------------------------------------------ for the GUI

    def overview(self, slug: str) -> dict:
        proj, fp, cfg = self.get(slug)
        if cfg.get("ready"):
            self._preflight_once(slug)
        agg = self.net.store.one(f"SELECT {PROGRESS_COLUMNS}, SUM(attempts) AS attempts "
                                 f"FROM functions WHERE project_id=? AND {IN_BUILD}", proj["id"])
        m = cfg.get("measures") or {}
        need = fp.needs(cfg.get("version")) if not cfg.get("ready") else None
        return {
            "slug": slug, "title": proj["name"], "description": proj["description"], "kind": "fork",
            **consoles.get(cfg.get("platform")).project_facts(),
            "repo_url": cfg.get("repo_url"), "fork_kind": cfg.get("fork_kind"), "version": cfg.get("version"),
            "origin": cfg.get("origin"), "compiler_name": cfg.get("compiler"),
            "versions": fp.versions(), "ready": bool(cfg.get("ready")), "needs": need,
            "rom": {"game_code": cfg.get("version") or "", "region": consoles.name(cfg.get("platform")),
                    "title": proj["name"]},
            "compiler": {"toolchain": "project", "name": "Project's own build", "flags": [], "installed": True,
                         "proprietary": False, "supported": True},
            "compilers": [],
            **(self._compiler_facts(fp, cfg) if cfg.get("ready") else {}),
            "functions": agg["total"] or 0, "matched": agg["matched"] or 0, "bytes": agg["bytes"] or 0,
            "matched_bytes": agg["matched_bytes"] or 0, "set_aside": agg["set_aside"] or 0,
            "set_aside_bytes": agg["set_aside_bytes"] or 0, "attempts": agg["attempts"] or 0,
            "context_version": 0, "folder": proj["root"], "room": self.net._room_info(slug),
            "measures": m, "uncovered": self.uncovered(slug) if cfg.get("ready") and fp.linkable() else None,
            "rebuild": (fp.manifest or {}).get("rebuild") if cfg.get("origin") == "scratch" else None,
            **self._scope_facts(proj, fp, cfg),
            # why it doesn't build on this computer (preflight; checked once a run when its page first opens)
            "build_problem": cfg.get("build_problem") if cfg.get("ready") else None,
        }

    def _scope_facts(self, proj, fp: ForkProject, cfg: dict) -> dict:
        """What the project covers, for its page and Settings: `scope`, `left_out` (functions its progress leaves
        out now as not the game's own: part of the overview's `set_aside` count), `scope_list` (what its set-aside
        list names: {functions, without_source}, None: nothing) and `unanalysed` (code its import left unanalysed
        that a new analysis can bring in, Console.unanalysed's words).
        (`scope_list`, not `set_aside`: that's the overview's count of every set-aside function, which the pages take
        off the total; named the same, this dict replaced it and every such project showed "NaN functions".)
        (An older import's manifest may say what it left out as `set_aside_unimported`: the console's own check
        replaces it, so it's no longer read.)"""
        manifest = fp.manifest or {}
        left = self.net.store.one(f"SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND NOT ({GAME_CODE})",
                                  proj["id"])["n"]
        return {"scope": fp.scope if manifest else None, "left_out": left or 0, "scope_list": cfg.get("set_aside"),
                "unanalysed": self._unanalysed(fp, cfg) if cfg.get("origin") == "scratch" else None}

    def _file_parts(self, fp: ForkProject, cfg: dict) -> tuple[str, list[dict]] | None:
        """(the project's own compiler, the parts built with another: one per source file, with its address range),
        for a project whose files each have a compiler (Console.file_compiler: DS); None: its build decides."""
        console = consoles.get(cfg.get("platform"))
        own = console.file_compiler(fp, "")
        if own is None:
            return None
        ranges = console.file_ranges(fp)
        parts = []
        for source in sorted({u.source for u in fp.units() if u.source}):
            if (c := console.file_compiler(fp, source)) != own:
                for start, end in ranges.get(source) or [(0, 0)]:
                    parts.append({"file": source, "start": start, "end": end, "toolchain": c,
                                  "name": consoles.compiler_name(c), "flags": []})
        return own, parts

    def _compiler_facts(self, fp: ForkProject, cfg: dict) -> dict:
        """The overview's compiler fields, as a ROM project's: the Settings panel and its dropdown work the same."""
        found = self._file_parts(fp, cfg)
        if found is None:
            return {}
        own, parts = found
        return {"compiler": {"toolchain": own, "name": consoles.compiler_name(own), "flags": [], "installed": True,
                             "proprietary": False, "supported": True},
                "compilers": [c.id for c in consoles.get(cfg.get("platform")).compilers], "compiler_ranges": parts}

    def compiler_answer(self, slug: str) -> dict:
        """The Settings panel's answer (engine.compiler_answer's form): the project's compiler and the files built
        with another, each kept because its matched functions match with it."""
        _, fp, cfg = self.get(slug)
        found = self._file_parts(fp, cfg)
        if found is None:
            raise NetworkError("this project's own build sets its compilers")
        own, parts = found
        return {"compiler": consoles.compiler_name(own), "flags": "", "mips": False, "elsewhere": [],
                "elsewhere_count": 0, "checking": False, "survey": {}, "checks": [],
                "parts": [{"start": p["start"], "end": p["end"], "compiler": p["name"], "flags": "", "file": p["file"],
                           "check": "confirmed", "said": f"{p['file']}: its matched functions match with it"}
                          for p in parts]}

    def set_compiler_ranges(self, slug: str, ranges: list[list[int]], compiler: str | None) -> str:
        """The Settings panel's "only for these address ranges", where each source file has a compiler: the files
        whose code is in them get `compiler` (None: the project's own again), each only when its matches hold."""
        _, fp, cfg = self.get(slug)
        console = consoles.get(cfg.get("platform"))
        own = console.file_compiler(fp, "")
        if own is None:
            raise NetworkError("this project's own build sets its compilers")
        files = sorted(f for f, spans in console.file_ranges(fp).items()
                       if any(a < e and s < b for a, b in ranges for s, e in spans))
        if not files:
            raise NetworkError("no source file's code is in those ranges")
        done, stayed = [], []
        for f in files:
            try:
                self.set_file_compiler_by_hand(slug, f, compiler or own)
                done.append(f)
            except NetworkError as e:
                stayed.append(str(e))
        if not done:
            raise NetworkError("; ".join(stayed[:4]))
        said = f"{len(done)} source file{'s' if len(done) != 1 else ''} built with {consoles.compiler_name(compiler or own)}"
        return said + (f"; {len(stayed)} stay as they were ({stayed[0]})" if stayed else "") + "."

    def set_compiler_everywhere(self, slug: str, compiler: str, log=print) -> dict:
        """The Settings panel's compiler for the whole game, where each source file has one: every file switches,
        one at a time and only when its matches hold (a minute or two: it's a job), and then it's the project's
        own, the files that stayed keeping theirs."""
        proj, fp, cfg = self.get(slug)
        console = consoles.get(cfg.get("platform"))
        if console.file_compiler(fp, "") is None:
            raise NetworkError("this project's own build sets its compilers")
        if compiler not in {c.id for c in console.compilers}:
            raise NetworkError(f"{compiler!r} isn't one of this platform's compilers")
        sources = sorted({u.source for u in fp.units() if u.source})
        stayed = []
        for n, source in enumerate(sources, 1):
            try:
                self.set_file_compiler_by_hand(slug, source, compiler)
            except NetworkError as e:
                stayed.append(str(e))
            if n % 20 == 0 or n == len(sources):
                log(f"{n} of {len(sources)} source files tried, {len(stayed)} kept their compiler")
        with self._tree(proj).exclusive(), self._build_locks[proj["id"]]:
            changed = console.set_own_compiler(fp, compiler, sources)
        self.net.bump_generation(proj["id"])
        self._commit_paths(proj, fp, changed, f"Build the game with {compiler}\n\nChosen in decompdecomp's project "
                                              f"settings; {len(stayed)} source files keep the compiler they had.")
        self.net._update_cfg(slug, lambda c: c.update(compiler=compiler))
        return {"switched": len(sources) - len(stayed), "stayed": stayed[:20]}

    def set_file_compiler_by_hand(self, slug: str, source: str, compiler: str) -> None:
        """The user's choice of a source file's compiler (the project's Settings), kept only when every function of
        the file that matched still matches with it: the rule try_compiler's switches follow. What matches with it
        now is a match like any other."""
        proj, fp, cfg = self.get(slug)
        console = consoles.get(cfg.get("platform"))
        own = console.file_compiler(fp, source)
        units = fp.units_for(source)
        if own is None or not units:
            raise NetworkError(f"{source} isn't a source file whose compiler this project lets you choose")
        if compiler not in {c.id for c in console.compilers}:
            raise NetworkError(f"{compiler!r} isn't one of this platform's compilers")
        if compiler == own:
            return
        text = (fp.root / source).read_text(errors="replace", encoding="utf-8")
        with self._tree(proj).exclusive(), self._units_locked(proj, [u.name for u in units]):
            before = {u.name: self._baseline(proj, fp, u, text)[0] or {} for u in units}
            with self._build_locks[proj["id"]]:
                rel = console.set_file_compiler(fp, source, compiler)
            after, broken = {}, []
            for u in units:
                ok, log = self._build_unit(proj, fp, u)
                try:
                    after[u.name] = fp.diff(u, None).unit_scores if ok else {}
                except (ObjdiffError, ForkError):
                    after[u.name] = {}
                broken += [n for n, pct in before[u.name].items()
                           if (pct or 0) >= 100 and (after[u.name].get(n) or 0) < 100]
            if broken or not all(after.values()):
                with self._build_locks[proj["id"]]:
                    console.set_file_compiler(fp, source, own)
                for u in units:
                    self._build_unit(proj, fp, u)
                why = (f"{', '.join(sorted(broken)[:8])} no longer match with it" if broken
                       else f"the file doesn't build with it:\n{_tail(log)}")
                raise NetworkError(f"{source} stays with {own}: {why}")
            self._commit(proj, fp, rel, f"Build {source} with {compiler}\n\nChosen in decompdecomp's project settings.")
            self.net.bump_generation(proj["id"])  # (the file's compiler: earlier compiles aren't repeats any more)
            for u in units:
                self._keep_scores(proj, u, text, after[u.name])
                self._apply_scores(proj, u.name, after[u.name], text=text)

    def function_diff(self, slug: str, name: str) -> dict:
        """objdiff's diff of the function as it is in the source tree now."""
        proj, fp, cfg = self.get(slug)
        fn = self._fn(slug, name)
        meta = self._meta(fn)
        try:
            d = fp.diff(fp.unit(fn["unit"]), meta.get("symbol") or fn["name"])
        except (ObjdiffError, ForkError) as e:
            return {"kind": None, "rows": None, "error": f"objdiff: {e}"}
        if d.match_percent is None:
            return {"kind": None, "rows": None}
        problems = [f"a loaded constant differs: {m.describe()}" for m in d.literals]
        problems += [f"refers to `{mine}` where the original refers to `{orig}`" for orig, mine in d.renamed]
        return {"kind": "matched" if fn["status"] == "matched" else "latest", "rows": [list(r) for r in d.rows],
                "score": None, "percent": _floor_pct(d.match_percent, d.matched), "problems": problems}

    def function_detail(self, slug: str, name: str) -> dict:
        proj, fp, cfg = self.get(slug)
        fn = self._fn(slug, name)
        meta = self._meta(fn)
        unit = fp.unit(fn["unit"])
        asm, current, pct = "", None, None
        try:
            d = fp.diff(unit, meta.get("symbol") or fn["name"])
            asm, pct = d.target_asm(), (None if d.match_percent is None else _floor_pct(d.match_percent, d.matched))
        except (ObjdiffError, ForkError) as e:
            asm = f"(objdiff: {e})"
        if unit.source and (fp.root / unit.source).exists():
            text = (fp.root / unit.source).read_text(errors="replace", encoding="utf-8")
            _, span = self._find(text, self._names(meta, fn))
            current = text[span[0]:span[1]] if span else None
        attempts = self.net.store.all("SELECT t.score, t.created_at, a.name AS agent FROM attempts t LEFT JOIN agents a "
                                      "ON a.id=t.agent_id WHERE t.function_id=? ORDER BY t.id", fn["id"])
        notes = self.net.store.all("SELECT n.text, n.created_at, a.name AS agent FROM notes n LEFT JOIN agents a "
                                   "ON a.id=n.agent_id WHERE n.function_id=? ORDER BY n.id DESC LIMIT 10", fn["id"])
        return {"name": fn["name"], "vram": fn["vram"] or 0, "rom_offset": 0, "size": fn["size"], "insns": fn["n_insns"],
                "status": fn["status"], "best": fn["best_score"], "asm": asm, "unit": fn["unit"], **set_aside(fn),
                "source": current or fn["best_source"], "source_kind": "matched" if fn["status"] == "matched" else
                ("latest" if current else "best" if fn["best_source"] else None),
                "match_percent": pct, "scores": [{"score": a["score"], "t": a["created_at"], "agent": a["agent"]} for a in attempts],
                "notes": [dict(n) for n in notes], "calls": [], "called_by": []}
