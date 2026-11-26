"""What a small model's matching loop needs about a project, read in one call each (the match-function skill's
haiku.py, through `decomp judge-worker`): which open functions look most promising, and for one of them everything
that fits one screen. Read-only: it records nothing, claims nothing and writes no draft.

About 130 small-model runs on one N64 game set what travels with them: the same functions that had just failed were
picked again, a near-miss under another profile was lost with its session, and look-alikes' matched C could not be
read from the project's files, so agents searched the whole data home for it.

Small models lost runs gathering this a tool at a time, and judged only under the function's current profile while
much of the game needed another one (an N64 game's many -O1 functions), so the profiles travel with the brief.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter

from .. import seeds, similarity, toolchains
from ..network import NetworkError
from ..project import compiler_for

PROFILES = 5  # the function's own, the project's default, then the ones most matched functions use
SHORTLIST = 6  # by saved score per instruction, plus this many small ones that only have a draft
NEIGHBOURS = 6
DECLARATIONS = 30
RECENT_SECONDS = 24 * 3600  # worked on more recently than this: picked after everything else
# library code by its name (libultra, nusys, the audio library, compiler runtime): not worth a small model's run
LIBRARY = re.compile(r"^(?:nu[A-Z]|n_al|au[A-Z]|os[A-Z]|__)")
BACKGROUND = re.compile(r"^(?:autopilot|progress-judge)|permuter")  # the app's own passes over every function
TWIN = 0.75  # a matched function this similar in instruction shape is worth copying
TRIALS = 3


def _rom(net, slug):
    proj = net.project_row(slug)
    if net.cfg(proj).get("kind") == "fork":
        raise NetworkError(f"{slug} is a project-mode (objdiff) project; this loop only works on ROM projects")
    return proj


def _functions(net, proj):
    return net.project_store(proj["id"]).all(
        "SELECT * FROM functions WHERE project_id=? AND gone_mark IS NULL ORDER BY vram", proj["id"])


def _claimed(net, proj) -> set[int]:
    return {row["function_id"] for row in net.project_store(proj["id"]).all(
        "SELECT function_id FROM claims WHERE expires_at>?", time.time())}


def _near(net, proj, fn, rows) -> int:
    """How many matched functions sit beside `fn`: callers, callees and the five before and after it."""
    x = net.xrefs(proj)
    status = {row["vram"]: row["status"] for row in rows}
    at = next((i for i, row in enumerate(rows) if row["id"] == fn["id"]), 0)
    near = {row["vram"] for row in rows[max(0, at - 5):at + 6]}
    near |= set(x["calls"].get(fn["vram"], ())) | set(x["called_by"].get(fn["vram"], ()))
    near.discard(fn["vram"])
    return sum(status.get(vram) == "matched" for vram in near)


def worked(net, proj, rows) -> dict[int, float]:
    """{function id: when a model last worked on it}, within RECENT_SECONDS: its claims, releases, submissions and
    notes. The app's own passes touch everything, so they don't count."""
    since = time.time() - RECENT_SECONDS
    store = net.project_store(proj["id"])
    background = {row["id"] for row in net.store.all("SELECT id, name FROM agents")
                  if BACKGROUND.search(row["name"] or "")}
    ids = {fn["name"]: fn["id"] for fn in rows}
    out: dict[int, float] = {}

    def add(function_id, agent_id, at):
        if function_id is not None and agent_id not in background:
            out[function_id] = max(at, out.get(function_id, 0))

    for row in store.all("SELECT function_id, agent_id, created_at FROM attempts WHERE via='agent' AND created_at>?",
                         since):
        add(row["function_id"], row["agent_id"], row["created_at"])
    for row in store.all("SELECT function_id, agent_id, created_at FROM notes WHERE project_id=? AND created_at>? "
                         "AND text NOT LIKE 'Deep permuter:%'", proj["id"], since):
        add(row["function_id"], row["agent_id"], row["created_at"])
    for row in store.all("SELECT detail, agent_id, created_at FROM events WHERE project_id=? AND created_at>? "
                         "AND kind IN ('claim', 'release')", proj["id"], since):
        add(ids.get(row["detail"]), row["agent_id"], row["created_at"])
    return out


def trials(net, proj, fn) -> list[dict]:
    """The function's best recorded compiler trials under profiles other than its own (compiler_experiments' durable
    records, where haiku.py keeps a session's near-miss): [{score, toolchain, flags, source}], best first."""
    store = net.project_store(proj["id"])
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='compiler_trial_evidence'"):
        return []
    own = compiler_for(net.cfg(proj), fn["vram"], fn)
    found = {}
    for row in store.all("SELECT record FROM compiler_trial_evidence WHERE project_id=? AND function_id=? "
                         "ORDER BY created_at", proj["id"], fn["id"]):
        try:
            record = json.loads(row["record"])
            result, source = record["result"], record["source"]
            score = 0 if result.get("exact") else result.get("score")
            toolchain, flags = record["compiler"], str(record.get("flags") or "").split()
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        same = toolchain == own.get("toolchain") and flags == list(own.get("flags") or [])
        if score is not None and source.strip() and not same:
            found[source, toolchain, tuple(flags)] = {"score": score, "toolchain": toolchain, "flags": flags,
                                                      "source": source}
    return sorted(found.values(), key=lambda trial: trial["score"])[:TRIALS]


