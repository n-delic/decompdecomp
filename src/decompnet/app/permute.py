"""The permuter, in the background: the decomp community's way through the last few percent.

A function at 95% usually differs in register allocation or instruction order, which comes down to how the C
is written, not what it does: the order of statements and declarations, a temporary, a cast, `x += 1` for
`x++`, an if turned around. Agents doing it by hand spend turns (and tokens) on it, and stall. Here it runs on its
own, on spare CPU, on the near misses agents left (or autopilot's drafts reached). Permutation workers never
reserve functions: agents can claim them while side searches continue. Each freshly judged improvement can become
the function's best attempt, and each exact result a match. Concurrently changed build inputs reject stale results. What matches is still judged byte for byte, like everything else.

Variants come from decomp-permuter (MIT, a pinned tool: Compilers & tools, or `decomp setup`): permuter_gen.py, run
in the tool's own environment, rewrites the function's C on its parse tree, BATCH variants at a time, with the
weights decomp-permuter tuned for the function's compiler. Our own judge scores each one (`_score`): a variant that
doesn't compile is skipped. Its rewrites don't all keep the meaning (it moves statements, retypes, deletes code), so
only what the judge says settles anything: a match is the same machine code as the game's, and an improvement is
only closer code for an agent to start from. Without the tool, or for C it can't read (a macro call of the source's
own), the six simple rewrites of permuter.py make the variants instead.

It runs on the background workers every project shares with the machine drafts (workers.py: one per spare core, up
to four), each at a lower CPU priority that the compilers it runs inherit: the app and the agents' compiles come first.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import random
import subprocess
import threading
import time
from concurrent.futures import wait
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

import decomprepair

from .. import compile_timing, consoles, m2cfix, permuter_claims, scoring, seeds, toolchains
from ..candidate_work import trial
from ..compile_sessions import batched
from ..forks.project import ForkError
from ..hostenv import NO_WINDOW
from ..network import NetworkError
from ..permuter import mutate
from ..project import compiler_for
from ..repair_feedback import trial as repair_trial
from . import draft_repairs, local_claims, switch_tables, workers
from .trial_bookkeeping import buffered

log = logging.getLogger("decompdecomp")
NEAR = 0.06  # a near miss: at most this much of the function differs (best_score <= 6% of n_insns * 100)
SECONDS = 60  # per function per turn
MAX_TURNS = 4  # turns per function before it's left to agents (counted in the database: functions.permuted)
CANDIDATES = 20  # near misses looked up at a time, for the workers to share
TOOL = "decomp-permuter"
GENERATOR = Path(decomprepair.__file__).resolve().parent / "permuter_gen.py"  # with its sibling modules
BATCH = 64  # variants per run of the generator: about 0.15 s to write, a few seconds to judge
GENERATOR_SECONDS = 30  # a batch that takes longer is stuck: its randomizer retries until a rewrite applies
READ_BYTES = 1 << 16
MAX_SEED = 2**31  # seeds of the generator's runs: the same function and turn write the same variants
REWRITE_MISSES = 200  # simple rewrites in a row that change nothing: none applies to the C
MAX_INSNS = 400  # default size ceiling; larger functions cost more time per variant
REPAIR_MAX_INSNS = 1024  # deterministic hypotheses keep their own 32-attempt / 20-second cap
IDLE_SECONDS = 180  # with no near misses, how long until the permuter looks again
IDLE_LOOKS = 20  # looks in a row with nothing to do (an hour) and it's done until agents start again
HOLD_SPARE_SECONDS = local_claims.HOLD_SPARE_SECONDS
_EOF = object()
# decomp-permuter's weights per compiler (its default_weights.toml), by the start of a toolchain's id
FAMILIES = (("ido", "ido"), ("gcc", "gcc"), ("ee-gcc", "gcc"), ("psp-gcc", "gcc"), ("ps3", "gcc"), ("agbcc", "gcc"),
            ("mwc", "mwcc"))


def weight_family(cfg: dict, vram: int | None, fork: bool, function=None) -> str:
    """Which of decomp-permuter's weight sets suits the compiler that builds the code at `vram`."""
    if fork:  # forks name their console, not a toolchain: the console says its compilers' family
        return consoles.get(cfg.get("platform")).compiler_family or "base"
    toolchain = (compiler_for(cfg, vram, function).get("toolchain") or "") if cfg.get("compiler") else ""
    return toolchains.compiler_family(toolchain) or next((family for prefix, family in FAMILIES if toolchain.startswith(prefix)), "base")


def available() -> bool:
    return toolchains.installed(TOOL) is not None


def generator_argv() -> list[str]:
    tool = toolchains.path(TOOL)
    return [str(toolchains.venv_bin(tool / "venv", "python")), "-I", str(GENERATOR), str(tool)]


class TracedVariant(str):
    """A generated source with the randomizer's successful rewrite history."""
    def __new__(cls, source, lineage):
        value = super().__new__(cls, source)
        value.lineage = lineage
        return value


class GenerationStopped(Exception):
    """The current generation stream no longer has a search budget."""


