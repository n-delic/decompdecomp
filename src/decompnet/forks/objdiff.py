"""objdiff-cli: the community's diff engine, for every architecture it supports."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from decompnet.hostenv import NO_WINDOW

from .. import toolchains
from ..network import NetworkError

KIND_MARK = {None: " ", "DIFF_NONE": " ", "DIFF_REPLACE": "|", "DIFF_DELETE": "<", "DIFF_INSERT": ">",
             "DIFF_OP_MISMATCH": "|", "DIFF_ARG_MISMATCH": "i"}

# How `objdiff-cli report generate` diffs (its base config, before the project's and unit's own options): progress
# reports, decomp.dev and this app's sync count a function as matched by these rules, where a relocation's target
# may have another name (the compiler's @91 for the original's lbl_802C2BD0). Every per-function diff uses them too,
# so a match is a match everywhere; with objdiff's interactive defaults the same function would read 99.9%.
REPORT_OPTIONS = {"functionRelocDiffs": "none", "combineDataSections": True, "combineTextSections": True,
                  "ppc.calculatePoolRelocations": False}
# Symbol names compilers make up for literals and pools, which never have to match the original's names. Visual C++'s
# $L and $T label its own exception-handling code and tables in the object (.text$x, .xdata$x): a function with a
# local object's destructor pushes its handler, $L278, where the delinked original names the stub func_006450E8, and
# the code still links, with its own handler. A jump table's labels too: Visual C++ 2017's $LN4, and $L0040101B, the
# label a PC project's delinked function names a place in itself by (tools/delink.py): local to the function.
_GENERATED = re.compile(r"^(@\d+|\$LC\d+|\$[LT]\d+|\$LN\d+|\$L[0-9A-F]{8,16}|_\$E\d+|\.\.\..*|@stringBase\d*|\.L.*)$")
# An LTCG unit's checks file that can't be read (being rewritten as the unit builds): tries and the wait between
SIDECAR_TRIES, SIDECAR_WAIT = 5, 0.2


class ObjdiffError(NetworkError):
    pass


def _exe(log=print) -> str:
    toolchains.ensure("objdiff-cli", log=log)
    return str(toolchains.binary("objdiff-cli"))


def judge_options(project_options: dict | None = None, unit_options: dict | None = None) -> dict:
    """The diff settings the progress report uses for a unit: the report's own, then the project's, then the unit's."""
    return {**REPORT_OPTIONS, **(project_options or {}), **(unit_options or {})}


