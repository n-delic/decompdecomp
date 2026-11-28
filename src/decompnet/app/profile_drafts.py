"""Drafts under other profiles: each open function's machine draft and saved C judged under the compiler profiles the
project's matched functions already use, by the background drafter, after the drafts are made.

An N64 game built with KMC GCC `-O2` by default had a second profile (SN GCC 2.8.1, `-mfp64`) that a few dozen of its
matched functions used, one by one. A one-off sweep that judged each of its 443 open functions' existing C under that
profile found 18 exact, and 17 of them were the untouched machine draft (the stored one or one made fresh), not the
saved best, which agents had tuned toward the default. The same profile with `-mfp32` matched 3 more float functions
whose targets use only even float registers. Nothing in the app tried that: the profile probe (profile_probe.py) runs
only inside the autopilot's saved-source pass and the Deep Permuter, for functions with saved C, and plans another
profile there only when five functions use it.

What runs, for each open function, smallest first:
- the texts: the stored machine draft, a draft made fresh by m2c with the header's declarations (match_brief.draft:
  m2c without its strict mode, what an agent's brief shows), and the saved best, each judged once (the same text is
  judged only once);
- the profiles (`plan`): the ones the project's matched functions use now (compiler_profile_usage: per-function
  profiles, ranges and the default), the most used first, never the function's own; then those of the matched
  functions beside it that the most used ones leave out (`neighbour_profiles`: a file or library built its own way
  is a few functions of a large project, so counting the whole project never reaches its profile); and for a MIPS GCC function whose
  target's float registers point to the other register file (fp_fingerprints.why), the other `-mfp` flag of its own
  profile and of each of those;
- each pair through deep_profiles.probe, the call the profile probe and the Deep Permuter's sweep use: a verdict is
  cached per (function, text, build inputs, profile) and never compiled twice, and only an EXACT pair is rechecked and
  applied, for that function only, through `suggest` and the project's own rule for automatic compiler changes (with
  them off, the exact pair waits on the project page). Never a project default or a compiler range, never a score.
- the closest pair that was not exact is kept as the function's closest attempt under another profile
  (profile_attempts.py), which briefs hand on. It changes no score and no setting.

A function's pass is recorded as finished (a `reuse_trials` marker over its target, its draft's inputs, its saved C
and the profiles) only when every pair was judged, so a Stop or an idle project leaves it for the next pass, and a new
draft, a new saved C or a profile's first match makes it worth one more pass (whose old pairs need no compile). A
function another worker holds is left for the next look. The drafter runs the pass while the project is being worked
on (project_activity), on its own low-priority worker group ("Drafts under other profiles" in Background work, whose
Stop ends the pass until the drafter starts again).

ROM mode only, like the profile probe: a project-mode build gives each source file its compiler and flags
(ROADMAP.md, "Drafts under other profiles in project mode, and on the PS3"). In a room the host's copy judges.
"""
from __future__ import annotations

import hashlib
import json
import logging

from . import deep_permuter, deep_profiles, fp_fingerprints, profile_attempts, profile_detection, project_activity
from .deep_profile_sweep import _ready

log = logging.getLogger("decompdecomp")

SIGNATURE = "profile-drafts-v1:"
MAX_USED = 4  # the project's most used profiles judged per function (their float-register variants come on top)
NEIGHBOURS = 2  # the matched functions on each side of a function whose profiles are judged too
HOLD_SECONDS = 600  # one function's hold: every pair below compiles in far less
SEARCH_MINUTES = 10
VERIFY = 3  # compiles an exact pair's recheck and apply take beyond the pairs themselves (deep_profiles.probe)
WAITING = "Exact match found"  # deep_profiles.probe's word for an exact pair waiting on the project page


def key(profile) -> tuple:
    flags = profile["flags"]
    return profile["toolchain"], tuple(flags.split() if isinstance(flags, str) else flags)


def supported(net, proj) -> bool:
    """A local ROM project: a project-mode build owns its files' compilers, a room its host's."""
    return profile_detection.supported(net, proj)


def used_profiles(net, proj) -> list[dict]:
    """The profiles the project's matched functions are built with now, the most used first: [{toolchain, flags,
    matched}]. A profile no matched function uses is a guess, and the profile probe has the evidence rules for those."""
    used = [p for p in net.compiler_profile_usage(proj) if p["matched"] > 0 and _ready(p["toolchain"])]
    return [{"toolchain": p["toolchain"], "flags": list(p["flags"]), "matched": p["matched"]}
            for p in sorted(used, key=lambda p: (-p["matched"], key(p)))]