class Generator:
    """The generator (permuter_gen.py) for one turn, in decomp-permuter's environment. It stays up for the turn, so
    it parses the header once per C it starts from: that takes a second for a header of 300 KB (debug symbols give a
    game thousands of types), where writing a batch of 64 variants takes 0.1 s."""

    def __init__(self, argv: list[str] | None = None):
        self.proc = subprocess.Popen(argv or generator_argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL,
                                     creationflags=NO_WINDOW | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        # Windows select() accepts sockets only. A reader thread also gives pipe reads a deadline there.
        self._closed = threading.Event()
        self._lines = queue.Queue(maxsize=32)
        self._reader = threading.Thread(target=self._read, daemon=True, name="permuter-output")
        self._reader.start()

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:
                if not self._put_line(line):break
        finally:
            self._put_line(_EOF)

    def _put_line(self, line):
        while not self._closed.is_set():
            try:
                self._lines.put(line, timeout=0.1)
                return True
            except queue.Full:
                pass
        return False

    def chunks(self, job, check):
        """Read small chunks while the existing generator process writes the rest.

        Closing an obsolete stream kills its generator rather than mixing replies
        from two parents. The next round starts a new generator when needed.
        """
        done = False
        self.empty = True
        self.failed = False
        try:
            self.proc.stdin.write(json.dumps(job).encode() + b"\n")
            self.proc.stdin.flush()
            deadline = time.monotonic() + GENERATOR_SECONDS
            chunk = []
            failed = False
            while True:
                check()
                try:line = self._lines.get(timeout=0.1)
                except queue.Empty:
                    if time.monotonic() >= deadline:
                        self.failed = True
                        return
                    continue
                if line is _EOF:
                    self.failed = True
                    return
                try:out = json.loads(line)
                except ValueError:continue
                if not isinstance(out, dict):continue
                if 'done' in out:
                    done = True
                    if chunk:yield chunk
                    return
                if 'error' in out:
                    failed = self.failed = True
                    log.info("permuter: the generator could not read %s", job['function'])
                elif not failed and isinstance(out.get('variant'), str):
                    from decomprepair.permuter_initialization import unsafe_temporary
                    if unsafe_temporary(job['source'], out['variant'], job['function'], job.get('header', '')):continue
                    compile_timing.add('generated', 1)
                    chunk.append(TracedVariant(out['variant'], out['lineage'])
                                 if isinstance(out.get('lineage'), list) else out['variant'])
                    self.empty = False
                    if len(chunk) >= 8:
                        yield chunk
                        chunk = []
                        # Compiler time is not a generator timeout.
                        deadline = time.monotonic() + GENERATOR_SECONDS
        except OSError:
            self.failed = True
            return
        finally:
            if not done:self.close()

    def batch(self, job: dict) -> list[str] | None:
        """Variants of a function's C (the job's fields: permuter_gen.py), or None when the generator can't read
        the C, has stopped or doesn't answer: then the simple rewrites take over."""
        try:
            self.proc.stdin.write(json.dumps(job).encode() + b"\n")
            self.proc.stdin.flush()
        except OSError:
            return None
        deadline = time.monotonic() + GENERATOR_SECONDS
        variants: list[str] | None = []
        while True:
            line = self._line(deadline)
            if line is None or line is _EOF:
                if line is _EOF:
                    try:
                        self.proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                log.info("permuter: the generator stopped or hung on %s", job["function"])
                self.close()
                return None
            try:
                out = json.loads(line)
            except ValueError:
                continue
            if not isinstance(out, dict):
                continue
            if "done" in out:
                return variants
            if "error" in out:  # read on to its "done": the next job's answers start after it
                log.info("permuter: decomp-permuter can't read %s: %s", job["function"], str(out["error"])[:300])
                variants = None
            elif variants is not None and isinstance(out.get("variant"), str):
                from decomprepair.permuter_initialization import unsafe_temporary
                if not unsafe_temporary(job["source"], out["variant"], job["function"], job.get("header", "")):
                    compile_timing.add('generated', 1)
                    variants.append(TracedVariant(out["variant"], out["lineage"]) if isinstance(out.get("lineage"), list) else out["variant"])
    def _line(self, deadline: float):
        """The generator's next line, or None when it has stopped or hasn't answered by `deadline`."""
        try:
            with compile_timing.phase('generation'):
                return self._lines.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            return None

    def close(self) -> None:
        self._closed.set()
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        self._reader.join(timeout=0.5)
        if not self._reader.is_alive():
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                # BufferedWriter.close retries a failed request's flush after the child has
                # exited. Disposal must not replace its failed-job fallback or cancellation.
                pass
            finally:
                self.proc.stdout.close()


@dataclass
class State:
    state: str = "running"
    tried: int = 0  # functions permuted
    variants: int = 0
    improved: int = 0
    matched: int = 0
    current: str = ""  # the functions the workers hold now
    started_at: float = field(default_factory=time.time)
    stop: threading.Event = field(default_factory=threading.Event)
    repairs: int = 0  # deterministic candidates, with a separate per-round budget
    _held: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, **counts: int) -> None:
        with self._lock:  # every worker counts here
            for name, n in counts.items():
                setattr(self, name, getattr(self, name) + n)

    def holding(self, name: str, held: bool) -> None:
        with self._lock:
            if held:
                self._held.append(name)
            else:
                self._held.remove(name)
            self.current = ", ".join(self._held)


