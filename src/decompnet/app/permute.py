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

import hashlib
import json
import logging
import queue
import random
import subprocess
import threading
import time
from concurrent.futures import wait
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__, consoles, field_probes, matching_search, scoring, seeds, toolchains
from ..hostenv import NO_WINDOW
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
PROBE_VERSION = 1  # bump when the deterministic field search changes; old negative results are no longer evidence
PROBE_INSNS = 80  # bounded layout probes can rescue small initializers outside the usual 6% cutoff
MAX_CONTEXTS = 4
MAX_INSNS = 400  # bigger near misses are left to agents: one variant takes too long to judge
IDLE_SECONDS = 180  # with no near misses, how long until the permuter looks again
IDLE_LOOKS = 20  # looks in a row with nothing to do (an hour) and it's done until agents start again
HOLD_SPARE_SECONDS = 120  # a turn's hold on a function lasts this much longer than the turn, in case it runs over
_EOF = object()
# decomp-permuter's weights per compiler (its default_weights.toml), by the start of a toolchain's id
FAMILIES = (("ido", "ido"), ("gcc", "gcc"), ("ee-gcc", "gcc"), ("psp-gcc", "gcc"), ("ps3", "gcc"), ("agbcc", "gcc"),
            ("mwc", "mwcc"))


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
                                     stderr=subprocess.DEVNULL,
                                     creationflags=NO_WINDOW | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        # Windows select() accepts sockets only. A reader thread also gives pipe reads a deadline there.
        self._lines = queue.Queue()
        threading.Thread(target=self._read, daemon=True, name="permuter-output").start()

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:
                self._lines.put(line)
        finally:
            self._lines.put(_EOF)

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
                variants.append(out["variant"])
    def _line(self, deadline: float):
        """The generator's next line, or None when it has stopped or hasn't answered by `deadline`."""
        try:
            return self._lines.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            return None

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
            "AND best_score > 0 AND (best_score <= n_insns * 100 * ? OR "
            "(n_insns <= ? AND best_score <= n_insns * 20 AND attempts >= ?)) AND n_insns <= ? AND permuted < ? "
            "AND id NOT IN (SELECT function_id FROM claims) ORDER BY CAST(best_score AS REAL) / (n_insns * 100) LIMIT ?",
            proj["id"], NEAR, PROBE_INSNS, max(8, min(int(self.net.cfg(proj).get("matching_stall_budget", 24)), 256)),
            MAX_INSNS, MAX_TURNS, limit)

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
        except NetworkError as e:  # the project refused its source (a helper the file hasn't): an answer, not a crash
            log.info("permuter: %s: %s (left to agents)", fn["name"], str(e).splitlines()[0])
            with self.net.store.tx() as db:  # the same best attempt is refused every time: no more turns on it
                db.execute("UPDATE functions SET permuted=MAX(permuted, ?) WHERE id=?", (MAX_TURNS, fn["id"]))
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
            best_src = cur_src = fn["best_source"]
            best = cur = fn["best_score"]
            deadline = time.monotonic() + SECONDS
            prepared = self._prepare(agent, proj, fn, st, fork, deadline)
            if prepared is None:
                return
            contexts, best, best_src, order_best = prepared
            cur, cur_src = best, best_src
            batches = misses = empty = 0
            seen = set()
            while time.monotonic() < deadline and best > 0 and not st.stop.is_set():
                if gen is not None:
                    # a batch of decomp-permuter's variants, of the C the turn started from and of the best so far in
                    # turn: from the best alone, rewrites piled on rewrites and got stuck on one of nine near misses of
                    # the demo ROM (at 45); taking turns, every one matched within 12 s
                    base = best_src if batches % 2 else contexts[(batches // 2) % len(contexts)]
                    batches += 1
                    batch = gen.batch({"header": header, "source": base, "function": fn["name"], "family": family,
                                       "seed": rng.randrange(MAX_SEED), "count": BATCH})
                    if batch is None:  # C it can't read: the simple rewrites can still try
                        gen.close()
                        gen = None
                        continue
                    empty = empty + 1 if not batch else 0
                    if empty >= 2 * len(contexts):
                        break  # every retained context is exhausted, not just the scalar best
                else:  # one simple rewrite, mostly of the last C kept
                    base = contexts[(batches // 4) % len(contexts)] if batches % 4 == 0 else cur_src
                    batches += 1
                    batch = [mutate(base, rng)]
                    # none of them applies to this C (lerp's `a + (b - a) * t`): a minute of trying burnt a core
                    misses = 0 if batch[0] else misses + 1
                    if misses >= REWRITE_MISSES:
                        break
                for cand in batch:
                    if cand is None or time.monotonic() >= deadline or st.stop.is_set():
                        break
                    if cand in seen:
                        continue
                    seen.add(cand)
                    st.add(variants=1)
                    measured = self._measure(agent, proj, fn, cand, best, fork)
                    if measured is None:
                        continue
                    score, order = measured
                    if order is not None and (order_best is None or order < order_best):
                        order_best = order
                        if cand not in contexts:
                            contexts = [contexts[0], *contexts[1:MAX_CONTEXTS - 1], cand]
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

    def _prepare(self, agent, proj, fn, st, fork, deadline):
        """Reproduce the historical best before spending a turn, then try bounded layout hypotheses.

        PROJECT builds keep their build system and objdiff; the ROM-only field probes need known 32-bit layouts
        and standalone declarations. A mismatching baseline stops this turn, rather than optimizing a stale score.
        """
        source, best = fn["best_source"], fn["best_score"]
        if fork:
            return [source], best, source, None
        baseline = self._measure(agent, proj, fn, source, best, fork)
        if baseline and baseline[0] == 0:
            st.add(matched=1, improved=1)
            return None
        ctx = self.net._context(proj["id"], fn["unit"])
        cfg = self.net.cfg(proj)
        own = compiler_for(cfg, fn["vram"])
        # Symbol addresses, helper targets, header, target and compiler changes invalidate both failures and hits.
        symbols = [tuple(r) for r in self.net.store.all(
            "SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name,address", proj["id"])]
        functions = [tuple(r) for r in self.net.store.all(
            "SELECT name,vram,size FROM functions WHERE project_id=? ORDER BY id", proj["id"])]
        key = hashlib.sha256(json.dumps([PROBE_VERSION, __version__, proj["arch"], proj["big_endian"], cfg.get("platform"),
                                        source, ctx["header"], own, cfg.get("gp"), fn["relocs"],
                                        symbols, functions, bytes(fn["target"] or b"").hex()], sort_keys=True).encode()).hexdigest()
        if baseline is None or baseline[0] != best:
            message = (f"Saved best score {best} does not reproduce under the active compiler "
                       f"(now {baseline[0] if baseline else 'compile/link failure'}). "
                       "Random permutations paused; check compiler provenance and header changes first.")
            self._save_probe(fn, key, {"diagnostic": message, "contexts": [], "complete": False})
            log.info("permuter: %s: %s", fn['name'], message)
            return None
        cached = self.net.store.one("SELECT result FROM matching_probes WHERE function_id=? AND cache_key=?",
                                    fn["id"], key)
        if cached and json.loads(cached['result']).get('complete'):
            kept = json.loads(cached['result']).get('contexts', [])
            return [source, *kept][:MAX_CONTEXTS], best, source, baseline[1]
        variants = (field_probes.candidates(source, fn['name'], ctx['header'])
                    if proj['arch'] in ('mips', 'arm', 'ppc', 'x86') and fn['n_insns'] <= PROBE_INSNS else [])
        ranked, complete = [], True
        best_source = source
        for variant in variants:
            if st.stop.is_set() or time.monotonic() >= deadline:
                complete = False
                break
            st.add(variants=1)
            measured = self._measure(agent, proj, fn, variant, best, fork)
            if measured is None:
                complete = False  # compile failures may be transient; never persist them as negative evidence
                continue
            score, order = measured
            ranked.append((order, score, variant))
            if score < best:
                best, best_source = score, variant
                st.add(improved=1)
            if score == 0:
                st.add(matched=1)
                return None
        kept = [entry[2] for entry in sorted(ranked)[:MAX_CONTEXTS - 1]]
        if complete:
            self._save_probe(fn, key, {"diagnostic": f"Tested {len(variants)} field layouts under the active compiler; "
                                      "none matched exactly.", "contexts": kept, "complete": True})
        distances = [v for v in [baseline[1], *[entry[0] for entry in ranked]] if v is not None]
        order = min(distances) if distances else None
        return [source, *kept], best, best_source, order

    def _save_probe(self, fn, key, result):
        with self.net.store.tx() as db:
            db.execute("INSERT OR REPLACE INTO matching_probes VALUES(?,?,?,?)",
                       (fn['id'], key, json.dumps(result), time.time()))
            db.execute("DELETE FROM matching_probes WHERE function_id=? AND cache_key NOT IN "
                       "(SELECT cache_key FROM matching_probes WHERE function_id=? ORDER BY created_at DESC LIMIT 8)",
                       (fn['id'], fn['id']))

    def _score(self, agent, proj, fn, source: str, best: int, fork: str | None) -> int | None:
        measured = self._measure(agent, proj, fn, source, best, fork)
        return measured[0] if measured is not None else None

    def _measure(self, agent, proj, fn, source: str, best: int, fork: str | None):
        """Normal judge score plus a search-only ordering metric; only the normal judge may record a match."""
        with self.net.progress.judging(proj["slug"]):
            if fork:
                res = self.net.forks.attempt(agent, fork, fn["name"], source, keep=False, quiet=best)
                return (res['score'], None) if res.get('score') is not None else None
            ctx = self.net._context(proj["id"], fn["unit"])
            result = self.net._compile(proj, ctx["header"], source, fn)
            if not result.ok:
                return None
            cand, verdict, problems = self.net._evaluate(fn, proj, result.obj)
            if cand is None or problems:
                return None
            target = self.net._target_obj(fn, proj)
            d = scoring.diff(target, cand)
            if verdict is not None:
                d = scoring.DiffResult(0 if verdict else max(1, d.score), d.max_score, d.rows, verdict)
            if d.score < best:
                cfg = self.net.cfg(proj)
                provenance = compiler_for(cfg, fn["vram"]) if cfg.get("compiler") else None
                self.net._record(fn, agent, d.score, source, ctx["version"], matched=d.bytes_match, via="permuter",
                                 compiler=provenance)
            order = None if fn["n_insns"] > PROBE_INSNS else matching_search.instruction_distance(
                [i.text for i in scoring.disassemble(target)],
                [i.text for i in scoring.disassemble(cand, target.cpu)])
            return (0 if d.bytes_match else max(1, d.score)), order