def neighbour_profiles(net, proj, cfg, fn, used) -> list[dict]:
    """Those of `used` that the matched functions nearest to `fn` (NEIGHBOURS on each side) are built with, however
    few functions of the project use them. By place in the ROM, which overlays don't share, else by address."""
    from ..project import compiler_for
    row = dict(fn)
    column = "rom_offset" if row.get("rom_offset") is not None else "vram"
    if row.get(column) is None:
        return []
    near = set()
    for side, order in (("<", "DESC"), (">", "ASC")):
        for other in net.project_store(proj["id"]).all(
                "SELECT name,vram,rom_offset FROM functions WHERE project_id=? AND status='matched' "
                f"AND {column}{side}? ORDER BY {column} {order} LIMIT ?", proj["id"], row[column], NEIGHBOURS):
            theirs = compiler_for(cfg, other["vram"], other)
            if theirs.get("toolchain") and not theirs.get("cmd"):
                near.add(key(deep_profiles._profile(theirs)))
    return [p for p in used if key(p) in near]


def _fp_variant(arch, profile, code, big_endian) -> list[str] | None:
    """`profile`'s flags with the other `-mfp` flag, when the target's float registers say it was built so. MIPS GCC
    profiles that name the register file only (as profile_probe.mips_gcc): IDO has no such flag."""
    flags = list(profile["flags"])
    if (arch != "mips" or not code or profile.get("cmd") or str(profile["toolchain"]).startswith("ido-")
            or not any(f in fp_fingerprints.FP_FLAGS for f in flags)):
        return None
    return fp_fingerprints.flipped(flags) if fp_fingerprints.why(flags, code, (), big_endian) else None


def plan(arch, own, used, code, big_endian=True, near=()) -> list[dict]:
    """The profiles to judge one function's texts under, in order: the used ones (at most MAX_USED), those of its
    matched neighbours (`near`) not among them, then the other float-register file of its own profile and of each of
    those. Never its own profile, never one twice."""
    seen, out = {key(own)}, []

    def add(profile, why):
        if key(profile) not in seen:
            seen.add(key(profile))
            out.append({"toolchain": profile["toolchain"], "flags": list(profile["flags"]), "why": why})
    chosen = used[:MAX_USED]
    for profile in chosen:
        add(profile, f"{profile['matched']} matched functions of this project use it")
    for profile in near:
        add(profile, "a matched function beside it uses it")
    chosen = [*chosen, *(p for p in near if p not in chosen)]
    for base in [own, *chosen]:
        if (flags := _fp_variant(arch, base, code, big_endian)) is not None and _ready(base["toolchain"]):
            add({"toolchain": base["toolchain"], "flags": flags}, "the target's float registers point to the other register file")
    return out


def _digest(text) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def marker(fn, profiles) -> str:
    """What a finished pass over this function was made from: its target, its draft's inputs and text, its saved C,
    and the profiles."""
    row = dict(fn)
    inputs = (profile_attempts._target(fn), row.get("seed_key"), _digest(row.get("seed")), _digest(row.get("best_source")),
              sorted(key(p) for p in profiles))
    return SIGNATURE + hashlib.sha256(repr(inputs).encode()).hexdigest()


def finished(net, proj, fn, profiles) -> bool:
    return net.project_store(proj["id"]).one("SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?",
                                             fn["id"], marker(fn, profiles)) is not None


def _record(net, proj, fn, profiles, outcome, best) -> None:
    with net.project_store(proj["id"]).tx() as db:
        db.execute("INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)",
                   (fn["id"], marker(fn, profiles), outcome, "drafts under other profiles", json.dumps({"best": best})))


def texts(net, proj, fn) -> list[tuple[str, str]]:
    """[(which, C)]: the stored draft, a fresh draft, the saved best; each text once, in that order."""
    from . import match_brief
    from .function_names import canonical_source
    found, seen = [], set()

    def add(which, text):
        if text and text.strip() and text.strip() not in seen:
            seen.add(text.strip())
            found.append((which, canonical_source(fn, text)))
    add("stored draft", fn["seed"])
    try:
        add("fresh draft", match_brief.draft(net, proj, fn))
    except Exception:  # noqa: BLE001 - a draft that can't be made fresh leaves the stored one and the saved C
        log.info("drafts under other profiles: no fresh draft of %s", fn["name"], exc_info=True)
    add("saved C", fn["best_source"])
    return found