class Permuter:
    def __init__(self, net):
        self.net = net
        self.parallel_claims = True
        self.jobs: dict[str, State] = {}
        self.max_insns, self.seconds, self.max_turns = MAX_INSNS, SECONDS, MAX_TURNS

    def configure(self, settings: dict) -> None:
        """Global random-permutation budgets; running turns keep their existing deadline."""
        self.max_insns = max(1, min(100000, int(settings.get('permuter_max_insns', MAX_INSNS))))
        self.seconds = max(1, min(3600, int(settings.get('permuter_seconds', SECONDS))))
        self.max_turns = max(1, min(1000, int(settings.get('permuter_max_turns', MAX_TURNS))))

    def status(self, slug: str) -> dict | None:
        st = self.jobs.get(slug)
        if st is None:
            return None
        # Source preparation attaches agents and private source measurements to this
        # worker. Neither belongs in JSON (or in the UI). New internal attributes
        # must not break the entire project summary or expose source/header text.
        with st._lock:
            return {name:getattr(st,name) for name in (
                "state", "tried", "variants", "improved", "matched", "current", "started_at", "repairs"
            )} | {"threads": workers.WORKERS}

    def start(self, slug: str, *, automatic=False) -> State | None:
        """Run on the project's near misses until there are none (looking again every few minutes while the
        app runs: agents make new ones). `automatic`: started by the app, not the user (the worker process's
        start tells them apart; here they run the same)."""
        if (st := self.jobs.get(slug)) is not None and st.state in ("running", "update_wait"):
            return st
        if slug in self.net.links:  # a room member: the host's copy permutes, and its matches come back here
            return None
        st = self.jobs[slug] = State()
        st.thread = threading.Thread(target=self._loop, args=(slug, st), daemon=True, name=f"permuter-{slug}")
        st.thread.start()
        return st

    def stop(self, slug: str) -> None:
        if (st := self.jobs.get(slug)) is not None:
            st.stop.set()

    def join(self) -> None:
        """Drain owned loops before interpreter finalizers can remove active compiler scratch.

        The worker process's parent retains its bounded terminate/kill deadline.
        """
        for state in list(self.jobs.values()):
            thread = getattr(state, 'thread', None)
            if thread is not None and thread is not threading.current_thread():
                thread.join()

    def candidates(self, proj, limit: int | None = None) -> list:
        limit = limit or max(CANDIDATES, 2 * workers.WORKERS)  # (a worker for each, and the next ones waiting)
        from . import permuter_inputs
        rows = self.net.project_store(proj['id']).all("SELECT * FROM functions WHERE project_id=? AND status='open' "
                                  "AND best_source IS NOT NULL AND best_score>0 AND best_score<=n_insns*100*? "
                                  "AND n_insns<=?", proj['id'], NEAR, self.max_insns)
        permuter_inputs.refresh(self.net, proj, rows)
        from .boundary_repairs import blocked
        held=blocked(self.net,proj)
        candidates=self.net.project_store(proj['id']).all(
            "SELECT * FROM functions WHERE project_id=? AND status='open' AND best_source IS NOT NULL "
            "AND best_score > 0 AND best_score <= n_insns * 100 * ? AND n_insns <= ? AND permuted < ? "
            "AND id NOT IN (SELECT function_id FROM claims) AND id NOT IN (SELECT value FROM json_each(?)) ORDER BY CAST(best_score AS REAL) / (n_insns * 100) LIMIT ?",
            proj["id"], NEAR, self.max_insns, self.max_turns, json.dumps(list(held)), limit)
        return [r for r in candidates if r["id"] not in held]

    def _loop(self, slug: str, st: State) -> None:
        # Concurrent background and explicit passes only need credit, not rotating bearer tokens.
        agent = self.net.attribution_agent("permuter")
        idle = 0
        try:
            while not st.stop.is_set():
                from .source_runtime import checkpoint, idle_wait
                checkpoint(self.net, st)
                if st.stop.is_set():
                    return
                try:
                    proj = self.net.project_row(slug)
                except NetworkError:
                    return  # the project is gone
                from .source_preparation import run as prepare_sources
                with self._deferring():
                    prepare_sources(self, proj, st, agent=agent)
                    proj = self.net.project_row(slug)
                    self._repair_round(agent, proj, st)
                todo = self.candidates(proj)
                if not todo:
                    idle += 1
                    if idle > IDLE_LOOKS:
                        return
                    idle_wait(self.net, st, IDLE_SECONDS)
                    continue
                idle = 0
                from .purpose_work import Executor
                with Executor(workers.WORKERS, purpose='Background permutation', project=slug, stop=st.stop.set, initializer=workers.lower_priority) as pool:
                    wait([pool.submit(self._turn, agent, proj, fn, st) for fn in todo])
        finally:
            st.state = "stopped" if st.stop.is_set() else "done"

    def repair_pass(self, slug: str, stop: threading.Event) -> State:
        """Autopilot's Run again can try these without starting agents or random permutation."""
        st = State(stop=stop)
        if slug not in self.net.links:
            agent = self.net.attribution_agent("permuter")
            from .source_preparation import run as prepare_sources
            prepare_sources(self, self.net.project_row(slug), st, agent=agent)
            self._repair_round(agent, self.net.project_row(slug), st)
        return st

    @buffered
    @batched
    def _repair_round(self, agent, proj, st: State, *, function_id=None, budget=32, seconds=20) -> None:
        """At most 32 compiles / 20 seconds before normal random turns; rotate even over ineligible drafts.

        Cache individual candidates with their target, header and configured compiler. A new source/header/
        configuration gets another chance. This uses the same judge for ROM and imported project modes.
        """
        from .progress import ProgressMoved

        key = f"draft-repair-cursor:{proj['id']}"
        saved = self.net.project_store(proj['id']).one("SELECT value FROM settings WHERE key=?", key)
        cursor = int(saved['value']) if saved else 0
        todo = self.net.project_store(proj['id']).all(
            "SELECT * FROM functions WHERE project_id=? AND status='open' AND best_source IS NOT NULL "
            "AND best_score>0 AND n_insns<=? AND (? IS NULL OR id=?) AND id NOT IN (SELECT function_id FROM claims) "
            "ORDER BY (id<=?), id LIMIT ?", proj['id'], REPAIR_MAX_INSNS, function_id, function_id, cursor, CANDIDATES)
        deadline, attempts = time.monotonic() + seconds, 0
        fork = self.net.fork_slug(proj['slug'])
        symbols = [tuple(row) for row in self.net.project_store(proj['id']).all(
            "SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name", proj['id'])]
        for fn in todo:
            if st.stop.is_set() or attempts >= budget or time.monotonic() >= deadline:
                break
            with self.net.project_store(proj['id']).tx() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, str(fn['id'])))
            try:
                context = self.net._context(proj['id'], fn['unit'])
                header = self.net.forks.permuter_header(fork, fn['name']) if fork else (context['header'] if context else '')
            except (ForkError, NetworkError, OSError) as error:
                log.info('permuter: %s: header unavailable: %s', fn['name'], error)
                continue
            # Autopilot's deterministic pass must reach joint hypotheses too, before
            # source-only scores discard their corrected loop exit. No claim is held yet.
            from decomprepair import guarded_loop_repairs

            from . import matching_preflight
            if (not fork and hasattr(self.net,'_comparison_digest')
                    and guarded_loop_repairs.probes(proj,fn,compiler_for(self.net.cfg(proj),fn['vram'],fn),header)):
                try:
                    paired=matching_preflight.run(self.net,proj,fn,log.info,
                        lambda:not st.stop.is_set() and time.monotonic()<deadline,
                        max_profiles=min(4,budget-attempts))
                except NetworkError as error:
                    log.info('permuter: joint profile check deferred: %s',error)
                    continue
                attempts+=paired['tested'];st.add(variants=paired['tested'],repairs=paired['tested'])
                proj=self.net.project_row(proj['slug'])
                fn=self.net.project_store(proj['id']).one('SELECT * FROM functions WHERE id=?',fn['id'])
                if fn['status']=='matched':
                    st.add(improved=int(bool(paired.get('applied'))),matched=int(bool(paired.get('applied'))))
                    continue
                if paired.get('link') or attempts>=budget or st.stop.is_set() or time.monotonic()>=deadline:
                    continue
            labels = {}
            candidates = draft_repairs.variants(fn['best_source'], fn['name'], proj['arch'], fn['asm'] or '', flags=compiler_for(self.net.cfg(proj),fn['vram'], fn).get('flags',[]) if not fork else [], labels=labels,header=header,limit=None,tables=switch_tables.read(self.net,proj,fn),callees=draft_repairs.capture_followup_repairs.callee_evidence(self.net,proj,fn,fn['best_source']),entry_scope=draft_repairs.promoted_entry_repairs.scope(self.net,proj,fn))
            from decomprepair import normalization_repairs

            from . import repair_ranking
            normalization_repairs.extend_frame(candidates, labels, fn['best_source'], fn['name'], proj['arch'], repair_ranking.evidence(self.net,fn['id'],agent.id))
            ranked = repair_ranking.order(dict.fromkeys(labels.get(text, 'draft_repair') for text in candidates),repair_ranking.evidence(self.net,fn['id'],agent.id))
            effect_scope=repair_ranking.effect_scope(self.net,fn,fn['best_source']) if hasattr(self.net,'_context') else None
            ranked=repair_ranking.demote(self.net,effect_scope,ranked)
            ranked=repair_ranking.private_abi_order(ranked,proj['arch'],fn['asm'] or '',repair_ranking.evidence(self.net,fn['id'],agent.id))
            if not self._hold(agent, fn, count_turn=False):
                continue
            st.holding(fn['name'], True)
            try:
                current = self.net.project_store(proj['id']).one("SELECT * FROM functions WHERE id=?", fn['id'])
                if current['status'] != 'open' or current['best_source'] != fn['best_source']:
                    continue
                header = (self.net.forks.permuter_header(fork, fn['name']) if fork else
                          self.net._context(proj['id'], fn['unit'])['header'])
                # Imported builds own flags outside config. Their cache is only for this process/session;
                # the next explicit Run again/start retries after a user has changed their build setup.
                scope = st.started_at if fork else None
                context = repr((scope, header, self.net.cfg(proj), symbols, proj['arch'], proj['big_endian'],
                                fn['unit'], fn['target'], fn['relocs'], fn['vram']))
                best = fn['best_score']
                if hasattr(self.net, 'keep_diff') and attempts < budget and time.monotonic() < deadline:
                    measured, compiled = self._baseline(agent, proj, fn, st, fork)
                    attempts += compiled
                    if measured == 0:
                        st.add(improved=1, matched=1)
                        continue
                    normalization_repairs.extend_frame(candidates, labels, fn['best_source'], fn['name'], proj['arch'], repair_ranking.evidence(self.net,fn['id'],agent.id))
                    ranked = repair_ranking.order(dict.fromkeys(labels.get(text, 'draft_repair') for text in candidates), repair_ranking.evidence(self.net, fn['id'], agent.id))
                    ranked = repair_ranking.demote(self.net, effect_scope, ranked)
                from . import adaptive_repairs, permuter_focus
                policy_rows = repair_ranking.evidence(self.net, fn['id'], agent.id)
                ranked = adaptive_repairs.order(self.net, proj, fn, policy_rows, ranked)
                ranked = repair_ranking.private_abi_order(ranked,proj['arch'],fn['asm'] or '',policy_rows)
                policy_key = adaptive_repairs.profile(self.net, proj, fn)
                candidates.sort(key=lambda text: ranked.index(labels.get(text, 'draft_repair')) if labels.get(text, 'draft_repair') in ranked else len(ranked))
                from . import proposal_progress
                progressed, cursor = proposal_progress.order(self.net,proj,fn,fn['best_source'],[(labels.get(c,'draft_repair'),c) for c in candidates])
                for _,candidate in progressed:
                    if st.stop.is_set() or attempts >= budget or time.monotonic() >= deadline:
                        break
                    digest = hashlib.sha256((context + candidate).encode()).hexdigest()
                    trial = f"draft-repair-v15:{fn['id']}:{digest}"
                    cached = self.net.project_store(proj['id']).one("SELECT outcome FROM reuse_trials WHERE function_id=? AND signature=?", fn['id'], trial)
                    # A reopened or restored draft may need an old winner again. Cache hits can
                    # avoid misses, but never substitute for rejudging a possible improvement.
                    if cached and (not cached['outcome'].isdecimal() or int(cached['outcome']) >= best):
                        continue
                    attempts += 1
                    st.add(variants=1, repairs=1)
                    baseline=getattr(self.net,'_agent_outcomes',{}).get((agent.id,fn['id']))
                    if baseline and baseline.get('source')!=fn['best_source']:
                        baseline=None
                    policy_started = time.monotonic()
                    with repair_trial({'kind':'deterministic draft repair','family':labels.get(candidate,'draft_repair'),'base_source_sha256':hashlib.sha256(fn['best_source'].encode()).hexdigest(),'source_sha256':hashlib.sha256(candidate.encode()).hexdigest()}):
                        score = self._score(agent, proj, fn, candidate, best, fork)
                    if not getattr(self, 'trace_variants', False) and getattr(self, '_trial_outcome', '') != 'no_edit':
                        adaptive_repairs.record(self.net, policy_key, permuter_focus.classify(policy_rows),
                                                labels.get(candidate, 'draft_repair'), best, score,
                                                (time.monotonic()-policy_started)*1000, origin="background deterministic")
                    if cursor:cursor.measured(labels.get(candidate),candidate,score)
                    measured=getattr(self.net,'_agent_outcomes',{}).get((agent.id,fn['id']))
                    if measured and measured.get('source')==candidate:
                        repair_ranking.remember(self.net,effect_scope,labels.get(candidate,'draft_repair'),baseline,measured)
                    with self.net.project_store(proj['id']).tx() as db:
                        db.execute("INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)",
                                   (fn['id'], trial, str(score), "targeted draft repair"))
                    if score is not None and score < best:
                        st.add(improved=1)
                        best = score
                        if score == 0:
                            st.add(matched=1)
                            break
                if function_id is None and not fork and best > 0 and attempts <= budget-3 and time.monotonic() < deadline and not st.stop.is_set():
                    attempts += self._repair_flags(agent, proj, fn, st, deadline)
            except ProgressMoved:
                # Discard this round's project/header/source snapshot; the next pass reloads it.
                log.info("permuter: %s: progress moved; targeted repairs deferred", fn['name'])
                return
            except Exception:
                log.exception("permuter: targeted repairs failed for %s", fn['name'])
            finally:
                st.holding(fn['name'], False)
                self._let_go(agent, fn)

    def _deferring(self):
        """Inside a turn, a project's saved files are read once and database-only changes published every few
        seconds (Progress.deferring, as for agents' tool calls): each lookup synced, and on a 2,000-function game
        eight threads queued on the project's lock to render its save, about half the variants of a 4-minute run."""
        progress = getattr(self.net, 'progress', None)
        return progress.deferring() if progress is not None else nullcontext()

    def _turn(self, agent, proj, fn, st: State) -> None:
        from .progress import ProgressMoved

        try:
            with self._deferring():
                self._permute(agent, proj, fn, st)
        except ProgressMoved:
            # A stale result is no evidence that the current source cannot be permuted.
            log.info("permuter: %s: progress moved; random turn deferred", fn["name"])
        except NetworkError as e:  # the project refused its source (a helper the file hasn't): an answer, not a crash
            log.info("permuter: %s: %s (left to agents)", fn["name"], str(e).splitlines()[0])
            with self.net.project_store(proj['id']).tx() as db:  # the same best attempt is refused every time: no more turns on it
                db.execute("UPDATE functions SET permuted=MAX(permuted, ?) WHERE id=?", (self.max_turns, fn["id"]))
        except Exception as e:  # one odd function must not stop the rest
            log.info("permuter: %s: %s", fn["name"], e, exc_info=True)

    def _hold(self, agent, fn, *, count_turn=True, seconds=None) -> bool:
        return permuter_claims.hold(self.net, agent, fn, self.seconds if seconds is None else seconds,
                                 count_turn=count_turn)

    def _let_go(self, agent, fn) -> None:
        local_claims.release(self.net, agent, fn)

    def _after_improvement(self, agent, proj, fn, st, fork, best, source):
        """Deep search can run bounded local repairs here without changing normal random turns."""
        return best, source

    def _compiler_preflight(self, proj, fn, st, deadline):
        from .matching_preflight import run
        return run(self.net, proj, fn, log.info,
                   lambda: not st.stop.is_set() and time.monotonic() < deadline)

    @buffered
    @batched
    def _permute(self, agent, proj, fn, st: State) -> None:
        seconds = self.seconds
        preflight_deadline = time.monotonic() + seconds
        preflight = self._compiler_preflight(proj, fn, st, preflight_deadline)
        seconds = max(0, preflight_deadline-time.monotonic())
        st.add(variants=preflight['tested'])
        proj = self.net.project_row(proj['slug'])
        fn = self.net._function(fn['name'], proj['slug']) if hasattr(self.net, '_function') else fn
        if fn['status'] == 'matched':
            st.add(tried=1, matched=int(bool(preflight.get('applied'))), improved=int(bool(preflight.get('applied'))))
            return
        if not fn['best_source'] or fn['best_score'] is None:
            raise NetworkError('Saved C is no longer available for this function; waiting for a new saved candidate.')
        if preflight.get('link'):
            if self._hold(agent, fn):
                st.add(tried=1)
                self._let_go(agent, fn)
            return  # Keep the C for address repairs/agents; random rewriting cannot resolve a missing address.
        if seconds <= 0:
            return
        if st.stop.is_set() or not self._hold(agent, fn, seconds=seconds):
            return
        st.add(tried=1)
        st.holding(fn["name"], True)
        fork = self.net.fork_slug(proj["slug"])
        from . import random_safety
        rng = random.Random(f"{fn['id']}-{fn['permuted']}"+getattr(self, "random_salt", ""))
        gen = None
        header = ''
        try:
            random_safety.checkpoint(self.net,fn)
            if available():
                # (as m2c reads it: what's only for C++ made pycparser refuse GTA San Andreas's whole header)
                header = seeds.c_only(self.net.forks.permuter_header(fork, fn["name"]) if fork else
                                      self.net._context(proj["id"], fn["unit"])["header"])
                family = weight_family(self.net.cfg(proj), fn["vram"], bool(fork), fn)
                try:
                    gen = Generator()
                except OSError as e:  # its environment is broken: the simple rewrites still work
                    log.info("permuter: decomp-permuter didn't start: %s", e)
            # (a draft still in m2c's macros isn't C decomp-permuter's parser reads: written out, it is the same code)
            start_src = best_src = cur_src = m2cfix.expand_macros(fn["best_source"], header)
            best = cur = fn["best_score"]
            deadline = time.monotonic() + seconds
            from . import adaptive_repairs, permuter_focus, repair_ranking
            if hasattr(self.net, 'keep_diff'):
                measured, _ = self._baseline(agent, proj, fn, st, fork)
                if measured == 0:
                    st.add(improved=1, matched=1)
                    return
            if hasattr(self.net, 'keep_diff'):
                best, best_src = self._local_order(agent, proj, fn, st, fork, deadline, best, best_src)
                cur, cur_src = best, best_src
                if best == 0:
                    return
            rows = repair_ranking.evidence(self.net, fn['id'], agent.id)
            focus = permuter_focus.classify(rows)
            boosts = adaptive_repairs.boosts(self.net, proj, fn, rows, permuter_focus.boosts(rows))
            from .search_frontier import Frontier
            frontier = self.search_frontier = Frontier()
            frontier.offer(best_src, best, rows, arch=proj['arch'])
            batches = misses = 0
            while time.monotonic() < deadline and best > 0 and not st.stop.is_set():
                if gen is not None:
                    # a batch of decomp-permuter's variants, of the C the turn started from and of the best so far in
                    # turn: from the best alone, rewrites piled on rewrites and got stuck on one of nine near misses of
                    # the demo ROM (at 45); taking turns, every one matched within 12 s
                    # A freshly saved winner takes the next batch. Other batches rotate the
                    # original, numeric best and structurally promising alternatives.
                    fresh = self.net._function(fn['name'], proj['slug']) if hasattr(self.net, '_function') else fn
                    if fresh['status'] == 'matched':
                        break
                    if fresh['best_source'] and fresh['best_score'] is not None and fresh['best_score'] < best:
                        fn = fresh
                        fresh_proj = self.net.project_row(proj['slug'])
                        fresh_header = seeds.c_only(self.net.forks.permuter_header(fork, fn['name']) if fork else
                                                    self.net._context(proj['id'], fn['unit'])['header'])
                        if fresh_header != header or fresh_proj['config'] != proj['config']:
                            frontier = self.search_frontier = Frontier()
                        proj, header = fresh_proj, fresh_header
                        family = weight_family(self.net.cfg(proj), fn['vram'], bool(fork), fn)
                        best, _ = self._baseline(agent, proj, fn, st, fork)
                        if best is None or best == 0:
                            break
                        best_src = m2cfix.expand_macros(fn['best_source'], header)
                        rows = repair_ranking.evidence(self.net, fn['id'], agent.id)
                        frontier.offer(best_src, best, rows, arch=proj['arch'])
                        reseed = True
                    else:
                        reseed = getattr(self, '_reseed_random', False)
                    self._reseed_random = False
                    base = (best_src if reseed else frontier.choose(batches//3, best_src) if batches % 3 == 2
                            else best_src if batches % 2 else start_src)
                    batches += 1
                    if batches % 2 == 0:
                        boosts = adaptive_repairs.boosts(self.net, proj, fn, rows, permuter_focus.boosts(rows))
                    # Alternate upstream defaults with focused weights: preserve an exploratory lane when
                    # the advisory mismatch category is incomplete or points at the wrong repair.
                    job = {"header": header, "source": base, "function": fn["name"], "family": family,
                           "seed": rng.randrange(MAX_SEED), "count": BATCH, "weight_boosts": boosts if batches % 2 == 0 else {},
                           "trace": True}
                    if hasattr(gen, 'chunks'):
                        if gen._closed.is_set():gen = Generator()
                        def check_generation(fn=fn):
                            random_safety.checkpoint(self.net, fn)
                            if st.stop.is_set() or time.monotonic() >= deadline:
                                raise GenerationStopped()
                        batch = self._stream_variants(gen.chunks(job, check_generation), agent, proj, fn, fork)
                    else:
                        batch = gen.batch(job)
                    if batch is None:  # C it can't read: the simple rewrites can still try
                        gen.close()
                        gen = None
                        continue
                    if not batch:  # nothing new left to write from this C
                        break
                else:  # one simple rewrite, mostly of the last C kept
                    base = cur_src if rng.random() < 0.85 else best_src
                    batch = [mutate(base, rng, focus)]
                    # none of them applies to this C (lerp's `a + (b - a) * t`): a minute of trying burnt a core
                    misses = 0 if batch[0] else misses + 1
                    if misses >= REWRITE_MISSES:
                        break
                for cand in batch:
                    if cand is None or time.monotonic() >= deadline or st.stop.is_set():
                        break
                    if random_safety.unsafe_update(fn['best_source'], cand):
                        continue  # reject introduced update chains or empty constant-loop scaffolding
                    lineage = getattr(cand, 'lineage', None)
                    cand = m2cfix.static_helpers(cand)  # (a global helper would not link: m2cfix.py)
                    st.add(variants=1)
                    provenance = {'kind': 'random rewrite', 'family': lineage[-1] if lineage else 'random_rewrite_unattributed',
                                  'lineage': lineage or [], 'base_source_sha256': hashlib.sha256(base.encode()).hexdigest(),
                                  'source_sha256': hashlib.sha256(cand.encode()).hexdigest()}
                    started = time.monotonic()
                    with repair_trial(provenance):
                        score = self._score(agent, proj, fn, cand, best, fork)
                    if not getattr(self, 'trace_variants', False) and getattr(self, '_trial_outcome', '') != 'no_edit':
                        adaptive_repairs.record(self.net, adaptive_repairs.profile(self.net, proj, fn), focus,
                                                provenance['family'], best, score, (time.monotonic()-started)*1000, origin="background random")
                    if score is None:
                        continue
                    measured_rows = repair_ranking.evidence(self.net, fn['id'], agent.id)
                    frontier.offer(cand, score, measured_rows, arch=proj['arch'], baseline=rows, baseline_score=best)
                    if score < best:
                        from .deep_compilation import discard
                        discard(self)
                        best, best_src = score, cand
                        st.add(improved=1)
                        if best > 0:
                            best, best_src = self._after_improvement(agent, proj, fn, st, fork, best, best_src)
                        rows = repair_ranking.evidence(self.net, fn['id'], agent.id)
                        focus = permuter_focus.classify(rows)
                        boosts = adaptive_repairs.boosts(self.net, proj, fn, rows, permuter_focus.boosts(rows))
                        if best == 0:
                            st.add(matched=1)
                            log.info("permuter: %s matched", fn["name"])
                            break
                        cur, cur_src = best, best_src
                        self._reseed_random = True
                        break  # discard siblings generated from an obsolete parent after every improvement
                    if score <= cur or rng.random() < 0.02:  # mostly downhill, now and then sideways off a plateau
                        cur, cur_src = score, cand
                if hasattr(batch, 'close'):batch.close()
                if gen is not None and getattr(gen, 'failed', False):
                    gen.close();gen = None
                elif gen is not None and getattr(gen, 'empty', False):break
        except GenerationStopped:
            pass
        finally:
            if gen is not None:
                gen.close()
            st.holding(fn["name"], False)
            self._let_go(agent, fn)

    def _stream_variants(self, chunks, agent, proj, fn, fork):
        try:
            for chunk in chunks:
                sources = [m2cfix.static_helpers(cand) for cand in chunk]
                if hasattr(self, '_prime_trials'):
                    self._prime_trials(agent, proj, fn, sources, fork)
                from .deep_compilation import chunk as compile_chunk
                with compile_chunk(self, agent, proj, fn, sources, fork):yield from chunk
        finally:
            chunks.close()

    def _repair_flags(self, agent, proj, fn, st, deadline):
        """Two cached compiler-flag probes per pass on close ROM misses; persist only an exact function range.

        Imported projects own their build flags: never rewrite their build setup from this phase.
        """
        if not hasattr(self.net, '_judge_with'):
            return 0
        current = self.net.project_store(proj['id']).one("SELECT * FROM functions WHERE id=?", fn['id'])
        if current['status'] != 'open' or not current['best_score'] or current['best_score'] > current['n_insns'] * 2:
            return 0
        own = compiler_for(self.net.cfg(proj), fn['vram'], fn)
        ctx = self.net._context(proj['id'], fn['unit'])
        count = 0
        symbols = [tuple(row) for row in self.net.project_store(proj['id']).all('SELECT name,address FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
        choices = [f for f in self.net.debug_flag_variants(own['toolchain'], list(own['flags'])) if f != own['flags']]
        if hasattr(self.net, 'flag_variants'):
            choices += [f for f in self.net.flag_variants(own['toolchain'], list(own['flags']))
                        if f != own['flags'] and f not in choices]
        for flags in choices:
            if count >= 2 or st.stop.is_set() or time.monotonic() >= deadline:
                break
            digest = hashlib.sha256(repr((current['target'], current['relocs'], current['best_source'],
                                          ctx['header'], self.net.cfg(proj), symbols, flags)).encode()).hexdigest()
            key = 'draft-flags-v2:' + digest
            if self.net.project_store(proj['id']).one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], key):
                continue
            count += 1
            st.add(variants=1, repairs=1)
            exact, _, _ = self.net._judge_with(proj, current, own['toolchain'], flags, current['best_source'])
            with self.net.project_store(proj['id']).tx() as db:
                db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                           (fn['id'], key, 'match' if exact else 'miss', 'near-miss compiler flag probe'))
            if exact:
                if hasattr(self.net, 'auto_adjusts') and not self.net.auto_adjusts(self.net.project_row(proj['slug'])):
                    self.net.suggest(agent, proj['slug'], 'The permuter found an exact match with these compiler flags.',
                                     compiler=own['toolchain'], flags=' '.join(flags), function=current['name'],
                                     ranges=f"0x{fn['vram']:X}-0x{fn['vram']+fn['size']:X}", source=current['best_source'])
                    break  # leave the proven change for the user, as their project setting requests
                # Range changes still protect any affected existing match before saving.
                self.net.set_compiler_ranges(proj['slug'], [[fn['vram'], fn['vram'] + fn['size']]], own['toolchain'], flags)
                fresh = self.net.project_row(proj['slug'])
                count += 1
                st.add(variants=1, repairs=1)
                if self._score(agent, fresh, current, current['best_source'], current['best_score'], None) == 0:
                    st.add(improved=1, matched=1)
                break
        return count

    def _local_order(self, agent, proj, fn, st, fork, deadline, best, best_src):
        """Eight cached cheap hypotheses, inside the turn's existing time/claim allowance."""
        from . import permuter_inputs, permuter_local, source_preparation
        state = (st.started_at, proj['config'], self.net.forks.permuter_header(fork, fn['name']),
                 permuter_inputs.build_state(proj)) if fork else source_preparation.inputs(self.net, proj, fn)
        from decomprepair import independent_address_repairs

        from . import pointer_index_scope, repair_ranking
        rows=repair_ranking.evidence(self.net,fn['id'],agent.id)
        header=(self.net._context(proj['id'],fn['unit']) or {'header':''})['header'] if not fork else ''
        independent=independent_address_repairs.variants(best_src,fn['name'],proj['arch'],rows,header) if not fork else []
        # Both pools share the existing eight-compile allowance, deadline and exact judge.
        proposals=(independent+pointer_index_scope.variants(best_src,fn['name'],header)+permuter_local.variants(fn['best_source'],fn['name']))[:8]
        for family, candidate in proposals:
            if st.stop.is_set() or time.monotonic() >= deadline:
                break
            key = 'local-order-v1:' + hashlib.sha256(repr((state, candidate)).encode()).hexdigest()
            if self.net.project_store(proj['id']).one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], key):
                continue
            st.add(variants=1, repairs=1)
            with repair_trial({'kind': 'local order before permutation', 'family': family}):
                score = self._score(agent, proj, fn, candidate, best, fork)
            with self.net.project_store(proj['id']).tx() as db:
                db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                           (fn['id'], key, str(score), family))
            if score is not None and score < best:
                best, best_src = score, candidate
                st.add(improved=1)
                if score == 0:
                    st.add(matched=1)
                    break
        return best, best_src

    def _baseline(self, agent, proj, fn, st, fork):
        """Fresh evidence once per exact input in this process; a bounded cache avoids repeated baseline compiles."""
        from . import permuter_inputs, source_preparation
        header = self.net.forks.permuter_header(fork, fn['name']) if fork else self.net._context(proj['id'], fn['unit'])['header']
        state = (st.started_at, header, proj['config'], fn['best_source'], fn['target'], permuter_inputs.build_state(proj)) if fork else source_preparation.inputs(self.net, proj, fn)
        key = (fn['id'], hashlib.sha256(repr(state).encode()).hexdigest())
        cache = getattr(self, '_baselines', {})
        self._baselines = cache
        if key in cache:
            score, rows = cache[key]
            self.net.keep_diff(agent.id, fn['id'], rows)
            if not hasattr(self.net, '_agent_outcomes'):
                self.net._agent_outcomes = {}
            self.net._agent_outcomes[agent.id, fn['id']] = {'source': fn['best_source'], 'score': score, 'rows': rows}
            return score, 0
        st.add(variants=1, repairs=1)
        # Clear old evidence so a compile/link error cannot leave a misleading diff as today's baseline.
        getattr(self.net, '_last_diffs', {}).pop((agent.id, fn['id']), None)
        score = self._score(agent, proj, fn, fn['best_source'], fn['best_score'], fork)
        rows = getattr(self.net, '_last_diffs', {}).get((agent.id, fn['id']), ([], ''))[0] if score is not None else []
        self.net.keep_diff(agent.id, fn['id'], rows)
        if not hasattr(self.net, '_agent_outcomes'):
            self.net._agent_outcomes = {}
        self.net._agent_outcomes[agent.id, fn['id']] = {'source': fn['best_source'], 'score': score, 'rows': rows}
        cache[key] = (score, rows)
        while len(cache) > 256:
            del cache[next(iter(cache))]
        return score, 1

    @trial
    def _score(self, agent, proj, fn, source: str, best: int, fork: str | None) -> int | None:
        """A variant's score; recorded (as the function's best attempt, or its match) only when it beats `best`."""
        # Search uses captured DB inputs; the active-project watcher imports/exports files.
        # Keep revision/recheck guards without two forced tree scans per candidate.
        with (self.net.progress.view() if hasattr(self.net, "progress") else nullcontext()), \
                (self.net.progress.judging(proj["slug"], synchronize=False) if hasattr(self.net, "progress") else nullcontext()):
            from .experiment_evidence import measured
            return measured(self, agent, proj, fn, source, best, fork)

    def _accept_candidate(self, agent, proj, fn, source, diff):
        """A deep room search shares/validates a winning candidate before local acceptance."""

    def _score_work(self, agent, proj, fn, source: str, best: int, fork: str | None) -> int | None:
        self._trial_outcome = 'unscored'
        self._compact_trial = False
        self._trial_output = None
        self._trial_locations = None
        self._trial_output_granularity = None
        if fork:
            from ..inline_helpers import PREFIX as aggregate_helper_prefix
            if aggregate_helper_prefix in source:
                # Imported compile_source accepts one definition. Its helpers belong in the
                # real unit via edit_file; do not spend a build on an unsupported insertion.
                self._trial_outcome = 'unsupported helper placement'
                return None
            res = self.net.forks.attempt(agent, fork, fn["name"], source, keep=False, quiet=best,
                                         **({"background": True} if getattr(self, "parallel_claims", False) else {}))
            if res.get('score') is not None:
                self._trial_outcome = 'scored'
                measured=res.get('compiled_output') or {}
                self._trial_output=measured.get('fingerprint')
                self._trial_output_granularity=measured.get('granularity')
                self._trial_locations=res.get('repair_locations')
            return res.get("score")
        ctx = self.net._context(proj["id"], fn["unit"])
        from decomprepair.repair_preflight import invalid_call
        if source != fn['best_source'] and invalid_call(source, fn['name'], ctx['header']):
            return None
        guard = None
        if getattr(self, "parallel_claims", False):
            identity = tuple(fn[k] for k in ('name', 'vram', 'size', 'unit', 'target', 'relocs'))
            naming = json.loads(fn['meta'] or '{}').get('naming')
            def build_settings(project):
                cfg = json.loads(project['config'])
                return (compiler_for(cfg, fn['vram'], fn), project['root'], project['arch'], project['big_endian'],
                        tuple(cfg.get(k) for k in ('kind', 'platform', 'big_endian', 'gp')))
            build = build_settings(proj)
            generation = self.net._generation.get(proj['id'], 0)
            def guard():
                current = self.net.project_store(proj['id']).one('SELECT * FROM functions WHERE id=?', fn['id'])
                project = self.net.project_store(proj['id']).one('SELECT * FROM projects WHERE id=?', proj['id'])
                header = self.net._context(proj['id'], current['unit']) if current else None
                if not current or not project or current['status'] != 'open' or not header:
                    raise NetworkError('Search inputs changed while compiling; the function is no longer open.')
                changed = []
                if tuple(current[k] for k in ('name', 'vram', 'size', 'unit', 'target', 'relocs')) != identity or json.loads(current['meta'] or '{}').get('naming') != naming:
                    changed.append('function identity')
                if build_settings(project) != build:
                    changed.append('project settings')
                if header['version'] != ctx['version'] or header['header'] != ctx['header']:
                    changed.append('header')
                if self.net._generation.get(proj['id'], 0) != generation:
                    changed.append('symbols or layout')
                if changed:
                    raise NetworkError('Search inputs changed while compiling ('+', '.join(changed)+'); the saved result was left untouched. Retry the function.')
        compile_candidate = getattr(self.net, "_attempt_compilation", self.net._compile)
        result = compile_candidate(proj, ctx["header"], source, fn)
        if not result.ok:
            self._trial_outcome = 'compile_failed'
            if 'Compiler or build process exceeded' in getattr(result, 'log', ''):
                log.warning('%s: %s', fn['name'], result.log.strip())
            return None
        from .. import compile_timing
        from .engine.judging import search_evaluation
        with search_evaluation():
            cand, verdict, _ = self.net._evaluate(fn, proj, result.obj)
        if cand is None:
            return None
        self._trial_outcome = 'scored'
        from .experiment_evidence import output
        self._trial_output = output(cand)
        self._trial_output_granularity = 'function'
        from decomprepair.repair_localisation import bind

        from ..repair_feedback import current_trial
        from ..source_lines import mismatches
        lean = ((current_trial() or {}).get('kind') == 'random rewrite' and source != fn['best_source'])
        target = self.net._target_obj(fn, proj)
        # Use this actual compiled object; never recompile with different debug flags.
        with compile_timing.phase('score'):
            d = scoring.score(target, cand) if lean else scoring.diff(target, cand)
            if lean and (d.score < best or d.bytes_match or verdict is True):
                d = scoring.diff(target, cand)
        if verdict is not None:
            d = scoring.DiffResult(0 if verdict else max(1, d.score), d.max_score, d.rows, verdict)
        if d.score < best or d.bytes_match or source == fn['best_source']:
            # Winners and baselines feed subsequent local repairs and saved evidence.
            # Ordinary misses skip export/address reports; search feedback remains available.
            finish=getattr(self.net,'_complete_search_evaluation',self.net._evaluate)
            finish(fn,proj,result.obj)
        from ..repair_feedback import current_trial
        self._compact_trial = ((current_trial() or {}).get('kind') == 'random rewrite'
                               and source != fn['best_source'] and d.score >= best and not d.bytes_match)
        if not self._compact_trial:
            with compile_timing.phase('search_diagnostics'):
                self._trial_locations=bind(source,mismatches(result.obj,fn['name'],d.rows,source,
                    filename='func.cpp' if getattr(result,'language','c')=='c++' else 'func.c',arch=proj['arch'],
                    offsets=[ins.offset for ins in scoring.disassemble(cand, target.cpu)]))
        rows = (self.net._search_rows(fn,proj,result.obj,d.rows)
                if not self._compact_trial and hasattr(self.net,'_search_rows') else d.rows)
        self.net.keep_diff(agent.id, fn['id'], rows)
        if not hasattr(self.net, '_agent_outcomes'):
            self.net._agent_outcomes = {}
        self.net._agent_outcomes[agent.id, fn['id']] = {'source': source, 'score': d.score, 'rows': rows, 'repair_locations':self._trial_locations}
        if d.score < best or (d.bytes_match and fn['status'] != 'matched'):
            self._accept_candidate(agent, proj, fn, source, d)
            from .trial_bookkeeping import flush
            flush(self.net.project_store(proj['id']))
            self.net._record(fn, agent, d.score, source, ctx["version"], matched=d.bytes_match, via="permuter", effective_settings=getattr(result,"effective_settings",None),
                             **({"before_record": guard} if guard else {}))
        return 0 if d.bytes_match else max(1, d.score)