def twin(net, proj, fn, rows) -> dict | None:
    """The matched function whose instructions (opcode and function fields, in order) are most like `fn`'s, at least
    TWIN similar, with its matched C and the profile that built it."""
    if proj["arch"] != "mips" or not fn["target"]:
        return None
    cpu = net.console(proj).cpu_variant

    def shape(row):
        return similarity.mips_mnemonics(bytes(row["target"]), bool(proj["big_endian"]), cpu=cpu)

    want = shape(fn)
    best, value = None, TWIN
    for row in rows:
        if len(want) < 4 or row["status"] != "matched" or row["id"] == fn["id"] or not row["target"] \
                or not row["matched_source"]:
            continue
        size = len(row["target"]) // 4
        if 2 * min(size, len(want)) / (size + len(want)) < value:  # no ratio can be higher than this
            continue
        found = similarity.score(want, shape(row))
        if found >= value:
            best, value = row, found
    if best is None:
        return None
    profile = compiler_for(net.cfg(proj), best["vram"], best)
    return {"name": best["name"], "similarity": round(value, 2), "n_insns": best["n_insns"],
            "toolchain": profile.get("toolchain"), "flags": list(profile.get("flags") or []),
            "source": best["matched_source"]}


def candidates(net, slug: str, max_insns: int = 150) -> list[dict]:
    """The shortlist of open, unclaimed functions: those with the lowest saved score for their size, then the
    smallest that have only a machine draft. Each earlier give-up counts against one, and library code is left out.
    Ones a model worked on lately come last, those given up least first and the longest untouched among equals: once a
    run had tried every small function, ordering them by time alone handed one that had failed eight times out as
    often as one that had failed once. The caller judges them under the candidate profiles and picks."""
    proj = _rom(net, slug)
    rows = _functions(net, proj)
    taken = _claimed(net, proj)
    recent = worked(net, proj, rows)
    usable = [fn for fn in rows if fn["status"] == "open" and fn["id"] not in taken and fn["vram"] is not None
              and fn["target"] and (not max_insns or (fn["n_insns"] or 0) <= max_insns)
              and (fn["best_source"] or fn["seed"]) and not LIBRARY.match(fn["name"])]
    def lately(fn):
        at = recent.get(fn["id"], 0)
        return bool(at), (fn["abandons"] or 0) if at else 0, at

    scored = sorted((fn for fn in usable if fn["best_score"] is not None), key=lambda fn: (
        *lately(fn), fn["best_score"] / max(1, fn["n_insns"] or 1) * (1 + (fn["abandons"] or 0))))
    drafted = sorted((fn for fn in usable if fn["best_score"] is None), key=lambda fn: (
        *lately(fn), fn["abandons"] or 0, fn["n_insns"] or 0))
    out = []
    for fn in scored[:SHORTLIST] + drafted[:SHORTLIST]:
        kept = trials(net, proj, fn)[:1]
        out.append({"name": fn["name"], "vram": fn["vram"], "n_insns": fn["n_insns"],
                    "target": bytes(fn["target"]).hex(), "best_score": fn["best_score"],
                    "best": fn["best_source"] or "", "draft": fn["seed"] or "",
                    "near": kept[0]["source"] if kept else "", "worked": recent.get(fn["id"], 0),
                    "abandons": fn["abandons"] or 0, "matched_near": _near(net, proj, fn, rows),
                    "profiles": profiles(net, proj, fn, rows, kept)})
    return out


