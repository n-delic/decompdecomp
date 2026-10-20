"""The permuter, in the background: the decomp community's way through the last few percent.

A function at 95% usually differs in register allocation or instruction order, which comes down to how the C
is written, not what it does: the order of statements and declarations, a temporary, a cast, `x += 1` for
`x++`, an if turned around. Agents doing it by hand spend turns (and tokens) on it, and stall. Here it runs on its
own, on spare CPU, on the near misses agents left (or autopilot's drafts reached): a worker holds a function for a
turn of a minute, so an agent that wants it gets it next, records every improvement as the function's best attempt
(agents start from it), and a match as a match. What matches is still judged byte for byte, like everything else.

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

import json
import logging
import os
import random
import select
import subprocess
import threading
import time
from concurrent.futures import wait
from dataclasses import dataclass, field
from pathlib import Path

from .. import consoles, scoring, seeds, toolchains
from ..network import NetworkError
from ..permuter import mutate
from ..project import compiler_for
from . import workers

log = logging.getLogger("decompdecomp")
NEAR = 0.06  # a near miss: at most this much of the function differs (best_score <= 6% of n_insns * 100)
SECONDS = 60  # per function per turn
MAX_TURNS = 4  # turns per function before it's left to agents (counted in the database: functions.permuted)
CANDIDATES = 20  # near misses looked up at a time, for the workers to share
TOOL = "decomp-permuter"
GENERATOR = Path(__file__).resolve().parent.parent / "permuter_gen.py"
BATCH = 64  # variants per run of the generator: about 0.15 s to write, a few seconds to judge
GENERATOR_SECONDS = 30  # a batch that takes longer is stuck: its randomizer retries until a rewrite applies
READ_BYTES = 1 << 16
MAX_SEED = 2**31  # seeds of the generator's runs: the same function and turn write the same variants
REWRITE_MISSES = 200  # simple rewrites in a row that change nothing: none applies to the C
MAX_INSNS = 400  # bigger near misses are left to agents: one variant takes too long to judge
IDLE_SECONDS = 180  # with no near misses, how long until the permuter looks again
IDLE_LOOKS = 20  # looks in a row with nothing to do (an hour) and it's done until agents start again
HOLD_SPARE_SECONDS = 120  # a turn's hold on a function lasts this much longer than the turn, in case it runs over
# decomp-permuter's weights per compiler (its default_weights.toml), by the start of a toolchain's id
FAMILIES = (("ido", "ido"), ("gcc", "gcc"), ("ee-gcc", "gcc"), ("mwc", "mwcc"))


def weight_family(cfg: dict, vram: int | None, fork: bool) -> str:
    """Which of decomp-permuter's weight sets suits the compiler that builds the code at `vram`."""
    if fork:  # forks name their console, not a toolchain: the console says its compilers' family
        return consoles.get(cfg.get("platform")).compiler_family or "base"
    toolchain = (compiler_for(cfg, vram).get("toolchain") or "") if cfg.get("compiler") else ""
    return next((family for prefix, family in FAMILIES if toolchain.startswith(prefix)), "base")


def available() -> bool:
    return toolchains.installed(TOOL) is not None


def generator_argv() -> list[str]:
    tool = toolchains.path(TOOL)
    return [str(toolchains.venv_bin(tool / "venv", "python")), "-I", str(GENERATOR), str(tool)]


