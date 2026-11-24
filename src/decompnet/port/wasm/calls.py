"""Matched but suspicious: calls whose C disagrees with the function it calls.

Matched C is judged by one compiler. On MIPS a call works whether or not its C agrees with the callee: arguments
and results are just registers. So matched C can take a result from a function it declares void, pass arguments the
callee doesn't declare, leave some out, or call a variadic function as a fixed one, and still build the game's
exact bytes. Any other compiler reads that C as it is written. A WebAssembly or native port, a modern compiler or a
recompiler can each behave differently there; the port's build bridges each one as MIPS behaved (adapt.py), and a
racing game's cars started backwards where it once didn't (docs/PORTING.md).

The build records every bridged call (adapt.note, one JSON line each in build/o/<file>.ll.calls) and gathers them
into build/calls.json (`gather`). Port readiness lists them by kind (`summary`) so the C can be put right while it
still matches: the declarations made to agree, the value returned, the arguments passed.
"""
from __future__ import annotations

import json
from pathlib import Path

# kind: (what it is, why it matters), worst first: the order the report lists them in
KINDS = {
    "void-result": ("takes a result from a function its C declares void",
                    "on MIPS the value is whatever $v0 holds when the callee returns; any other compiler returns "
                    "nothing: declare the callee's real return type and return the value"),
    "variadic-declared-fixed": ("calls a variadic function through a fixed prototype",
                                "its extra arguments go where a fixed function's would, not to the variadic list"),
    "fixed-declared-variadic": ("calls a fixed function through a variadic prototype",
                                "its arguments past the fixed ones go to a variadic list the callee never reads"),
    "by-value-size": ("passes a struct by value of a size the callee doesn't declare",
                      "the callee reads words past the struct on MIPS: declare the size it really takes"),
    "missing-arguments": ("passes fewer arguments than the callee declares",
                          "the rest are whatever the argument registers still hold on MIPS"),
    "argument-type": ("passes an argument of another width or kind than the callee declares",
                      "how it widens (sign or zero) or which registers carry it differs off MIPS"),
    "result-type": ("takes a result of another width or kind than the callee returns",
                    "how it narrows or widens differs off MIPS"),
    "extra-arguments": ("passes more arguments than the callee declares",
                        "the callee may still read them from the argument registers on MIPS"),
}


def gather(o: Path, objects: list[Path] | None = None) -> dict:
    """{caller: [records]} from the build's .calls files (X.be.ll.calls beside X.o; only those of `objects`, the
    ones linked, when given: a file the save no longer has leaves its old records behind)."""
    wanted = None if objects is None else {Path(p).stem for p in objects}
    out: dict[str, list[dict]] = {}
    for f in sorted(Path(o).glob("*.be.ll.calls")):
        if wanted is not None and f.name[:-len(".be.ll.calls")] not in wanted:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:  # an older build's sentence: it says only what a rebuild says better
                continue
            if record.get("kind") in KINDS:
                out.setdefault(record["caller"], []).append(record)
    return out


def write(build: Path, objects: list[Path] | None = None) -> dict:
    """Gather the build's records into build/calls.json; returns them."""
    found = gather(Path(build) / "o", objects)
    (Path(build) / "calls.json").write_text(json.dumps(found, indent=1, sort_keys=True), encoding="utf-8")
    return found


def describe(record: dict) -> str:
    """One call, as a sentence to act on."""
    kind = record["kind"]
    text = f"{record['caller']} → {record['callee']}"
    if kind in ("missing-arguments", "extra-arguments"):
        text += f" ({record['passed']} of {record['declared']})"
    elif kind == "argument-type":
        text += (f" (argument {record['argument']}: {'unsigned ' if record.get('unsigned') else ''}"
                 f"{record['passed']} for {record['declared']})")
    elif kind == "result-type":
        text += f" ({record['passed']} from {record['declared']})"
    elif kind == "by-value-size":
        text += f" (argument {record['argument']}: {record['passed']} bytes for {record['declared']})"
    return text


def summary(found: dict, limit: int = 8, refused: dict | None = None) -> dict:
    """{"functions", "calls", "kinds": [{"kind", "about", "why", "calls", "functions", "examples"}], "callees":
    [{"name", "calls", "callers", "kinds", "refused"}]} worst first. `callees` are the functions whose definitions
    disagree with how they're called (what Make calls agree rewrites), most-called first; `refused` is why the last
    Make calls agree kept one's C (call_fixes.fix), "" when it hasn't tried."""
    by_kind: dict[str, list[dict]] = {}
    for records in found.values():
        for r in records:
            by_kind.setdefault(r["kind"], []).append(r)
    kinds = []
    for kind, (about, why) in KINDS.items():
        records = by_kind.get(kind)
        if records:
            kinds.append({"kind": kind, "about": about, "why": why, "calls": len(records),
                          "functions": len({r["caller"] for r in records}),
                          "examples": [describe(r) for r in records[:limit]]})
    callees: dict[str, list[dict]] = {}
    for records in found.values():
        for r in records:
            callees.setdefault(r["callee"], []).append(r)
    listed = [{"name": name, "calls": len(rs), "callers": len({r["caller"] for r in rs}),
               "kinds": sorted({r["kind"] for r in rs}), "refused": (refused or {}).get(name, "")}
              for name, rs in callees.items()]
    listed.sort(key=lambda c: (-c["calls"], c["name"]))
    return {"functions": len(found), "calls": sum(len(v) for v in found.values()), "kinds": kinds, "callees": listed}


def load(build: Path) -> dict | None:
    """The last build's summary, or None when the game hasn't been built yet."""
    f = Path(build) / "calls.json"
    if not f.is_file():
        return None
    try:
        return summary(json.loads(f.read_text(encoding="utf-8")), refused=load_refused(build))
    except ValueError:
        return None


REFUSED = "calls_refused.json"


def load_refused(build: Path) -> dict:
    """{function: why} Make calls agree kept its C, as the last run left it (call_fixes.fix); {} before one."""
    try:
        return json.loads((Path(build) / REFUSED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