class _Log:
    """What deep_permuter.Search asks of its log: lines go to the app's log, and the pass's Stop is checked."""

    def __init__(self, name, cancelled):
        self.name, self.cancelled = name, cancelled

    def __call__(self, text):
        log.info("drafts under other profiles: %s: %s", self.name, text)

    def progress(self, value):
        pass

    def check_cancel(self):
        if self.cancelled():
            raise deep_permuter.SearchLimit("Stopped")


def run_one(net, agent, proj, fn, used, cancelled=lambda: False) -> str:
    """One open function's pass: 'matched' (an exact pair applied), 'waiting' (an exact pair waits on the project page),
    'tried' (nothing exact), 'done' (finished before), 'busy' (another worker holds it), 'stopped' or 'skip'."""
    from ..network import NetworkError
    from ..project import compiler_for
    from .progress import ProgressMoved
    if fn["status"] != "open" or not supported(net, proj):
        return "skip"
    cfg = net.cfg(proj)
    own = compiler_for(cfg, fn["vram"], fn)
    if own.get("cmd") or not own.get("toolchain"):
        return "skip"
    own = deep_profiles._profile(own)
    arch = proj["arch"] if "arch" in proj.keys() else "mips"
    big = bool(proj["big_endian"]) if "big_endian" in proj.keys() else True
    profiles = plan(arch, own, used, bytes(fn["target"] or b""), big, neighbour_profiles(net, proj, cfg, fn, used))
    if not profiles:
        return "skip"
    if finished(net, proj, fn, profiles):
        return "done"
    if not net.permuter._hold(agent, fn, count_turn=False, seconds=HOLD_SECONDS):
        return "busy"
    best = None
    try:
        candidates = texts(net, proj, fn)
        if not candidates:  # (a draft or saved C changes the marker, and then it is looked at again)
            _record(net, proj, fn, profiles, "nothing", None)
            return "skip"
        search = deep_permuter.Search(net, SEARCH_MINUTES, len(profiles) * len(candidates) + VERIFY,
                                      _Log(fn["name"], cancelled), fn)
        search.phase = "drafts under other profiles"
        for profile in profiles:
            for which, text in candidates:
                score = deep_profiles.probe(search, agent, proj, fn, text, profile, scored=True, review_variants=False)
                if score == 0:
                    _record(net, proj, fn, profiles, "matched", {**profile, "score": 0, "text": which})
                    return "matched"
                if score is not None and (best is None or score < best["score"]):
                    best = {**profile, "score": score, "text": which, "source": text}
    except deep_permuter.SearchLimit as error:
        if str(error).startswith(WAITING):  # (the exact pair is on the project page: judging on would only repeat it)
            _record(net, proj, fn, profiles, "waiting", None)
            return "waiting"
        if cancelled():
            return "stopped"
        log.info("drafts under other profiles: %s: %s", fn["name"], error)
        return "skip"  # (the time ran out: not finished, a later pass judges the rest from the cache)
    except (NetworkError, ProgressMoved) as error:  # it changed meanwhile: its next pass sees the new one
        log.info("drafts under other profiles: %s: %s", fn["name"], error)
        return "skip"
    finally:
        net.permuter._let_go(agent, fn)
    if best is not None:
        profile_attempts.keep(net, proj, fn, best["toolchain"], best["flags"], best["score"], best["source"])
    _record(net, proj, fn, profiles, "tried", best and {k: best[k] for k in ("toolchain", "flags", "score", "text")})
    return "tried"


def look(net, proj) -> tuple:
    """What a pass over the whole project depends on beyond each function's own inputs: the profiles in use."""
    return tuple(key(p) for p in used_profiles(net, proj))


def open_functions(net, proj) -> list[int]:
    """The open functions a pass looks at, smallest first; those under a boundary review are left alone."""
    from .boundary_repairs import blocked
    held = blocked(net, proj)
    rows = net.project_store(proj["id"]).all("SELECT id FROM functions WHERE project_id=? AND status='open' "
                                             "ORDER BY n_insns, vram", proj["id"])
    return [r["id"] for r in rows if r["id"] not in held]


def active(net, slug) -> bool:
    return project_activity.active(net, slug)
