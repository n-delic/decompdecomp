"""Which compiler builds which part of a game: the project's compiler and its address ranges, agents'
suggestions and try_compiler, and the compiler map with the judge's survey (compilermap.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time

from ... import arch, compilermap, consoles, toolchains
from ...network import Agent, NetworkError
from ...project import compiler_for
from .common import parse_ranges

# Flag search (search_flags): the flags of each compiler family worth trying, at most MAX_FLAG_VARIANTS compiles
FLAG_FAMILIES = (("ido", "ido"), ("mwcps2", "mwcps2"), ("ee-gcc", "ee"), ("gcc", "gcc"))
FLAG_LEVELS = {"gcc": ("-O1", "-O2", "-O3", "-Os"), "ee": ("-O1", "-O2", "-O3", "-Os"),
               "ido": ("-O1", "-O2", "-O3", "-g"), "mwcps2": ("-O1", "-O2", "-O3", "-O4")}
FLAG_EXTRAS = {"gcc": ("-fno-strength-reduce", "-fno-schedule-insns"),
               "ee": ("-fno-strength-reduce", "-fno-schedule-insns", "-fno-common"),
               "ido": (), "mwcps2": ()}
SMALL_DATA_SIZES = (0, 8)
MAX_FLAG_VARIANTS = 12
FLAGS_SHOWN = 6


class CompilerMixin:
    # Settings: auto-adjust compiler. A suggested compiler is applied without asking once it's proven (a function's
    # C matches with it) or three agents agree on it (see suggest)
    auto_compiler = False

    AGREE = 3

    SURVEY_MIN_INSNS = 12  # a shorter function comes out the same from several compilers: it proves nothing
    # The compiler answer's own checking: the survey runs again by itself at most this often (it skips C it tried, so
    # a run where little changed is quick)
    SURVEY_AGAIN_SECONDS = 3600
    ANSWER_STRETCHES = 12  # stretches that look built by another compiler, listed in the answer

    def compiler_map(self, proj, fresh: bool = False) -> compilermap.CompilerMap | None:
        """Which compiler built each part of a MIPS game: matches, the judge's confirmations and the habits that rule
        compilers out. Rebuilt when a match, a range or a confirmation changes it, one build at a time per project;
        meanwhile the last map answers, unless `fresh` (wait for the one that's current). GTA San Andreas: each match
        made the map stale, and every waiting brief and map request rebuilt it at once, 600-870 ms each.
        None: not MIPS code."""
        if not self.is_rom(proj) or arch.COMPILER_MAP not in self.console(proj).features:
            return None
        cached = self._cmaps.get(proj["id"])
        if cached and cached[0] == self._cmap_key(proj):
            return cached[1]
        building = self._cmap_builds.setdefault(proj["id"], threading.Lock())
        if cached and not fresh:
            if building.acquire(blocking=False):  # nobody builds it yet: in the background
                threading.Thread(target=self._rebuild_cmap, args=(proj["slug"], building), daemon=True,
                                 name=f"compiler-map-{proj['slug']}").start()
            return cached[1]
        with building:  # the first map, or a fresh one: build it, or wait for the build under way
            return self._build_cmap(proj)

    def _cmap_key(self, proj) -> tuple:
        """What the compiler map is built from, summed up: matches, the compilers and ranges, confirmations."""
        cfg = self.cfg(proj)
        done = self.store.one("SELECT COUNT(*) AS n, COALESCE(SUM(status='matched'), 0) AS m, COALESCE(MAX(matched_at), 0) "
                              "AS t FROM functions WHERE project_id=?", proj["id"])
        confirmed = (cfg.get("compiler_survey") or {}).get("confirmed", {})
        return tuple(done), json.dumps([cfg.get("compiler"), cfg.get("compiler_ranges")], sort_keys=True), len(confirmed)

    def _rebuild_cmap(self, slug: str, building: threading.Lock) -> None:
        try:
            self._build_cmap(self._project(slug))  # its settings now, not those of the request that asked
        except Exception:  # noqa: BLE001  the last map keeps answering
            logging.getLogger("decompdecomp").exception("the compiler map failed")
        finally:
            building.release()

    def _build_cmap(self, proj) -> compilermap.CompilerMap:
        key = self._cmap_key(proj)
        cached = self._cmaps.get(proj["id"])
        if cached and cached[0] == key:  # built while this one waited
            return cached[1]
        cfg = self.cfg(proj)
        confirmed = (cfg.get("compiler_survey") or {}).get("confirmed", {})

        def label(c: dict) -> compilermap.Label:
            return c["toolchain"], tuple(c["flags"])
        rows = self.store.all("SELECT vram, size, target, status FROM functions WHERE project_id=? AND target IS NOT NULL",
                              proj["id"])
        built = compilermap.build(
            [(r["vram"], r["size"], r["target"], label(compiler_for(cfg, r["vram"])), r["status"] == "matched",
              (lambda c: (c[0], tuple(c[1])) if c else None)(confirmed.get(str(r["vram"])))) for r in rows],
            bool(cfg.get("big_endian", True)))
        self._cmaps[proj["id"]] = (key, built)
        return built

    def _short(self, lab: compilermap.Label) -> str:
        return f"{consoles.compiler_name(lab[0])} {' '.join(lab[1])}".strip()

    def compiler_map_text(self, slug: str, survey: bool = False, check_ranges: bool = False) -> str:
        """The map for agents: where it differs from the compiler the game builds with today, and why."""
        self._rom_only(slug, "compiler_map")
        proj = self._project(slug)
        cmap = self.compiler_map(proj, fresh=True)  # an agent asked: what the evidence says now
        if cmap is None:
            raise NetworkError("the compiler map reads MIPS code (PlayStation 2, PlayStation, N64) for now")
        started = ""
        if survey:
            started = self.start_survey(slug)["state"] + ". "
        if check_ranges:
            started += (f"Checking {self.rangechecks.audit(proj)} applied range(s) again, with the evidence their "
                        f"neighbours already give (in the background). ")
        cfg = self.cfg(proj)
        syms = self.symbols(proj["id"])
        lines = [f"{started}The game builds with {self._short((cfg['compiler']['toolchain'], tuple(cfg['compiler']['flags'])))}"
                 f" and {len(cfg.get('compiler_ranges', []))} address ranges with others.",
                 "Evidence, matched or confirmed by the judge, per compiler: "
                 + ", ".join(f"{t} {s}".strip() + f": {n}" for (t, s), n in cmap.examples.most_common())]
        if cmap.never:
            lines.append("Habits that rule a compiler out (its matched code never shows them): "
                         + "; ".join(f"{t} {s}".strip() + ": " + ", ".join(h) for (t, s), h in cmap.never.items()))
        differ = [r for r in cmap.runs if r.differs()]
        if differ:
            lines.append("Where the code looks built by another compiler than today's (runs of neighbouring functions):")
            for r in differ[:30]:
                today = self._short(r.assigned.most_common(1)[0][0])
                lines.append(f"- 0x{r.start:08X}-0x{r.end:08X}: {self._short(r.label)}, {r.functions} functions "
                             f"({r.confirmed} confirmed by the judge, {r.matched} matched); today {today}")
        else:
            lines.append("The code agrees with the compilers the game builds it with today.")
        if cmap.contradicted:
            names = ", ".join(syms.label(a) for a in sorted(cmap.contradicted)[:12])
            lines.append(f"{len(cmap.contradicted)} functions show a habit their compiler never does: {names}"
                         + (", ..." if len(cmap.contradicted) > 12 else "") + ". try_compiler them with another one.")
        lines += self.rangechecks.text(proj)
        state = self.surveys.get(slug) or {}
        last = (cfg.get("compiler_survey") or {})
        lines.append("The judge's survey (agents' C built with each compiler the project uses): "
                     + (f"running, {state.get('tried', 0)} tried, {state.get('confirmed', 0)} confirmed" if state.get("state") == "running"
                        else f"{len(last.get('confirmed', {}))} confirmed in {len(last.get('tried', {}))} functions tried"
                        if last else "not run yet")
                     + ". compiler_map(survey=true) runs it in the background; runs with confirmations become suggestions, "
                       "checked against every matched function before they apply.")
        return "\n".join(lines)

    def start_survey(self, slug: str, limit: int = 400) -> dict:
        """The survey in the background (one per project)."""
        state = self.surveys.get(slug)
        if state and state["state"] == "running":
            return state
        state = self.surveys[slug] = {"state": "running", "tried": 0, "confirmed": 0, "suggested": [], "error": None,
                                      "t": time.time()}

        def run():
            try:
                state.update(self.survey_compilers(slug, limit=limit, state=state))
                state["state"] = "done"
            except Exception as e:  # noqa: BLE001  a background job says why it stopped
                logging.getLogger("decompdecomp").exception("compiler survey failed")
                state.update(state="error", error=str(e))
        threading.Thread(target=run, daemon=True, name=f"survey-{slug}").start()
        return state

    def survey_compilers(self, slug: str, log=lambda line: None, limit: int = 400, state: dict | None = None) -> dict:
        """The judge's part of the compiler map. Agents' C for functions that don't match yet, built with each other
        compiler the project uses: C that matches the game with another compiler (and not with today's) confirms that
        compiler for its function. Contradicted functions first, then the closest attempts; C already tried is
        skipped. Then each run of the map with confirmations becomes a suggestion, checked like an agent's."""
        self._rom_only(slug, "the compiler survey")
        proj = self._project(slug)
        if self.compiler_map(proj) is None:
            raise NetworkError("the compiler map reads MIPS code (PlayStation 2, PlayStation, N64) for now")
        cmap = self.compiler_map(proj)
        cfg = self.cfg(proj)
        candidates = sorted({(cfg["compiler"]["toolchain"], tuple(cfg["compiler"]["flags"]))}
                            | {(r["toolchain"], tuple(r["flags"])) for r in cfg.get("compiler_ranges", [])})
        old = cfg.get("compiler_survey") or {}
        tried, confirmed = dict(old.get("tried", {})), dict(old.get("confirmed", {}))
        rows = self.store.all("SELECT * FROM functions WHERE project_id=? AND status NOT IN ('matched', 'asm') AND n_insns>=?",
                              proj["id"], self.SURVEY_MIN_INSNS)
        rows.sort(key=lambda f: (f["vram"] not in cmap.contradicted, f["best_score"] if f["best_score"] is not None else 1 << 30))
        state = state if state is not None else {}
        n = found = 0
        for fn in rows:
            if n >= limit:
                break
            source = self._source_of(fn)
            if not source:
                continue
            digest = hashlib.sha1(source.encode()).hexdigest()[:16]
            if tried.get(str(fn["vram"])) == digest:
                continue
            n += 1
            own = compiler_for(cfg, fn["vram"])
            today = (own["toolchain"], tuple(own["flags"]))
            for tc, flags in candidates:
                if (tc, flags) == today or not self._judge_with(proj, fn, tc, list(flags), source)[0]:
                    continue
                if self._judge_with(proj, fn, today[0], list(today[1]), source)[0]:
                    break  # it matches with today's compiler too: no evidence (and an agent's next compile matches it)
                confirmed[str(fn["vram"])] = [tc, list(flags)]
                found += 1
                log(f"{fn['name']}: its C matches the game with {self._short((tc, flags))}, not with {self._short(today)}")
                break
            tried[str(fn["vram"])] = digest
            state.update(tried=n, confirmed=found)
            if n % 25 == 0:
                log(f"{n} functions tried, {found} confirmed")
        self._update_cfg(slug, lambda c: c.__setitem__("compiler_survey", {"tried": tried, "confirmed": confirmed,
                                                                           "t": time.time()}))
        suggested = self._suggest_from_map(proj, confirmed)
        for line in suggested:
            log(line)
        if found:
            with self.store.tx() as db:
                self._event(db, proj["id"], None, "compiler",
                            f"the compiler survey: {found} function(s) match with another compiler than today's")
        return {"tried": n, "confirmed": found, "suggested": suggested}

    def _suggest_from_map(self, proj, confirmed: dict) -> list[str]:
        """Each run of the map that the judge says another compiler built, as a suggestion from the autopilot (applied
        by itself only where the user lets proven compiler changes apply; checked against matched functions)."""
        proj = self._project(proj["slug"])  # its settings as the survey left them
        cmap = self.compiler_map(proj, fresh=True)  # with the survey's confirmations
        agent, _ = self.local_agent("autopilot")
        out = []
        for run in cmap.proposals(self.RANGE_PROOF_BYTES, self.RANGE_PROOF_FUNCTIONS):
            proof = next((int(a) for a, lab in sorted(confirmed.items())
                          if run.start <= int(a) < run.end and compilermap.kind((lab[0], tuple(lab[1]))) == compilermap.kind(run.label)), None)
            if proof is None:
                continue
            fn = self.store.one("SELECT * FROM functions WHERE project_id=? AND vram=?", proj["id"], proof)
            source = self._source_of(fn) if fn else None
            if not source:
                continue
            tc, flags = confirmed[str(proof)][0], tuple(confirmed[str(proof)][1])
            text = (f"The compiler map: {run.functions} functions at 0x{run.start:X}-0x{run.end:X} look built by "
                    f"{self._short((tc, flags))}; {run.confirmed} of them match the game with it and not with today's "
                    f"compiler ({fn['name']}'s C is the check).")
            try:
                said = self.suggest(agent, proj["slug"], text, compiler=tc, flags=" ".join(flags), function=fn["name"],
                                    ranges=f"0x{run.start:X}-0x{run.end:X}", source=source)
            except NetworkError as e:
                said = f"not suggested: {e}"
            out.append(f"0x{run.start:X}-0x{run.end:X} {self._short((tc, flags))}: {said}")
        return out

    def _compiler_brief(self, fn, proj) -> list[str]:
        """What get_function says when the map disagrees with the compiler a function is built with."""
        try:
            cmap = self.compiler_map(proj)
        except Exception:  # never stands between an agent and the function
            logging.getLogger("decompdecomp").exception("the compiler map failed")
            return []
        if cmap is None or fn["status"] == "matched":
            return []
        own = compiler_for(self.cfg(proj), fn["vram"])
        today = (own["toolchain"], tuple(own["flags"]))
        looks = cmap.of.get(fn["vram"])
        lines = []
        if looks and compilermap.kind(looks) != compilermap.kind(today):
            lines.append(f"The compiler map says this part of the game looks built by {self._short(looks)}, not "
                         f"{self._short(today)}: if it won't match, try_compiler(\"{fn['name']}\", \"{looks[0]}\", "
                         f"\"{' '.join(looks[1])}\").")
        if fn["vram"] in cmap.contradicted:
            habits = ", ".join(cmap.never.get(compilermap.kind(today), []))
            lines.append(f"Its code shows a habit {self._short(today)} never has here ({habits}): it was probably "
                         f"built by another compiler. compiler_map() shows which compilers this game uses.")
        for c in self.rangechecks.checks_on(proj, fn["vram"]):
            lines.append(self.rangechecks.task_text(c, fn["name"]))
        return ["", "### Compiler"] + lines if lines else []

    def suggest(self, agent: Agent, slug: str, text: str, compiler: str = "", flags: str = "",
                function: str | None = None, ranges: str = "", source: str = "") -> str:
        """A change to the project's setup an agent can't make itself (the compiler, its flags, anything), for the user
        to apply or dismiss. With a compiler and a function whose C is ready, it's checked first: that C, built with the
        suggested compiler, matches the game or doesn't (GTA San Andreas: agents said EE-GCC 2.96, then 2.95.3). The
        same compiler and flags suggested again counts as another agent agreeing. `ranges` ("0x10BFD0-0x10CD40, ..."):
        the compiler built those address ranges only (a game built by several compilers)."""
        text = " ".join(text.split())[:1000]
        spans = parse_ranges(ranges)
        if spans and not compiler:
            raise NetworkError("say which compiler built those ranges (compiler=...)")
        if not text:
            raise NetworkError("say what should change, and what you saw that says so")
        proj = self._project(slug)
        cfg = self.cfg(proj)
        tc, flag_list, check = None, [], None
        if compiler:
            if self.fork_slug(slug):
                raise NetworkError("a fork builds with its project's own compilers: suggest the change in words only "
                                   "(where its files can have their own, try_compiler keeps the one that matches)")
            tc = self._toolchain_named(cfg, compiler)
            flag_list = flags.split() or consoles.of(cfg).default_flags(tc, cfg.get("gp"))
            if not spans and (tc, flag_list) == (cfg["compiler"]["toolchain"], cfg["compiler"]["flags"]):
                raise NetworkError("that's already the project's compiler and flags")
            if function:
                check = self._check_compiler(proj, tc, flag_list, function, source)
                if re.search(r"does not match|doesn't compile", check):
                    raise NetworkError(f"not suggested: {check.removeprefix('checked: ')}. Test compilers with "
                                       "try_compiler first; suggest the one its C matches with")

        def change(cfg):
            items = cfg.setdefault("suggestions", [])
            same = next((s for s in items if tc and s["status"] == "open" and s["toolchain"] == tc
                         and s["flags"] == flag_list and s.get("ranges", []) == spans), None)
            if same is None:
                same = {"id": max((s["id"] for s in items), default=0) + 1, "text": text, "toolchain": tc,
                        "flags": flag_list, "ranges": spans, "agents": [], "check": None, "status": "open",
                        "t": time.time()}
                items.append(same)
            same["agents"] = sorted({*same["agents"], agent.name})
            if check and (not same["check"] or "matches the game" in check):
                same["check"] = check
            found.append(same)
        found: list[dict] = []
        self._update_cfg(slug, change)
        s = found[0]
        note = f" ({check})" if check else ""
        if spans and tc:  # a hypothesis: its neighbours decide (rangecheck.py), not its size
            return f"Suggested.{note} " + self.rangechecks.propose(proj, s, function, tc, flag_list, spans, source)
        if tc and self.auto_adjusts(proj) and ("matches the game" in (s["check"] or "") or len(s["agents"]) >= self.AGREE):
            try:
                self.resolve_suggestion(slug, s["id"], apply=True, guard=True)
            except NetworkError as e:  # it would break matched code: it waits for the user
                return f"Suggested, not applied by itself: {e}.{note}"
            where = f"{', '.join(f'0x{a:X}-0x{b:X}' for a, b in spans)} build" if spans else "the project builds"
            return (f"Applied (the user lets agreed compiler changes apply themselves): {where} with "
                    f"{consoles.compiler_name(tc)} {' '.join(flag_list)} from now on.{note}")
        return f"Suggested: the user sees it on the project page and can apply it.{note}"

    def auto_adjusts(self, proj) -> bool:
        """Whether this project's proven compiler suggestions apply by themselves: the project's own setting (a game
        whose agents keep finding quirks, like Ridge Racer V, wants it off while another wants it on), else the app's
        older global one, which projects without their own keep."""
        own = self.cfg(proj).get("auto_compiler")
        return self.auto_compiler if own is None else bool(own)

    def compiler_answer(self, slug: str, check_now: bool = False) -> dict:
        """Which compiler built the game, as one answer for the GUI: the project's compiler, the parts built with
        another one (each with what checking it said), and the stretches whose code looks like another compiler's
        that nothing applies yet. The app does the checking itself: the judge's survey runs in the background when
        it's older than SURVEY_AGAIN_SECONDS, and every applied range gets looked at again once. "Survey again" and
        "Check the applied ranges" were buttons nobody could say when to press, the owner and their author included.
        `check_now`: both at once, whatever their age."""
        if self.fork_slug(slug):  # a project whose source files each have a compiler (DS): the files are its parts
            return self.forks.compiler_answer(slug)
        self._rom_only(slug, "the compiler answer")
        proj = self._project(slug)
        cfg = self.cfg(proj)
        main = (cfg["compiler"]["toolchain"], tuple(cfg["compiler"]["flags"]))
        ranges = cfg.get("compiler_ranges", [])
        cmap = self.compiler_map(proj)  # None: not MIPS code (no map, no survey)
        state = self.surveys.get(slug) or {}
        if cmap is not None:
            last = cfg.get("compiler_survey") or {}
            several = len({main} | {(r["toolchain"], tuple(r["flags"])) for r in ranges}) > 1  # (one compiler: nothing to try)
            if several and state.get("state") != "running" and (check_now or time.time() - (last.get("t") or 0) > self.SURVEY_AGAIN_SECONDS):
                state = self.start_survey(slug)
            audited = {(tuple(map(tuple, c["spans"])), c["toolchain"]) for c in cfg.get("range_checks", []) if c.get("audit")}
            if ranges and (check_now or any((((r["start"], r["end"]),), r["toolchain"]) not in audited for r in ranges)):
                self.rangechecks.audit(proj)
                cfg = self.cfg(self._project(slug))
        checks = cfg.get("range_checks", [])
        audits = {tuple(map(tuple, c["spans"])): c for c in checks if c.get("audit")}
        parts = []
        for r in ranges:
            c = audits.get(((r["start"], r["end"]),)) or {}
            parts.append({"start": r["start"], "end": r["end"], "compiler": consoles.compiler_name(r["toolchain"]),
                          "flags": " ".join(r["flags"]),
                          "check": c.get("status", ""), "said": c.get("said", "")})
        elsewhere = sorted((r for r in (cmap.runs if cmap else []) if r.differs()), key=lambda r: -r.functions)
        # the compiler's name and its flags apart: "built with KMC GCC 2.7.2" reads at a glance, the flags are details
        return {"compiler": consoles.compiler_name(main[0]), "flags": " ".join(main[1]), "parts": parts, "mips": cmap is not None,
                "elsewhere": [{"start": r.start, "end": r.end, "compiler": self._short(r.label), "functions": r.functions,
                               "confirmed": r.confirmed} for r in elsewhere[:self.ANSWER_STRETCHES]],
                "elsewhere_count": len(elsewhere),
                "checking": state.get("state") == "running" or any(c["status"] == "checking" for c in checks),
                "survey": {"state": state.get("state"), "tried": len((cfg.get("compiler_survey") or {}).get("tried", {})),
                           "confirmed": len((cfg.get("compiler_survey") or {}).get("confirmed", {})),
                           "running_tried": state.get("tried", 0), "t": (cfg.get("compiler_survey") or {}).get("t")},
                "checks": [{"id": c["id"], "audit": bool(c.get("audit")), "status": c["status"], "said": c.get("said") or "",
                            "ranges": c["spans"], "compiler": self._short((c["toolchain"], tuple(c["flags"])))} for c in checks]}

    def set_auto_adjust(self, slug: str, on: bool) -> None:
        self._update_cfg(slug, lambda cfg: cfg.__setitem__("auto_compiler", bool(on)))

    def _toolchain_named(self, cfg: dict, compiler: str) -> str:
        names = [c.id for c in consoles.of(cfg).compilers]
        tc = next((t for t in names if compiler.strip().lower() in (t, consoles.compiler_name(t).lower())), None)
        if tc is None:
            raise NetworkError(f"no compiler {compiler!r} for this game; its platform's: {', '.join(names)}")
        return tc

    def _check_compiler(self, proj, toolchain: str, flags: list[str], function: str, source: str = "") -> str:
        """Whether a function's C (`source`, else its best attempt, else its latest) matches the game when built with
        this compiler."""
        fn = self._function(function, proj["slug"])
        source = source.strip() or self._source_of(fn)
        if not source:
            return f"not checked: {fn['name']} has no attempt yet"
        ok, d, _ = self._judge_with(proj, fn, toolchain, flags, source)
        if d is None:
            return f"checked: {fn['name']}'s C doesn't compile with it"
        return f"checked: {fn['name']}'s C {'matches the game byte for byte' if ok else 'does not match'} with it"

    RANGE_PROOF_BYTES, RANGE_PROOF_FUNCTIONS = 256, 3

    def try_compiler(self, agent: Agent, slug: str, function: str, compiler: str, flags: str = "",
                     source: str = "") -> str:
        """A function's C (`source`, else its best attempt) built with another compiler and flags, judged like an
        attempt but recorded nowhere: to work out which compiler built a part of the game before suggesting it (on GTA
        San Andreas an agent tried compilers by suggesting them: 128 of its 149 suggestions were disproved)."""
        if self.fork_slug(slug):  # where the project lets each file have its own compiler (DS), the fork engine's
            return self.forks.try_compiler(agent, slug, function, compiler, flags, source)
        proj = self._project(slug)
        cfg = self.cfg(proj)
        tc = self._toolchain_named(cfg, compiler)
        flag_list = flags.split() or consoles.of(cfg).default_flags(tc, cfg.get("gp"))
        fn = self._function(function, slug)
        source = source.strip() or self._source_of(fn)
        if not source:
            raise NetworkError(f"{fn['name']} has no attempt yet: pass the C as source")
        ok, d, problem = self._judge_with(proj, fn, tc, flag_list, source)
        what = f"{consoles.compiler_name(tc)} {' '.join(flag_list)}"
        if d is None:
            return f"{what}: doesn't compile:\n{problem.strip()[-1500:]}"
        if ok:
            self.rangechecks.tried(proj, fn, tc, flag_list, source, True)
            return (f"{what}: MATCHES the game byte for byte. Suggest it: suggest(compiler=\"{tc}\", flags=\"{' '.join(flag_list)}\", "
                    f"function=\"{fn['name']}\", ranges=<the address range it built>, source=<this C>)")
        return f"{what}: score {d.score} ({d.percent:.1f}%), not a match\n{d.render(context=1, max_rows=40)}"

    def flag_variants(self, toolchain: str, base: list[str]) -> list[list[str]]:
        """The flags worth trying for a function on this compiler: its optimisation levels, small data on and off
        (MIPS GCC's -G; CodeWarrior's -sdatathreshold), and the few options that change the shape of code most.
        Each variant changes the project's flags in one place, so what matches says which flag it was."""
        family = next((f for prefix, f in FLAG_FAMILIES if toolchain.startswith(prefix)), "gcc")
        levels, extras = FLAG_LEVELS[family], FLAG_EXTRAS[family]
        current = next((f for f in base if f in levels), None)
        out = [base]
        for level in levels:
            if level != current:
                out.append([level if f == current else f for f in base] if current else [*base, level])
        if family in ("gcc", "ee"):
            small = next((f for f in base if f.startswith("-G") and f[2:].isdigit()), None)
            for g in SMALL_DATA_SIZES:
                if small and small != f"-G{g}":
                    out.append([f"-G{g}" if f == small else f for f in base])
        elif family == "mwcps2" and "-sdatathreshold" in base:
            at = base.index("-sdatathreshold") + 1
            for g in SMALL_DATA_SIZES:
                if base[at] != str(g):
                    out.append(base[:at] + [str(g)] + base[at + 1:])
        out += [[*base, extra] for extra in extras if extra not in base]
        return out[:MAX_FLAG_VARIANTS]

    def search_flags(self, agent: Agent, slug: str, function: str, compiler: str = "", source: str = "") -> str:
        """A function's C (its best attempt, or `source`) built with the variants of its compiler's flags
        (flag_variants), each judged like an attempt and recorded nowhere: when the C looks right but won't match,
        the flags are often what's wrong, and agents tried them one compile at a time."""
        if self.fork_slug(slug):
            raise NetworkError("a fork builds with its project's own compilers and flags")
        proj = self._project(slug)
        cfg = self.cfg(proj)
        fn = self._function(function, slug)
        own = compiler_for(cfg, fn["vram"])
        tc = self._toolchain_named(cfg, compiler) if compiler else own["toolchain"]
        base = list(own["flags"]) if tc == own["toolchain"] else consoles.of(cfg).default_flags(tc, cfg.get("gp"))
        source = source.strip() or self._source_of(fn)
        if not source:
            raise NetworkError(f"{fn['name']} has no attempt yet: pass the C as source")
        results = []
        for flags in self.flag_variants(tc, base):
            ok, d, _ = self._judge_with(proj, fn, tc, flags, source)
            if d is not None:
                results.append((0 if ok else max(1, d.score), flags))
        if not results:
            return f"{consoles.compiler_name(tc)}: the C doesn't compile with any of its flag variants"
        results.sort(key=lambda r: r[0])
        lines = [f"- score {score:>5}  {' '.join(flags)}" + ("   ← the project's flags" if flags == base else "")
                 for score, flags in results[:FLAGS_SHOWN]]
        best_score, best = results[0]
        head = f"{consoles.compiler_name(tc)}, {len(results)} flag variants for {fn['name']} (lower is better):\n"
        if best_score == 0 and best != base:
            if tc == own["toolchain"]:
                self.rangechecks.tried(proj, fn, tc, best, source, True)
            return (head + "\n".join(lines) + f"\nIt MATCHES with {' '.join(best)}. Suggest it: suggest(compiler=\"{tc}\", "
                    f"flags=\"{' '.join(best)}\", function=\"{fn['name']}\", ranges=<the address range it built>, "
                    f"source=<this C>)")
        own_score = next((score for score, flags in results if flags == base), None)
        verdict = ("The flags aren't the problem: the project's are as good as any." if own_score == best_score
                   else "Other flags come closer, but none matches: look at the C again first.")
        return head + "\n".join(lines) + "\n" + verdict

    def resolve_suggestion(self, slug: str, sid: int, apply: bool, guard: bool = False) -> None:
        """The user's answer to a suggestion: apply it (a compiler: the project switches to it) or dismiss it."""
        hit = next((s for s in self.cfg(self._project(slug)).get("suggestions", []) if s["id"] == sid), None)
        if hit is None:
            raise NetworkError(f"no suggestion {sid}")
        if apply and hit["toolchain"] and hit.get("ranges"):
            self.set_compiler_ranges(slug, hit["ranges"], hit["toolchain"], hit["flags"])
        elif apply and hit["toolchain"]:
            self.set_compiler(slug, hit["toolchain"], hit["flags"], guard=guard)
        self._update_cfg(slug, lambda cfg: next(s for s in cfg["suggestions"] if s["id"] == sid).__setitem__(
            "status", "applied" if apply else "dismissed"))

    def set_compiler_ranges(self, slug: str, ranges: list[list[int]], toolchain: str | None,
                            flags: list[str] | None = None, reopen: bool = False) -> None:
        """Address ranges built by another compiler than the rest of the game (replacing ranges they overlap), or
        with toolchain None: those ranges back to the project's compiler. Matched functions that wouldn't match any
        more stop it, unless `reopen` (removing a range the user judged a quirk: Ridge Racer V's two GCC 2.96 ranges
        held two functions whose C only matched with 2.96): then they're open again, their C their best attempt."""
        if self.fork_slug(slug):
            raise NetworkError("a fork builds with the project's own compilers and flags")
        if toolchain:
            cfg = self.cfg(self._project(slug))
            toolchain = toolchains.get(toolchain).id
            flags = flags or consoles.of(cfg).default_flags(toolchain, cfg.get("gp"))
        flags = flags or []

        before = self.cfg(self._project(slug)).get("compiler_ranges", [])

        def change(cfg):
            keep = [r for r in cfg.get("compiler_ranges", []) if all(r["end"] <= a or r["start"] >= b for a, b in ranges)]
            if toolchain:
                keep += [{"start": a, "end": b, "toolchain": toolchain, "flags": flags} for a, b in ranges]
            cfg["compiler_ranges"] = sorted(keep, key=lambda r: r["start"])
        self._update_cfg(slug, change)
        proj = self._project(slug)
        # functions already matched in those ranges must still match with it (auto-adjust runs unattended)
        inside = [f for f in self.store.all("SELECT * FROM functions WHERE project_id=? AND status='matched'", proj["id"])
                  if any(a <= (f["vram"] or -1) < b for a, b in ranges)]
        broken = self.regression_test(proj, None, only=inside,
                                      header_for=lambda f: self._context(proj["id"], f["unit"])["header"]) if inside else []
        if broken and reopen and not toolchain:
            self._reopen(proj, [n for n, _ in broken], "its range was removed")
        elif broken:
            self._update_cfg(slug, lambda cfg: cfg.__setitem__("compiler_ranges", before))
            raise NetworkError(f"{len(broken)} matched function(s) in those ranges wouldn't match with it any more "
                               f"({', '.join(n for n, _ in broken[:5])}): narrow the ranges"
                               + ("" if toolchain else ", or remove them and reopen those functions"))
        with self.store.tx() as db:
            where = ", ".join(f"0x{a:X}-0x{b:X}" for a, b in ranges)
            self._event(db, proj["id"], None, "compiler", f"{consoles.compiler_name(toolchain)} {' '.join(flags)} for {where}"
                        if toolchain else f"the project's compiler again for {where}")

    def _reopen(self, proj, names: list[str], why: str) -> None:
        """Matched functions open again: their matching C becomes their best attempt, scored with the compiler they're
        built with now, so the next agent starts from it."""
        cfg = self.cfg(proj)
        for name in names:
            fn = self.store.one("SELECT * FROM functions WHERE project_id=? AND name=?", proj["id"], name)
            if fn is None or fn["status"] != "matched":
                continue
            own = compiler_for(cfg, fn["vram"])
            _, d, _ = self._judge_with(proj, fn, own["toolchain"], list(own["flags"]), fn["matched_source"] or "")
            with self.store.tx() as db:
                db.execute("UPDATE functions SET status='open', best_source=matched_source, best_agent_id=matched_agent_id, "
                           "best_score=?, matched_source=NULL, matched_agent_id=NULL, matched_at=NULL WHERE id=?",
                           (d.score if d is not None else None, fn["id"]))
                self._event(db, proj["id"], None, "reopen", f"{name}: open again ({why})")

    def set_compiler(self, slug: str, toolchain: str, flags: list[str] | None = None, guard: bool = False) -> None:
        """`guard` (auto-adjust, unattended): matched functions must still match with it, checked on up to 40 of them
        across the game, or nothing changes."""
        if self.fork_slug(slug):
            raise NetworkError("a fork builds with the project's own compilers and flags")
        proj = self._project(slug)
        tc = toolchains.get(toolchain)
        cfg = json.loads(proj["config"])  # a copy of its own: changed below
        before = cfg["compiler"]
        cfg["compiler"] = {"toolchain": tc.id, "flags": flags or consoles.of(cfg).default_flags(tc.id, cfg.get("gp")),
                           "timeout": 20}
        with self.store.tx() as db:
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
            db.execute("UPDATE functions SET seed=NULL WHERE project_id=? AND status='open'", (proj["id"],))
            self._event(db, proj["id"], None, "compiler", f"{tc.name} {' '.join(cfg['compiler']['flags'])}")
        matched = self.store.all("SELECT * FROM functions WHERE project_id=? AND status='matched' ORDER BY vram", proj["id"])
        sample = matched[::max(1, len(matched) // 40)][:40]
        broken = self.regression_test(self._project(slug), None, only=sample,
                                      header_for=lambda f: self._context(proj["id"], f["unit"])["header"]) if guard and sample else []
        if broken:
            self._update_cfg(slug, lambda c: c.__setitem__("compiler", before))
            raise NetworkError(f"{len(broken)} matched function(s) wouldn't match with it any more "
                               f"({', '.join(n for n, _ in broken[:5])})")