def _runs(profile) -> bool:
    tid = profile.get("toolchain") or ""
    if profile.get("cmd") or not tid:
        return True
    try:
        return toolchains.installed(tid) is not None
    except Exception:  # a compiler the registry doesn't pin (a user's kit): the judge says
        return True


def profiles(net, proj, fn, rows=None, also=()) -> list[dict]:
    """[{toolchain, flags, uses, own}]: the function's own profile first, then the default and the profiles the
    project's matched functions use most, then those of `also` ([{toolchain, flags}]: a near-miss's, a twin's)."""
    cfg = net.cfg(proj)
    rows = rows or _functions(net, proj)

    def key(profile):
        return profile.get("toolchain") or "", tuple(profile.get("flags") or ())

    used = Counter(key(compiler_for(cfg, row["vram"], row)) for row in rows if row["status"] == "matched")
    own = key(compiler_for(cfg, fn["vram"], fn))
    order = [own, key(cfg["compiler"]), *(k for k, _ in used.most_common())]
    out = []
    for toolchain, flags in dict.fromkeys(order):
        if len(out) < PROFILES and ((toolchain, flags) == own or _runs({"toolchain": toolchain})):
            out.append({"toolchain": toolchain, "flags": list(flags), "uses": used[toolchain, flags],
                        "own": (toolchain, flags) == own})
    for extra in also:
        toolchain, flags = key(extra)
        if not any(key(p) == (toolchain, flags) for p in out) and _runs({"toolchain": toolchain}):
            out.append({"toolchain": toolchain, "flags": list(flags), "uses": used[toolchain, flags], "own": False})
    return out


def draft(net, proj, fn) -> str:
    """m2c's draft made now with the shared header's declarations; the stored one when it can't be made."""
    try:
        _, toolchain, context = net._draft_plan(fn, proj)
        made = seeds.generate_from_asm(net._asm_text(fn, proj), fn["name"], toolchain, context,
                                       "\n".join(net._jtbl_asm(proj, fn)), valid=True, timeout=20)
        if made:
            return seeds.with_m2c_macros(made)
    except Exception:  # noqa: BLE001  (no m2c for this compiler, or m2c failed: the stored draft will do)
        pass
    return fn["seed"] or ""


def signature(source: str, name: str) -> str:
    """The definition line of `name` in its matched C, without the body."""
    found = re.search(rf"^[^\n#/;{{}}]*\b{re.escape(name)}\s*\([^;{{}}]*\)", source or "", re.M)
    return " ".join(found[0].split()) if found else ""


def neighbours(net, proj, fn) -> list[dict]:
    from . import matched_neighbours
    out = []
    for item in matched_neighbours.gather(net, proj, fn, NEIGHBOURS):
        line = signature(item.get("source") or "", item["name"])
        out.append({"name": item["name"], "n_insns": item["n_insns"], "relations": item["relations"],
                    "signature": line})
    return out


def declarations(net, proj, fn) -> list[str]:
    """The shared header's declarations of what the function's assembly names (its globals and callees)."""
    ctx = net._context(proj["id"], fn["unit"])
    roots = net._current_names(proj, seeds.asm_names(fn["asm"] or ""))
    used = net._header_index(proj["id"], ctx, False).used(roots)
    own = [d.text.strip() for d in used if set(d.names or ()) & (roots | {fn["name"]})]
    return [" ".join(text.split()) for text in own][:DECLARATIONS]


def brief(net, slug: str, name: str) -> dict:
    proj = _rom(net, slug)
    rows = _functions(net, proj)
    found = [fn for fn in rows if fn["name"] == name]
    if len(found) != 1:
        raise NetworkError(f"{slug} has {len(found)} functions named {name!r}")
    fn = found[0]

    def optional(part, *more):
        try:
            return part(net, proj, fn, *more)
        except Exception:  # noqa: BLE001  (a screen without one section beats no screen)
            return []

    kept, alike = optional(trials), optional(twin, rows) or None

    return {"name": fn["name"], "vram": fn["vram"], "n_insns": fn["n_insns"], "status": fn["status"],
            "target": bytes(fn["target"] or b"").hex(), "asm": net.rename_tokens(proj, fn["asm"] or ""),
            "best_score": fn["best_score"], "best": fn["best_source"] or "", "draft": draft(net, proj, fn),
            "profiles": profiles(net, proj, fn, rows, kept[:1] + ([alike] if alike else [])),
            "trials": kept, "twin": alike, "neighbours": optional(neighbours),
            "declarations": optional(declarations)}
