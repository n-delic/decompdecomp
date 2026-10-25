"""Which compiler built each source file of a project whose files each have their own (Console.file_compiler: a PC
game's Visual C++ versions, a DS game's CodeWarrior's): the compiler map, the judge's survey and range checks, the
project-mode side of compilermap.py and app/rangecheck.py.

A project's source file is a run of neighbouring functions (PC: about 4 KiB) that its build gives one compiler, so
the file is the unit of evidence here, as the function is in a ROM project:
- what the program says: a PC program's Rich header counts the files each Visual C++ build compiled
  (Console.program_compilers); those are the compilers the survey tries;
- the judge's survey: each file with C in it built with each of those compilers in turn (ForkEngine.file_scores,
  the file keeping its own): a function that matches with another compiler and not with the file's own confirms
  that compiler for the file;
- the map: the files in address order, a file with matches known to be its own compiler's and a confirmed one the
  confirmed compiler's, spread over their neighbours as compilermap.build spreads functions' (an object file is one
  compiler, and the linker lays object files out one after another);
- range checks: a compiler suggested for a stretch of files (an agent's suggestion with address ranges, or a run of
  the map) applies once a second file confirms it, walking out from the first (rangecheck.evaluate), never past a
  file whose matches need its own compiler; files refuting it on both sides make it a quirk. One function proves
  one file: try_compiler switches the file it's in at once, as before.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import threading
import time

from .. import compilermap, consoles
from ..network import NetworkError
from .engine.common import parse_ranges
from .rangecheck import MAX_TASKS, REACH, Step, evaluate

log = logging.getLogger("decompdecomp")
SURVEY_FILES = 60  # files a survey run builds (each once per compiler it tries)
STRETCHES_SHOWN = 30
OPEN = ("checking", "waiting")


class FileCompilers:
    """Per project: the survey's state, and the range checks, kept in its settings (cfg["file_survey"],
    cfg["file_checks"])."""

    background = True  # surveys and checks build: in a thread of their own (tests run them in place)

    def __init__(self, net):
        self.net = net
        self.surveys: dict[str, dict] = {}  # slug -> the running or last survey
        self._lock = threading.Lock()  # one check run at a time

    # ------------------------------------------------------------------ the project's files

    def applies(self, slug: str | None) -> bool:
        """Whether the project is one whose source files each have a compiler."""
        if not (slug and self.net.fork_slug(slug)):
            return False
        _, fp, cfg = self.net.forks.get(slug)
        return consoles.get(cfg.get("platform")).file_compiler(fp, "") is not None

    def _files(self, slug: str) -> list[tuple[int, int, str, str]]:
        """(start, end, source, its compiler) of every source file with code, in address order."""
        _, fp, cfg = self.net.forks.get(slug)
        console = consoles.get(cfg.get("platform"))
        out = [(spans[0][0], spans[-1][1], source, console.file_compiler(fp, source))
               for source, spans in console.file_ranges(fp).items() if spans]
        return sorted(out)

    def _rows_by_file(self, proj) -> dict[str, list]:
        """The project's functions by their source file."""
        out: dict[str, list] = {}
        for r in self.net.store.all("SELECT id, name, status, meta, best_source FROM functions WHERE project_id=?",
                                    proj["id"]):
            if (source := self.net.forks._meta(r).get("source")):
                out.setdefault(source, []).append(r)
        return out

    def candidates(self, slug: str) -> list[str]:
        """The compilers the survey tries: what the program says built it (installed), and the files' own."""
        _, fp, cfg = self.net.forks.get(slug)
        said = [c for c, _, _, installed in consoles.get(cfg.get("platform")).program_compilers(fp) if installed]
        return list(dict.fromkeys([*said, *(own for *_, own in self._files(slug))]))

    # ------------------------------------------------------------------ the map

    def build(self, slug: str) -> compilermap.CompilerMap:
        """The map over the project's files (compilermap.build, a file for a function: no code habits, only the
        judge's evidence and the files' compilers)."""
        proj = self.net.project_row(slug)
        rows = self._rows_by_file(proj)
        confirmed = (self.net.cfg(proj).get("file_survey") or {}).get("confirmed", {})
        by_file = {}
        for source, fns in rows.items():
            said = [confirmed[f["name"]] for f in fns if f["name"] in confirmed]
            by_file[source] = (any(f["status"] == "matched" for f in fns), said[0] if said else None)
        items = []
        for start, end, source, own in self._files(slug):
            matched, said = by_file.get(source, (False, None))
            items.append((start, end - start, b"", (own, ()), matched, (said, ()) if said else None))
        return compilermap.build(items, big_endian=False)

    def _file_of(self, slug: str, addr: int) -> str | None:
        return next((source for start, end, source, _ in self._files(slug) if start <= addr < end), None)

    def text(self, slug: str, survey: bool = False, check_ranges: bool = False) -> str:
        """compiler_map for agents: what the program says built it, the files built with another compiler, the
        evidence, where the map says files look built by another, the survey and the range checks."""
        proj, fp, cfg = self.net.forks.get(slug)
        console = consoles.get(cfg.get("platform"))
        started = ""
        if survey:
            started = self.start_survey(slug)["state"] + ". "
        if check_ranges:
            started += f"Weighing {self.recheck(slug)} open range check(s) again (in the background). "
        files = self._files(slug)
        own = console.file_compiler(fp, "")
        lines = [f"{started}The project builds with {consoles.compiler_name(own)}; {len(files)} source files, "
                 f"{sum(1 for *_, c in files if c != own)} of them with another compiler."]
        said = console.program_compilers(fp)
        if said:
            lines.append("What the program says built it (its files, by compiler): " + "; ".join(
                f"{text}: {n} files" + ("" if installed else f" (built here with {consoles.compiler_name(c)}, "
                                        "not installed)") for c, n, text, installed in said))
        if len(self.candidates(slug)) < 2:
            lines.append(f"Every compiler it names is built here with {consoles.compiler_name(own)}: the survey has no "
                         "other compiler to try.")
        cmap = self.build(slug)
        matched: dict[str, int] = {}
        for source, fns in self._rows_by_file(proj).items():
            mine = console.file_compiler(fp, source)
            matched[mine] = matched.get(mine, 0) + sum(f["status"] == "matched" for f in fns)
        lines.append("Matched functions, by the compiler of their file: "
                     + (", ".join(f"{consoles.compiler_name(c)}: {n}" for c, n in matched.items() if n) or "none yet"))
        differ = [r for r in cmap.runs if r.differs()]
        if differ:
            lines.append("Where files look built by another compiler than today's (from the judge's confirmations):")
            for r in differ[:STRETCHES_SHOWN]:
                lines.append(f"- 0x{r.start:08X}-0x{r.end:08X}: {consoles.compiler_name(r.label[0])}, {r.functions} "
                             f"files ({r.confirmed} confirmed by the judge); today "
                             f"{consoles.compiler_name(r.assigned.most_common(1)[0][0][0])}")
        else:
            lines.append("The judge confirms no other compiler than each file's own so far.")
        state = self.surveys.get(slug) or {}
        last = cfg.get("file_survey") or {}
        lines.append("The judge's survey (each file's C built with each compiler the program names): "
                     + (f"running, {state.get('tried', 0)} files tried, {state.get('confirmed', 0)} confirmed"
                        if state.get("state") == "running" else
                        f"{len(last.get('confirmed', {}))} functions confirmed in {len(last.get('tried', {}))} files"
                        if last else "not run yet")
                     + ". compiler_map(survey=true) runs it; a file confirmed becomes a suggestion, checked on its "
                       "neighbouring files before it applies.")
        checks = cfg.get("file_checks") or []
        if checks:
            lines.append("Range checks (a compiler suggested for a stretch of files is tested on its neighbours first):")
            lines += [f"- #{c['id']} {', '.join(f'0x{a:X}-0x{b:X}' for a, b in c['spans'])} "
                      f"{consoles.compiler_name(c['toolchain'])}: {c.get('said') or c['status']}" for c in checks[-40:]]
        return "\n".join(lines)

    def brief(self, slug: str, fn, source: str) -> list[str]:
        """get_function's line where the map says the function's file looks built by another compiler."""
        try:
            _, fp, cfg = self.net.forks.get(slug)
            own = consoles.get(cfg.get("platform")).file_compiler(fp, source)
            span = next(((start, end) for start, end, s, _ in self._files(slug) if s == source), None)
            looks = self.build(slug).of.get(span[0]) if span else None
        except Exception:  # never stands between an agent and the function
            log.exception("the compiler map failed")
            return []
        if not looks or looks[0] == own or fn["status"] == "matched":
            return []
        return [f"The compiler map says {source} looks built by {consoles.compiler_name(looks[0])}: if it won't "
                f"match, try_compiler(\"{fn['name']}\", \"{looks[0]}\")."]

    # ------------------------------------------------------------------ the survey

    def start_survey(self, slug: str, limit: int = SURVEY_FILES) -> dict:
        """The survey in the background (one per project)."""
        state = self.surveys.get(slug)
        if state and state["state"] == "running":
            return state
        state = self.surveys[slug] = {"state": "running", "tried": 0, "confirmed": 0, "suggested": [], "error": None,
                                      "t": time.time()}

        def run():
            try:
                state.update(self.survey(slug, limit=limit, state=state))
                state["state"] = "done"
            except Exception as e:  # noqa: BLE001  a background job says why it stopped
                log.exception("compiler survey failed")
                state.update(state="error", error=str(e))
        if self.background:
            threading.Thread(target=run, daemon=True, name=f"file-survey-{slug}").start()
        else:
            run()
        return state

    def survey(self, slug: str, limit: int = SURVEY_FILES, state: dict | None = None, log=lambda line: None) -> dict:
        """The judge's part of the map: each file with open functions and C for them, built with each candidate
        compiler but its own (a file's C as it is: the same C with the same compilers is skipped next time). A
        function that matches with one and not with the file's own confirms it. Then the map's runs with
        confirmations become suggestions, checked on their neighbours."""
        proj, fp, cfg = self.net.forks.get(slug)
        wanted = self.candidates(slug)
        old = cfg.get("file_survey") or {}
        tried, confirmed = dict(old.get("tried", {})), dict(old.get("confirmed", {}))
        rows = self._rows_by_file(proj)
        state = state if state is not None else {}
        n = found = 0
        for _, _, source, own in self._files(slug):
            if n >= limit:
                break
            if not any(f["status"] == "open" and f["best_source"] for f in rows.get(source, ())):
                continue  # (no C for its open functions yet: nothing to confirm)
            try:
                text = (fp.root / source).read_text(errors="replace", encoding="utf-8")
            except OSError:
                continue
            digest = hashlib.sha1((text + repr(wanted)).encode()).hexdigest()[:16]
            if tried.get(source) == digest:
                continue
            n += 1
            for compiler in (c for c in wanted if c != own):
                try:
                    mine, theirs = self.net.forks.file_scores(slug, source, compiler)
                except NetworkError:
                    break
                for name, pct in theirs.items():
                    if (pct or 0) >= 100 and (mine.get(name) or 0) < 100 and name not in confirmed:
                        confirmed[name] = compiler
                        found += 1
                        log(f"{name}: its C matches the game with {compiler}, not with {own}")
            tried[source] = digest
            state.update(tried=n, confirmed=found)
        self.net._update_cfg(slug, lambda c: c.__setitem__("file_survey", {"tried": tried, "confirmed": confirmed,
                                                                           "t": time.time()}))
        suggested = self._suggest_from_map(slug) if found else []
        if found:
            with self.net.store.tx() as db:
                self.net._event(db, proj["id"], None, "compiler",
                                f"the compiler survey: {found} function(s) match with another compiler than their file's")
        return {"tried": n, "confirmed": found, "suggested": suggested}

    def _suggest_from_map(self, slug: str) -> list[str]:
        """Each run of the map the judge says another compiler built, as the autopilot's suggestion."""
        agent, _ = self.net.local_agent("autopilot")
        out = []
        for run in self.build(slug).runs:
            if run.differs() and run.confirmed:
                compiler = run.label[0]
                text = (f"The compiler map: {run.functions} source files at 0x{run.start:X}-0x{run.end:X} look built by "
                        f"{consoles.compiler_name(compiler)}; {run.confirmed} of them have a function that matches "
                        "with it and not with its file's compiler.")
                out.append(self.suggest(agent, slug, text, compiler, None, f"0x{run.start:X}-0x{run.end:X}"))
        return out

    # ------------------------------------------------------------------ suggestions and range checks

    def suggest(self, agent, slug: str, text: str, compiler: str, function: str | None, ranges: str) -> str:
        """An agent's (or the map's) suggestion of a compiler for a stretch of files: kept for the user, and with
        address ranges, a range check on the files around it. Without ranges, the whole project's compiler: the
        user decides (Settings), as the files each keep theirs where their matches need it."""
        proj, fp, cfg = self.net.forks.get(slug)
        console = consoles.get(cfg.get("platform"))
        tc = next((c.id for c in console.compilers if compiler.strip().lower() in (c.id.lower(), c.name.lower())), None)
        if tc is None:
            raise NetworkError(f"no compiler {compiler!r} for this game; its platform's: "
                               f"{', '.join(c.id for c in console.compilers)}")
        spans = parse_ranges(ranges)
        if not spans and function:  # the file the function is in
            fn = self.net.forks._fn(slug, function)
            source = self.net.forks._meta(fn).get("source")
            spans = [[s, e] for s, e, src, _ in self._files(slug) if src == source]
        if not spans and tc == console.file_compiler(fp, ""):
            raise NetworkError("that's already the project's compiler")
        found: list[dict] = []

        def change(cfg):
            items = cfg.setdefault("suggestions", [])
            same = next((s for s in items if s["status"] == "open" and s["toolchain"] == tc
                         and s.get("ranges", []) == spans), None)
            if same is None:
                same = {"id": max((s["id"] for s in items), default=0) + 1, "text": text, "toolchain": tc, "flags": [],
                        "ranges": spans, "agents": [], "check": None, "status": "open", "t": time.time()}
                items.append(same)
            same["agents"] = sorted({*same["agents"], agent.name})
            found.append(same)
        self.net._update_cfg(slug, change)
        s = found[0]
        if not spans:
            return "Suggested: the user sees it on the project page and can apply it."
        origin = self._file_of(slug, spans[0][0])
        if function:
            origin = self.net.forks._meta(self.net.forks._fn(slug, function)).get("source") or origin
        return "Suggested. " + self.propose(slug, s, tc, spans, origin)

    def propose(self, slug: str, suggestion: dict, compiler: str, spans: list[list[int]], origin: str | None) -> str:
        """Test a suggested compiler for files on their neighbours before it applies."""
        check = {"id": 0, "suggestion": suggestion["id"], "toolchain": compiler, "origin": origin, "spans": spans,
                 "status": "checking", "evidence": {}, "tasks": [], "range": None, "t": time.time()}
        found: list[dict] = []

        def add(cfg):
            items = cfg.setdefault("file_checks", [])
            same = next((c for c in items if c["status"] in OPEN and c["toolchain"] == compiler
                         and c["origin"] == origin and c["spans"] == spans), None)
            if same is None:
                check["id"] = max((c["id"] for c in items), default=0) + 1
                items.append(check)
            found.append(same or check)
        self.net._update_cfg(slug, add)
        c = found[0]
        self.recheck(slug, c["id"])
        return (f"Range check #{c['id']} started: decompdecomp builds the files next to it with "
                f"{consoles.compiler_name(compiler)} before it applies (one file proves little). compiler_map() shows "
                "how it goes; a neighbouring file's function may come to an agent as a task.")

    def verdict(self, slug: str, source: str, compiler: str) -> str:
        """One file's verdict on "built by `compiler`": confirmed (a function of it matches with it and not with the
        other compiler: the file's own, or the project's where the file has `compiler` already), refuted (a function
        matched with the other doesn't with it, or the survey confirmed a third for it), neutral (what matches,
        matches with both), unknown (nothing of it matches with either)."""
        proj, fp, cfg = self.net.forks.get(slug)
        console = consoles.get(cfg.get("platform"))
        own = console.file_compiler(fp, source)
        other = own if own != compiler else console.file_compiler(fp, "")
        if other == compiler:
            return "neutral"
        confirmed = (cfg.get("file_survey") or {}).get("confirmed", {})
        said = {confirmed[f["name"]] for f in self._rows_by_file(proj).get(source, ()) if f["name"] in confirmed}
        if compiler in said:
            return "confirmed"
        if said - {own}:
            return "refuted"
        if own != compiler:
            with_other, with_it = self.net.forks.file_scores(slug, source, compiler)
        else:  # (the file has it already: what matches now, against the project's compiler)
            with_it, with_other = self.net.forks.file_scores(slug, source, other)
        if any((pct or 0) >= 100 and (with_it.get(n) or 0) < 100 for n, pct in with_other.items()):
            return "refuted"
        if any((pct or 0) >= 100 and (with_other.get(n) or 0) < 100 for n, pct in with_it.items()):
            return "confirmed"
        return "neutral" if any((pct or 0) >= 100 for pct in with_other.values()) else "unknown"

    def recheck(self, slug: str, cid: int | None = None) -> int:
        """Weigh a check (or every open one) again, in the background. Returns how many."""
        checks = [c for c in (self.net.cfg(self.net.project_row(slug)).get("file_checks") or [])
                  if c["status"] in OPEN and (cid is None or c["id"] == cid)]

        def run():
            with self._lock:
                for c in checks:
                    try:
                        self._weigh(slug, copy.deepcopy(c))
                    except Exception:  # noqa: BLE001  a check never stands between an agent and its work: it waits
                        log.exception("file range check failed")
        if checks:
            if self.background:
                threading.Thread(target=run, daemon=True, name=f"file-check-{slug}").start()
            else:
                run()
        return len(checks)

    def _weigh(self, slug: str, c: dict) -> None:
        files = self._files(slug)
        if not files:
            return
        at = {source: k for k, (_, _, source, _) in enumerate(files)}
        inside = lambda k: any(a < files[k][1] and files[k][0] < b for a, b in c["spans"])  # noqa: E731
        o = at.get(c["origin"], next((k for k in range(len(files)) if inside(k)), None))
        if o is None:
            return
        evidence = dict(c["evidence"])

        def verdict(k: int) -> str:
            source = files[k][2]
            if evidence.get(source) not in ("confirmed", "refuted"):
                evidence[source] = self.verdict(slug, source, c["toolchain"])
            return evidence[source]

        def walk(rng) -> list[Step]:
            out, outside = [], 0
            for k in rng:
                if not inside(k):
                    outside += 1
                    if outside > REACH:
                        break
                out.append(Step(files[k][0], files[k][1], verdict(k)))
                if out[-1].verdict in ("refuted", "unknown"):
                    break
            return out
        # the first file counts as one confirmation only when the judge says so (an agent's word says nothing)
        mine = verdict(o)
        first = Step(files[o][0], files[o][1], {"confirmed": "origin", "unknown": "neutral"}.get(mine, mine))
        left, right = walk(range(o - 1, -1, -1)), walk(range(o + 1, len(files)))
        status, start, end, nxt = evaluate(left, first, right)
        c["evidence"] = evidence
        if status == "confirmed":  # the suggested files stand, cut back only where a neighbour refutes them
            start, end = min([start] + [a for a, _ in c["spans"]]), max([end] + [b for _, b in c["spans"]])
            start = max([start] + [s.end for s in left if s.verdict == "refuted"])
            end = min([end] + [s.vram for s in right if s.verdict == "refuted"])
            return self._finish(slug, c, "confirmed", start, end)
        if status == "quirk":
            return self._finish(slug, c, "quirk", start, end)
        if nxt is not None and nxt.vram in c["tasks"]:
            return self._save(slug, c, "checking: an agent has the next file's task")  # (its answer moves it on)
        if nxt is not None and len(c["tasks"]) < MAX_TASKS:
            source = next(s for s0, _, s, _ in files if s0 == nxt.vram)
            fn = next((f for f in self._rows_by_file(self.net.project_row(slug)).get(source, ())
                       if f["status"] == "open"), None)
            if fn is not None:
                c["tasks"] = c["tasks"] + [nxt.vram]
                self.net.set_priority(slug, fn["name"], True)
                self.net.add_note(self.net.local_agent("autopilot")[0], slug, self.task_text(c, fn["name"], source),
                                  fn["name"])
                return self._save(slug, c, f"checking: {fn['name']} in {source} is next (first in line for an agent)")
        c["status"] = "waiting"
        self._save(slug, c, "waiting: no neighbouring file settled it yet; the user decides, or agents' next "
                            "matches do")

    def task_text(self, c: dict, name: str, source: str) -> str:
        compiler = consoles.compiler_name(c["toolchain"])
        return (f"Range check #{c['id']}: is {source} built by {compiler}? A file next to it looks so. Match {name} "
                f"as usual; then try_compiler(\"{name}\", \"{c['toolchain']}\"). Matching with {compiler} and not with "
                "the file's compiler confirms it (the file switches); matching only with the file's shows it isn't. "
                "Either answer helps.")

    def _finish(self, slug: str, c: dict, status: str, start: int, end: int) -> None:
        c["status"], c["range"] = status, [start, end]
        compiler = consoles.compiler_name(c["toolchain"])
        proj = self.net.project_row(slug)
        confirmed = sum(v == "confirmed" for v in c["evidence"].values())
        if status == "confirmed":
            text = f"confirmed by {confirmed} files: 0x{start:X}-0x{end:X} is built by {compiler}"
            resolve = None
            if self.net.auto_adjusts(proj):
                try:
                    text += f" ({self.net.forks.set_compiler_ranges(slug, [[start, end]], c['toolchain'])})"
                    resolve = "applied"
                except NetworkError as e:
                    text += f" (not applied: {e})"
            self._save(slug, c, text, resolve)
        else:
            text = "a quirk: the files on both sides are built by their own compilers, as they are"
            if c["origin"]:
                fn = next((f for f in self._rows_by_file(proj).get(c["origin"], ()) if f["status"] == "open"), None)
                if fn is not None:
                    self.net.add_note(self.net.local_agent("autopilot")[0], slug,
                                      f"Range check #{c['id']}: C in {c['origin']} matches with {compiler}, but the "
                                      "files around it need their own compilers. Look for C that matches with this "
                                      "file's compiler.", fn["name"])
            self._save(slug, c, text, "dismissed")
        for vram in c["tasks"]:
            source = next((s for s0, _, s, _ in self._files(slug) if s0 == vram), None)
            for f in self._rows_by_file(proj).get(source, ()):
                self.net.set_priority(slug, f["name"], False)
        with self.net.store.tx() as db:
            self.net._event(db, proj["id"], None, "compiler", f"range check #{c['id']} ({compiler}): {text}")

    def _save(self, slug: str, c: dict, said: str, resolve: str | None = None) -> None:
        c["said"] = said

        def change(cfg):
            for i, old in enumerate(cfg.get("file_checks", [])):
                if old["id"] == c["id"]:
                    cfg["file_checks"][i] = c
            for s in cfg.get("suggestions", []):
                if s["id"] == c["suggestion"]:
                    s["check"] = f"range check #{c['id']}: {said}"
                    if c["status"] == "confirmed":
                        s["ranges"] = [c["range"]]
                    if resolve and s["status"] == "open":
                        s["status"] = resolve
        self.net._update_cfg(slug, change)

    def tried(self, slug: str, source: str, compiler: str) -> None:
        """try_compiler switched a file (a function of it matches only with `compiler`): evidence for the checks
        around it."""
        cfg = self.net.cfg(self.net.project_row(slug))
        for c in cfg.get("file_checks") or []:
            if c["status"] in OPEN and c["toolchain"] == compiler:
                self.net._update_cfg(slug, lambda cfg, cid=c["id"]: next(
                    x for x in cfg["file_checks"] if x["id"] == cid)["evidence"].__setitem__(source, "confirmed"))
                self.recheck(slug, c["id"])
