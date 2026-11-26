"""What a small model's matching loop needs about a project, read in one call each (the match-function skill's
haiku.py, through `decomp judge-worker`): which open functions look most promising, and for one of them everything
that fits one screen. Read-only: it records nothing, claims nothing and writes no draft.

Small models lost runs gathering this a tool at a time, and judged only under the function's current profile while
much of the game needed another one (an N64 game's many -O1 functions), so the profiles travel with the brief.
"""
from __future__ import annotations

import re
import time
from collections import Counter

from .. import seeds, toolchains
from ..network import NetworkError
from ..project import compiler_for

PROFILES = 5  # the function's own, the project's default, then the ones most matched functions use
SHORTLIST = 6  # by saved score per instruction, plus this many small ones that only have a draft
NEIGHBOURS = 6
DECLARATIONS = 30


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


def candidates(net, slug: str, max_insns: int = 150) -> list[dict]:
    """The shortlist of open, unclaimed functions: those with the lowest saved score for their size, then the
    smallest that have only a machine draft. The caller judges them under the candidate profiles and picks."""
    proj = _rom(net, slug)
    rows = _functions(net, proj)
    taken = _claimed(net, proj)
    usable = [fn for fn in rows if fn["status"] == "open" and fn["id"] not in taken and fn["vram"] is not None
              and fn["target"] and (not max_insns or (fn["n_insns"] or 0) <= max_insns)
              and (fn["best_source"] or fn["seed"])]
    scored = sorted((fn for fn in usable if fn["best_score"] is not None),
                    key=lambda fn: fn["best_score"] / max(1, fn["n_insns"] or 1))[:SHORTLIST]
    drafted = sorted((fn for fn in usable if fn["best_score"] is None), key=lambda fn: fn["n_insns"] or 0)[:SHORTLIST]
    return [{"name": fn["name"], "vram": fn["vram"], "n_insns": fn["n_insns"], "target": bytes(fn["target"]).hex(),
             "best_score": fn["best_score"], "best": fn["best_source"] or "", "draft": fn["seed"] or "",
             "matched_near": _near(net, proj, fn, rows), "profiles": profiles(net, proj, fn, rows)}
            for fn in scored + drafted]


def _runs(profile) -> bool:
    tid = profile.get("toolchain") or ""
    if profile.get("cmd") or not tid:
        return True
    try:
        return toolchains.installed(tid) is not None
    except Exception:  # a compiler the registry doesn't pin (a user's kit): the judge says
        return True


def profiles(net, proj, fn, rows=None) -> list[dict]:
    """[{toolchain, flags, uses, own}]: the function's own profile first, then the default and the profiles the
    project's matched functions use most."""
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

    def optional(part):
        try:
            return part(net, proj, fn)
        except Exception:  # noqa: BLE001  (a screen without one section beats no screen)
            return []

    return {"name": fn["name"], "vram": fn["vram"], "n_insns": fn["n_insns"], "status": fn["status"],
            "target": bytes(fn["target"] or b"").hex(), "asm": net.rename_tokens(proj, fn["asm"] or ""),
            "best_score": fn["best_score"], "best": fn["best_source"] or "", "draft": draft(net, proj, fn),
            "profiles": profiles(net, proj, fn, rows), "neighbours": optional(neighbours),
            "declarations": optional(declarations)}