class Generator:
    """The generator (permuter_gen.py) for one turn, in decomp-permuter's environment. It stays up for the turn, so
    it parses the header once per C it starts from: that takes a second for a header of 300 KB (debug symbols give a
    game thousands of types), where writing a batch of 64 variants takes 0.1 s."""

    def __init__(self, argv: list[str] | None = None):
        self.proc = subprocess.Popen(argv or generator_argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        self._pending = b""

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
        while (line := self._line(deadline)) is not None:
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
                variants.append(out["variant"])
        log.info("permuter: the generator stopped or hung on %s", job["function"])
        self.close()
        return None

    def _line(self, deadline: float) -> bytes | None:
        """The generator's next line, or None when it has stopped or hasn't answered by `deadline`."""
        while b"\n" not in self._pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                return None
            chunk = os.read(self.proc.stdout.fileno(), READ_BYTES)
            if not chunk:
                return None
            self._pending += chunk
        line, self._pending = self._pending.split(b"\n", 1)
        return line

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


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
        self.jobs: dict[str, State] = {}

    def status(self, slug: str) -> dict | None:
        st = self.jobs.get(slug)
        return None if st is None else {k: v for k, v in st.__dict__.items() if k != "stop" and not k.startswith("_")}

    def start(self, slug: str) -> State | None:
        """Run on the project's near misses until there are none (looking again every few minutes while the
        app runs: agents make new ones)."""
        if (st := self.jobs.get(slug)) is not None and st.state == "running":
            return st
        if slug in self.net.links:  # a room member: the host's copy permutes, and its matches come back here
            return None
        st = self.jobs[slug] = State()
        threading.Thread(target=self._loop, args=(slug, st), daemon=True, name=f"permuter-{slug}").start()
        return st

    def stop(self, slug: str) -> None:
        if (st := self.jobs.get(slug)) is not None:
            st.stop.set()

    def candidates(self, proj, limit: int = CANDIDATES) -> list:
        return self.net.store.all(
            "SELECT * FROM functions WHERE project_id=? AND status='open' AND best_source IS NOT NULL "
            "AND best_score > 0 AND best_score <= n_insns * 100 * ? AND n_insns <= ? AND permuted < ? "
            "AND id NOT IN (SELECT function_id FROM claims) ORDER BY CAST(best_score AS REAL) / (n_insns * 100) LIMIT ?",
            proj["id"], NEAR, MAX_INSNS, MAX_TURNS, limit)

    def _loop(self, slug: str, st: State) -> None:
        agent, _ = self.net.local_agent("permuter")
        idle = 0
        try:
            while not st.stop.is_set():
                try:
                    proj = self.net.project_row(slug)
                except NetworkError:
                    return  # the project is gone
                todo = self.candidates(proj)
                if not todo:
                    idle += 1
                    if idle > IDLE_LOOKS:
                        return
                    st.stop.wait(IDLE_SECONDS)
                    continue
                idle = 0
                wait([workers.pool().submit(self._turn, agent, proj, fn, st) for fn in todo])
        finally:
            st.state = "stopped" if st.stop.is_set() else "done"

    def _turn(self, agent, proj, fn, st: State) -> None:
        try:
            self._permute(agent, proj, fn, st)
        except Exception as e:  # one odd function must not stop the rest
            log.info("permuter: %s: %s", fn["name"], e, exc_info=True)

    def _hold(self, agent, fn) -> bool:
        now = time.time()
        with self.net.store.tx() as db:
            self.net._expire_claims(db)
            if db.execute("SELECT 1 FROM claims WHERE function_id=?", (fn["id"],)).fetchone():
                return False
            db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + SECONDS + HOLD_SPARE_SECONDS))
            db.execute("UPDATE functions SET permuted = permuted + 1 WHERE id=?", (fn["id"],))
        return True

    def _let_go(self, agent, fn) -> None:
        with self.net.store.tx() as db:
            db.execute("DELETE FROM claims WHERE function_id=? AND agent_id=?", (fn["id"], agent.id))

    def _permute(self, agent, proj, fn, st: State) -> None:
        if st.stop.is_set() or not self._hold(agent, fn):
            return
        st.add(tried=1)
        st.holding(fn["name"], True)
        fork = self.net.fork_slug(proj["slug"])
        rng = random.Random(f"{fn['id']}-{fn['permuted']}")
        gen = None
        try:
            if available():
                # (as m2c reads it: what's only for C++ made pycparser refuse GTA San Andreas's whole header)
                header = seeds.c_only(self.net.forks.permuter_header(fork, fn["name"]) if fork else
                                      self.net._context(proj["id"], fn["unit"])["header"])
                family = weight_family(self.net.cfg(proj), fn["vram"], bool(fork))
                try:
                    gen = Generator()
                except OSError as e:  # its environment is broken: the simple rewrites still work
                    log.info("permuter: decomp-permuter didn't start: %s", e)
            start_src = best_src = cur_src = fn["best_source"]
            best = cur = fn["best_score"]
            deadline = time.monotonic() + SECONDS
            batches = misses = 0
            while time.monotonic() < deadline and best > 0 and not st.stop.is_set():
                if gen is not None:
                    # a batch of decomp-permuter's variants, of the C the turn started from and of the best so far in
                    # turn: from the best alone, rewrites piled on rewrites and got stuck on one of nine near misses of
                    # the demo ROM (at 45); taking turns, every one matched within 12 s
                    base = best_src if batches % 2 else start_src
                    batches += 1
                    batch = gen.batch({"header": header, "source": base, "function": fn["name"], "family": family,
                                       "seed": rng.randrange(MAX_SEED), "count": BATCH})
                    if batch is None:  # C it can't read: the simple rewrites can still try
                        gen.close()
                        gen = None
                        continue
                    if not batch:  # nothing new left to write from this C
                        break
                else:  # one simple rewrite, mostly of the last C kept
                    batch = [mutate(cur_src if rng.random() < 0.85 else best_src, rng)]
                    # none of them applies to this C (lerp's `a + (b - a) * t`): a minute of trying burnt a core
                    misses = 0 if batch[0] else misses + 1
                    if misses >= REWRITE_MISSES:
                        break
                for cand in batch:
                    if cand is None or time.monotonic() >= deadline or st.stop.is_set():
                        break
                    st.add(variants=1)
                    score = self._score(agent, proj, fn, cand, best, fork)
                    if score is None:
                        continue
                    if score < best:
                        best, best_src = score, cand
                        st.add(improved=1)
                        if score == 0:
                            st.add(matched=1)
                            log.info("permuter: %s matched", fn["name"])
                            break
                    if score <= cur or rng.random() < 0.02:  # mostly downhill, now and then sideways off a plateau
                        cur, cur_src = score, cand
        finally:
            if gen is not None:
                gen.close()
            st.holding(fn["name"], False)
            self._let_go(agent, fn)

    def _score(self, agent, proj, fn, source: str, best: int, fork: str | None) -> int | None:
        """A variant's score; recorded (as the function's best attempt, or its match) only when it beats `best`."""
        if fork:
            res = self.net.forks.attempt(agent, fork, fn["name"], source, keep=False, quiet=best)
            return res.get("score")
        ctx = self.net._context(proj["id"], fn["unit"])
        result = self.net._compile(proj, ctx["header"], source, fn)
        if not result.ok:
            return None
        cand, verdict, _ = self.net._evaluate(fn, proj, result.obj)
        if cand is None:
            return None
        d = scoring.diff(self.net._target_obj(fn, proj), cand)
        if verdict is not None:
            d = scoring.DiffResult(0 if verdict else max(1, d.score), d.max_score, d.rows, verdict)
        if d.score < best:
            self.net._record(fn, agent, d.score, source, ctx["version"], matched=d.bytes_match, via="permuter")
        return 0 if d.bytes_match else max(1, d.score)