def _run(args: list[str], cwd: Path, timeout: float = 600) -> str:
    res = subprocess.run([_exe(), "--no-color", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if res.returncode != 0:
        raise ObjdiffError((res.stderr or res.stdout).strip()[-1500:] or f"objdiff-cli exited with {res.returncode}")
    return res.stdout


def report(project: Path, out: Path) -> dict:
    """Progress report (objdiff report.proto as JSON) for a project with an objdiff.json.

    objdiff-cli stops at the first unit whose compiled object is missing (work-in-progress source that doesn't
    compile yet, or that the project's default build never compiles). Those units are measured from the game's
    object alone, as not matched yet, the way the projects' own report scripts do it."""
    cfg = json.loads((project / "objdiff.json").read_text(encoding="utf-8"))
    missing = [u for u in cfg.get("units", []) if u.get("base_path") and not (project / u["base_path"]).is_file()]
    if not missing:
        _run(["report", "generate", "-p", str(project), "-o", str(out)], cwd=project, timeout=1800)
        return subtract_conflicts(project, json.loads(out.read_text(encoding="utf-8")))
    for unit in cfg.get("units", []):
        for key in ("target_path", "base_path"):
            if unit.get(key):
                unit[key] = str((project / unit[key]).resolve())
    for unit in missing:
        del unit["base_path"]
    with tempfile.TemporaryDirectory(prefix="decompwtf-report-") as tmp:
        (Path(tmp) / "objdiff.json").write_text(json.dumps(cfg), encoding="utf-8")
        _run(["report", "generate", "-p", tmp, "-o", str(out.resolve())], cwd=project, timeout=1800)
    return subtract_conflicts(project, json.loads(out.read_text(encoding="utf-8")))


@dataclass
class FunctionDiff:
    symbol: str | None
    match_percent: float | None  # None: the function isn't in the base object (not written yet)
    rows: list[tuple[str, str, str]]  # (marker, target, base)
    target_size: int
    unit_scores: dict[str, float | None]  # every function in the unit -> match % (None: not in your build)
    order: list[str]  # the unit's functions in target address order
    ops: dict[str, list[str]]  # the unit's functions -> target mnemonics (for finding look-alikes)
    # (original, yours) symbol names of references that point at differently named things: objdiff's rules accept
    # them, but the game's link resolves names, so they're worth fixing before the code goes upstream
    renamed: list[tuple[str, str]] = field(default_factory=list)
    literals: list = field(default_factory=list)  # literals.Mismatch: constants that differ although the code matches
    refs: list[str] = field(default_factory=list)  # the symbols the original's relocations name, in order

    @property
    def matched(self) -> bool:
        return self.match_percent is not None and self.match_percent >= 100.0 and not self.literals

    def why_not(self) -> str:
        """Why a function every instruction of which matches isn't a match, in a few words."""
        if self.literals and all(isinstance(m, MappingConflict) for m in self.literals):
            return "every instruction matches, but it refers to other globals or callees than the original"
        return "every instruction matches, but loaded constants differ"

    def literals_note(self) -> str:
        if not self.literals:
            return ""
        if all(isinstance(m, MappingConflict) for m in self.literals):  # an LTCG unit's address and data checks
            listing = "\n".join(f"- {m.describe()}" for m in self.literals[:6])
            kinds = []
            if any(not isinstance(m, DataConflict) for m in self.literals):
                kinds.append("one original address pairs with two of yours, or the other way round")
            if any(isinstance(m, DataConflict) for m in self.literals):
                kinds.append("a reference names another original address than the one the original uses there, "
                             "or a literal has another value")
            return (f"NOT A MATCH YET: {self.why_not()} ({'; '.join(kinds)}):\n{listing}\n"
                    "Use the same global, literal or callee where the original does (in the source, or in the driver "
                    "and stand-ins).")
        listing = "\n".join(f"- {m.describe()}" for m in self.literals[:6])
        return ("NOT A MATCH YET: every instruction matches, but constants it loads differ from the original's:\n"
                f"{listing}\nWrite the literal with the original's value (float vs double, the exact digits).")

    def target_asm(self) -> str:
        return "\n".join(t for _, t, _ in self.rows if t)

    def render(self, context: int = 2, max_rows: int = 60) -> str:
        from ..scoring import render_rows

        return render_rows(self.rows, context, max_rows, legend=LEGEND)


LEGEND = "markers: |=different instruction i=operand differs <=missing (target only) >=extra (yours only)"


def expand_set_aside(data: dict, info: dict) -> dict[str, str]:
    """A project's set_aside.json as symbol -> why: its symbol entries (library code an import found), plus every
    function of functions.json (`info`) in its `ranges`: [{"from": "0x...", "to": "0x...", "why": ...}] (`to`
    exclusive), or listed in its `functions`: {"0x<address>": why}. That is for code that isn't the game's own,
    middleware (a graphics or sound engine), an SDK or the C runtime, which an import of an existing decompilation lists."""
    out = {k: v for k, v in data.items() if isinstance(v, str)}
    ranges = []
    for r in data.get("ranges") or []:
        try:
            ranges.append((int(str(r["from"]), 0), int(str(r["to"]), 0), str(r.get("why") or "not the game's code")))
        except (KeyError, TypeError, ValueError):
            continue
    listed = {}
    for k, v in (data.get("functions") or {}).items():
        try:
            listed[int(str(k), 0)] = str(v)
        except ValueError:
            continue
    if ranges or listed:
        for fns in (info.get("units") or {}).values():
            for fn in fns:
                why = listed.get(fn["addr"]) or next((w for lo, hi, w in ranges if lo <= fn["addr"] < hi), None)
                if why is not None:
                    out.setdefault(fn["name"], why)
    return out


def game_measures(rep: dict, aside: set[str]) -> dict:
    """The report's measures counting only the game's own functions: the set-aside ones (library code, middleware)
    are left out of the totals as well as the matches, so `matched_code_percent` is the share of the game done.
    The report's own figures stay in `all_functions`, `all_code` when anything was left out, and
    `set_aside_functions` says how many functions were. A project covering the whole program passes no `aside`."""
    m = dict(rep.get("measures") or {})
    if not aside:
        return m
    fns = code = done_fns = done_code = left = 0
    for unit in rep.get("units", []):
        for f in unit.get("functions", []):
            if f.get("name") in aside:
                left += 1
                continue
            size = int(f.get("size") or 0)
            fns, code = fns + 1, code + size
            if float(f.get("fuzzy_match_percent") or 0) >= 100.0:
                done_fns, done_code = done_fns + 1, done_code + size
    m["all_functions"], m["all_code"] = m.get("total_functions"), m.get("total_code")
    m.update(total_functions=fns, matched_functions=done_fns, total_code=str(code), matched_code=str(done_code),
             matched_functions_percent=100.0 * done_fns / fns if fns else 0.0,
             matched_code_percent=100.0 * done_code / code if code else 0.0, set_aside_functions=left)
    return m


@dataclass
class MappingConflict:
    """A 100% function whose references don't map one to one onto the original's (tools/dll2coff.py, LTCG units):
    objdiff, as decompdecomp runs it, doesn't compare what relocations point at, so swapped or split globals and
    callees would pass. Kept with the literal mismatches, the other '100% but not a match' finding."""

    text: str

    def describe(self) -> str:
        return self.text


@dataclass
class DataConflict(MappingConflict):
    """A 100% function with a reference that stands for another original address than the original's reference
    there (a D_<address>, func_<address> or `// GLOBAL:` global), or a literal of another value (dll2coff's data
    check): two globals used once each and swapped keep the mapping one to one, and only this sees them."""


def _sidecar(base: Path) -> dict | None:
    """The address mapping and data checks of an LTCG unit's functions (<object>.ltcg.json): {} for a unit with none,
    None for one whose file can't be read. dll2coff rewrites it each time the unit builds, an agent's compile or the
    permuter's; read then, it once looked like "no conflicts" and the progress report matched a 100% function with
    two swapped globals, twice. A read that fails is tried again (the rewrite takes milliseconds); after that the
    unit's checks are unknown, and callers count its functions as not confirmed. The checks name the object they're
    of (`object_sha1`, written before the object): checks of another object (the object's being written, or is
    another build's) are unknown too, and so are checks that name none (written before they did: a unit that left
    harness mode kept them, and they confirmed its 100%) and missing checks of a unit the build makes them for."""
    path = base.with_suffix(".ltcg.json")
    for _ in range(SIDECAR_TRIES):
        if not path.is_file():  # (nothing half-written to wait for: dll2coff writes the checks before the object)
            return None if checked(base) else {}
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
            of = found.get("object_sha1")
            if of is None:
                return None
            if of == hashlib.sha1(base.read_bytes()).hexdigest():
                return found.get("functions", {}) or {}
        except (OSError, ValueError, AttributeError):
            pass
        time.sleep(SIDECAR_WAIT)
    return None


_BUILD_FILES: dict[Path, tuple[float, str]] = {}  # build.ninja -> (its mtime, its text)


def checked(base: Path) -> bool:
    """Whether the project's build makes checks for this object: its build.ninja names <object>.ltcg.json as an
    output (tools/ltcg.py, a unit built in harness mode)."""
    for root in base.parents:
        ninja = root / "build.ninja"
        try:
            mtime = ninja.stat().st_mtime
        except OSError:
            continue
        if (cached := _BUILD_FILES.get(ninja)) is None or cached[0] != mtime:
            try:
                cached = _BUILD_FILES[ninja] = (mtime, ninja.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                return False
        rel = base.with_suffix(".ltcg.json").relative_to(root).as_posix()
        return re.search(r"(?<!\S)" + re.escape(rel) + r"(?=[\s:]|$)", cached[1], re.M) is not None
    return False


def ltcg_problems(base: Path | None) -> list[str]:
    """What dll2coff said about an LTCG unit's harness link (<object>.ltcg.json `problems`: a function it couldn't
    find in the map, an annotation of another unit's function...); none for other units."""
    if base is None:
        return []
    try:
        found = json.loads(base.with_suffix(".ltcg.json").read_text(encoding="utf-8")).get("problems") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [str(p) for p in found]


def mapping_conflicts(base: Path | None, symbol: str | None) -> list[MappingConflict]:
    """The address mapping and data checks dll2coff wrote next to an LTCG unit's object (<object>.ltcg.json), for
    a function."""
    if base is None or symbol is None:
        return []
    side = _sidecar(base)
    if side is None:
        return [MappingConflict(f"the unit's address checks ({base.with_suffix('.ltcg.json').name}) are missing, "
                                "couldn't be read, or are of another build of it, so this 100% isn't confirmed; it's "
                                "being rebuilt, or was built before its checks named their object: compile again")]
    found = side.get(symbol) or {}
    return [MappingConflict(c) for c in found.get("conflicts", [])] + [DataConflict(c) for c in found.get("data", [])]


def subtract_conflicts(project: Path, rep: dict) -> dict:
    """The progress report with LTCG functions that fail the mapping or data check counted as not matched:
    `report generate` counts objdiff's 100% alone. Each such function's match drops just below 100%, and the
    unit's and the report's matched counts and code lose it."""
    try:
        cfg = json.loads((project / "objdiff.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return rep
    bases = {u.get("name"): project / u["base_path"] for u in cfg.get("units", []) if u.get("base_path")}
    lost_fns, lost_code = 0, 0
    for unit in rep.get("units", []):
        base = bases.get(unit.get("name"))
        if base is None or not (base.with_suffix(".ltcg.json").is_file() or checked(base)):
            continue
        side = _sidecar(base)
        u_fns, u_code = 0, 0
        for f in unit.get("functions", []):
            if side is None:  # its checks couldn't be read: no 100% of the unit is confirmed
                bad = {"conflicts": ["unread"]}
            else:
                bad = side.get(f.get("name")) or {}
            if (bad.get("conflicts") or bad.get("data")) and float(f.get("fuzzy_match_percent") or 0) >= 100.0:
                f["fuzzy_match_percent"] = 99.99
                u_fns, u_code = u_fns + 1, u_code + int(f.get("size") or 0)
        if u_fns:
            _lose(unit.get("measures"), u_fns, u_code)
            lost_fns, lost_code = lost_fns + u_fns, lost_code + u_code
    if lost_fns:
        _lose(rep.get("measures"), lost_fns, lost_code)
    return rep


def _lose(measures: dict | None, fns: int, code: int) -> None:
    """Take matched functions and code off a report's measures (proto JSON writes 64-bit counts as strings)."""
    if not measures:
        return

    def less(key: str, n: int) -> None:
        if key in measures:
            value = max(0, int(measures[key]) - n)
            measures[key] = str(value) if isinstance(measures[key], str) else value

    less("matched_functions", fns)
    less("matched_code", code)
    for count, total, pct in (("matched_functions", "total_functions", "matched_functions_percent"),
                              ("matched_code", "total_code", "matched_code_percent")):
        if pct in measures and int(measures.get(total) or 0):
            measures[pct] = 100.0 * int(measures.get(count) or 0) / int(measures[total])


def _sym(obj: dict | None, name: str | None) -> dict | None:
    """The symbol `name` an object defines. A function the object only refers to (objdiff lists undefined symbols
    too, with the same name and kind and no size or code) isn't it: an object that calls a function it no longer
    defines (a unit whose definition went missing, a build step that left a call to it behind) had that
    function read 100%, its code compared with nothing, while the progress report rightly said it wasn't built, so
    every full measure reopened it and the next compile "matched" it again."""
    hits = [s for s in (obj or {}).get("symbols", []) if s.get("name") == name]
    for s in hits:
        if s.get("kind") != "SYMBOL_FUNCTION" or int(s.get("size") or 0) > 0 or s.get("instructions"):
            return s
    return None


# the PC bias ARM's branch relocations carry as their addend (the CPU reads pc 8 bytes ahead, 4 in Thumb): objdiff
# shows it, "bl func_0203aca8-0x8", and on a DS game a model spent a minute working out it was no offset at all
PC_BIAS = {"R_ARM_PC24": -8, "R_ARM_CALL": -8, "R_ARM_JUMP24": -8, "R_ARM_THM_CALL": -4, "R_ARM_THM_PC22": -4,
           "R_ARM_THM_JUMP24": -4}


def _defined_functions(side: dict | None) -> list[dict]:
    """The functions an object defines, once each. objdiff lists an object's undefined symbols too, with the same
    kind and no size or code: a COFF object can carry a function both ways (the PC template's delink.py gave every
    call inside a unit an undefined copy of the callee's name), and the undefined copy, scored None, came after the
    defined one and hid its score from the unit-wide report (free functions that matched went back to open after
    the next attempt in their unit; methods, called through vtables, kept theirs). Undefined callees from other
    units, scored None too, kept a finished file from ever counting as done."""
    out: dict[str, dict] = {}
    for s in (side or {}).get("symbols", []):
        if s.get("kind") == "SYMBOL_FUNCTION" and (int(s.get("size") or 0) > 0 or s.get("instructions")):
            out.setdefault(s["name"], s)
    return list(out.values())


def _fmt(row: dict) -> str:
    ins = row.get("instruction")
    if not ins:
        return ""
    text, rel = ins.get("formatted", ""), ins.get("relocation") or {}
    bias = PC_BIAS.get(rel.get("type_name") or "")
    if bias is not None and str(rel.get("addend")) == str(bias):
        text = text.removesuffix(f"-0x{-bias:x}")
    return text


def _option(value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _reloc_name(side: dict | None, row: dict) -> str | None:
    ins = row.get("instruction") or {}
    rel = ins.get("relocation")
    syms = (side or {}).get("symbols", [])
    if rel is None:
        return None
    idx = int(rel.get("target_symbol") or 0)
    return syms[idx].get("name") if 0 <= idx < len(syms) else None


def _renamed(left: dict | None, right: dict | None, lrows: list[dict], rrows: list[dict]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for lr, rr in zip(lrows, rrows):
        a, b = _reloc_name(left, lr), _reloc_name(right, rr)
        if a and b and a != b and not _GENERATED.match(a) and not _GENERATED.match(b) and (a, b) not in out:
            out.append((a, b))
    return out


def diff(target: Path | None, base: Path | None, symbol: str | None, cwd: Path, options: dict | None = None,
         project: Path | None = None, unit: str | None = None) -> FunctionDiff:
    """Diff one function between the target (expected) and base (yours) objects; symbol None measures the whole
    unit (unit_scores) without one function's rows.

    `options` are the settings to diff with (judge_options: the progress report's), so percentages agree with the
    report. With `project` and `unit`, objdiff reads the unit from objdiff.json, symbol mappings included."""
    args = ["diff", "-o", "-", "--format", "json"]
    for key, value in (options or {}).items():
        args += ["-c", f"{key}={_option(value)}"]
    if project is not None and unit and base is not None and base.exists():
        args += ["-p", str(project), "-u", unit]
    else:
        if target is not None and target.exists():
            args += ["-1", str(target)]
        if base is not None and base.exists():
            args += ["-2", str(base)]
    data = json.loads(_run([*args, *([symbol] if symbol else [])], cwd=cwd, timeout=120))
    left, right = _sym(data.get("left"), symbol), _sym(data.get("right"), symbol)
    if left is None and symbol is not None:
        raise ObjdiffError(f"{symbol} isn't in the target object")
    left = left or {}
    # An extern reference has the same name but no implementation. Its target's unpaired instructions have no
    # diff markers either: treating those as equal made agents stop on unwritten functions reading "100%".
    if right is not None and (right.get("kind") != "SYMBOL_FUNCTION" or not right.get("instructions")):
        right = None
    lrows = left.get("instructions", [])
    rrows = right.get("instructions", []) if right else []
    rows = []
    for k in range(max(len(lrows), len(rrows))):
        lr = lrows[k] if k < len(lrows) else {}
        rr = rrows[k] if k < len(rrows) else {}
        kind = lr.get("diff_kind") or rr.get("diff_kind")
        rows.append((KIND_MARK.get(kind, "|"), _fmt(lr), _fmt(rr)))
    pct = right.get("match_percent", left.get("match_percent")) if right else None
    if symbol is None:
        right = None
    if right is not None and pct is None:
        pct = 100.0 if lrows and len(lrows) == len(rrows) and all(m == " " for m, _, _ in rows) else 0.0
    built = {s.get("name") for s in (data.get("right") or {}).get("symbols", [])
             if s.get("kind") == "SYMBOL_FUNCTION" and s.get("instructions")}
    # (not an extern reference: a PC game's delinked object names a function it calls again, as an extern, beside
    # its definition, and that later entry scored a matched function "not in your build", which reopened it)
    funcs = _defined_functions(data.get("left"))
    scores = {s["name"]: (float(s["match_percent"]) if s.get("match_percent") is not None and s["name"] in built else None)
              for s in funcs}
    order = [s["name"] for s in sorted(funcs, key=lambda s: int(s.get("address") or 0))]
    ops = {s["name"]: [r["instruction"]["formatted"].split(None, 1)[0] for r in s.get("instructions", [])
                       if r.get("instruction") and r["instruction"].get("formatted")] for s in funcs}
    renamed = _renamed(data.get("left"), data.get("right"), lrows, rrows) if right is not None else []
    wrong = []
    if (symbol is not None and pct is not None and float(pct) >= 100.0
            and target is not None and base is not None and base.exists()):
        from . import literals

        insns, at = [], 0
        for r in lrows:
            ins = r.get("instruction") or {}
            if ins.get("formatted"):
                insns.append((at, ins["formatted"].split(None, 1)[0]))
            at += int(ins.get("size") or 4)
        wrong = literals.mismatches(target, base, symbol, insns)
        wrong += mapping_conflicts(base, symbol)  # (an LTCG unit's address and data checks, tools/dll2coff.py)
    refs = list(dict.fromkeys(n for lr in lrows if (n := _reloc_name(data.get("left"), lr))))
    return FunctionDiff(symbol, float(pct) if pct is not None else None, rows, int(left.get("size", 0)), scores, order, ops,
                        renamed, wrong, refs)
