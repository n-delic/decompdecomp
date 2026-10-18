"""Range checks: a suggested compiler range is tested on its neighbours before it applies. The verdict logic on its
own, then the whole check against a small stand-in for the engine whose judge is scripted (no compilers needed)."""

import contextlib
import json
import struct

from decompnet.app.rangecheck import RangeChecks, Step, evaluate

GCC = ("ee-gcc-2.95.3", ["-O2", "-G8"])
CW = ("mwcps2-3.0.1", ["-O3", "-sdatathreshold", "8"])


def steps(*verdicts, start=0x2000, step=0x100):
    return [Step(start + i * step, start + (i + 1) * step, v) for i, v in enumerate(verdicts)]


ORIGIN = Step(0x1000, 0x1100, "origin")


def test_a_second_function_confirms_and_the_range_covers_what_lies_between():
    left = [Step(0x0F00, 0x1000, "neutral"), Step(0x0E00, 0x0F00, "confirmed"), Step(0x0D00, 0x0E00, "refuted")]
    status, start, end, nxt = evaluate(left, ORIGIN, steps("unknown"))
    assert (status, start, end, nxt) == ("confirmed", 0x0E00, 0x1100, None)


def test_refuted_neighbours_on_both_sides_make_it_a_quirk():
    left = [Step(0x0F00, 0x1000, "neutral"), Step(0x0E00, 0x0F00, "refuted")]
    assert evaluate(left, ORIGIN, steps("refuted"))[0] == "quirk"


def test_a_refuted_neighbour_further_out_is_not_a_wall():
    # the nearest testable neighbour on the right is unknown: the check goes on with it
    status, _, _, nxt = evaluate([Step(0x0F00, 0x1000, "refuted")], ORIGIN, steps("unknown", "refuted"))
    assert status == "checking" and nxt.vram == 0x2000


def test_the_nearest_unknown_neighbour_is_next():
    status, start, end, nxt = evaluate([Step(0x0F00, 0x1000, "unknown")], ORIGIN, steps("neutral", "unknown"))
    assert status == "checking" and (start, end) == (0x1000, 0x1100) and nxt.vram == 0x0F00


# ---------------------------------------------------------------------------------------------- the whole check

class FakeNet:
    """What RangeChecks uses of the engine: a project's settings and functions, the judge (scripted: which compilers
    each function's C matches with), priorities, notes and events."""

    auto_compiler = True

    def __init__(self, functions, matches):
        self.proj = {"id": 1, "slug": "game", "config": json.dumps({
            "compiler": {"toolchain": GCC[0], "flags": GCC[1]}, "compiler_ranges": [], "big_endian": False})}
        self.rows = functions
        self.matches = matches  # name -> set of toolchains its C matches with
        self.priority, self.notes, self.events, self.applied = [], [], [], []
        self.sql, self.judged = [], []  # what was read, and what the judge was asked
        self.store = self
        self.rangechecks = RangeChecks(self)
        self.rangechecks.background = False

    # the store
    def all(self, sql, *args):
        self.sql.append(sql)
        return self.rows

    def one(self, sql, *args):
        self.sql.append(sql)
        return next((r for r in self.rows if r["id"] == args[0]), None)

    def tx(self):
        return contextlib.nullcontext(None)

    def _event(self, db, pid, agent, kind, detail):
        self.events.append(detail)

    # the engine
    def cfg(self, proj):
        return json.loads(self.proj["config"])

    def _project(self, slug):
        return self.proj

    def _update_cfg(self, slug, change):
        cfg = self.cfg(self.proj)
        change(cfg)
        self.proj["config"] = json.dumps(cfg)
        return cfg

    def _function(self, name, slug):
        return next(r for r in self.rows if r["name"] == name)

    def compiler_map(self, proj):
        return None

    def _source_of(self, fn):
        return fn["name"] if fn["name"] in self.matches else None

    def _judge_with(self, proj, fn, toolchain, flags, source):
        self.judged.append((fn["name"], toolchain))
        return toolchain in self.matches.get(fn["name"], set()), None, ""

    def _context(self, project_id, unit=""):
        return {"unit": "", "version": 1, "header": ""}

    def set_priority(self, slug, name, on=True):
        self.priority = [n for n in self.priority if n != name] + ([name] if on else [])

    def local_agent(self, name):
        return object(), ""

    def add_note(self, agent, slug, text, function=None):
        self.notes.append((function, text))

    def auto_adjusts(self, proj):
        return self.auto_compiler

    def _short(self, label):
        return label[0]

    def set_compiler_ranges(self, slug, ranges, toolchain, flags):
        self.applied.append((ranges, toolchain))


def functions(n):
    code = struct.pack("<20I", *[0] * 20)  # 20 instructions: long enough to test
    return [{"id": i + 1, "name": f"f{i}", "vram": 0x100000 + i * 0x80, "size": 0x50, "n_insns": 20, "status": "open",
             "matched_source": None, "target": code, "unit": ""} for i in range(n)]


def start(net, origin="f4"):
    s = {"id": 1}
    net._update_cfg("game", lambda cfg: cfg.setdefault("suggestions", []).append(
        {"id": 1, "status": "open", "ranges": [], "check": None}))
    fn = net._function(origin, "game")
    said = net.rangechecks.propose(net.proj, s, origin, CW[0], CW[1], [[fn["vram"], fn["vram"] + fn["size"]]])
    return said, net.cfg(net.proj)["range_checks"][-1]


def test_a_neighbour_matching_only_with_the_other_compiler_confirms_and_applies_it():
    net = FakeNet(functions(9), {"f4": {CW[0]}, "f5": {CW[0]}})
    said, check = start(net)
    assert "Range check #1" in said
    assert check["status"] == "confirmed" and check["range"] == [0x100200, 0x1002D0]
    assert net.applied == [([[0x100200, 0x1002D0]], CW[0])]
    assert net.cfg(net.proj)["suggestions"][0]["status"] == "applied"


def test_neighbours_built_by_the_games_compiler_on_both_sides_make_it_a_quirk():
    rows = functions(9)
    for r in rows:
        if r["name"] in ("f3", "f5"):
            r.update(status="matched", matched_source=r["name"])
    net = FakeNet(rows, {"f4": {CW[0]}, "f3": {GCC[0]}, "f5": {GCC[0]}})
    _, check = start(net)
    assert check["status"] == "quirk" and not net.applied
    assert net.cfg(net.proj)["suggestions"][0]["status"] == "dismissed"
    assert any(fn == "f4" and "Look for C that matches with the project's compiler" in t for fn, t in net.notes)


def test_c_that_matches_with_both_compilers_is_no_evidence():
    net = FakeNet(functions(9), {"f4": {CW[0], GCC[0]}})
    _, check = start(net)
    assert check["status"] == "quirk" and "matches the game with today's compiler too" in check["said"]


def test_without_evidence_the_nearest_neighbour_goes_to_an_agent_and_giving_up_moves_on():
    net = FakeNet(functions(9), {"f4": {CW[0]}})
    _, check = start(net)
    assert check["status"] == "checking" and net.priority == ["f3"]
    assert any(fn == "f3" and "try_compiler(\"f3\"" in t for fn, t in net.notes)
    # the agent gives up: inconclusive, the next neighbour (the other side) is tried
    net.rangechecks.gave_up(net.proj, net._function("f3", "game"))
    assert net.priority == ["f5"]
    # f5's agent finds C that matches only with CodeWarrior: confirmed
    net.matches["f5"] = {CW[0]}
    net.rangechecks.tried(net.proj, net._function("f5", "game"), CW[0], CW[1], "f5", True)
    check = net.cfg(net.proj)["range_checks"][-1]
    assert check["status"] == "confirmed" and net.priority == []


def test_a_recheck_reads_the_functions_once_and_never_their_assembly():
    """GTA San Andreas: each of its 62 open checks read every column of every function (73 MB of assembly), 17 s
    after every match. A run reads the functions' addresses once, and the judge's columns for the few it judges."""
    net = FakeNet(functions(9), {"f4": {CW[0]}})
    start(net)
    for origin in ("f1", "f7"):  # three open checks
        fn = net._function(origin, "game")
        net.rangechecks.propose(net.proj, {"id": 1}, origin, CW[0], CW[1], [[fn["vram"], fn["vram"] + fn["size"]]])
    net.sql.clear()
    net.rangechecks.recheck("game", background=False)
    assert sum(sql.lstrip().startswith("SELECT id, name, vram") for sql in net.sql) == 1
    assert not any("*" in sql or "asm" in sql for sql in net.sql)


def test_the_same_c_is_judged_once():
    net = FakeNet(functions(9), {"f4": {CW[0]}})
    start(net)
    asked = len(net.judged)
    net.rangechecks.recheck("game", background=False)  # nothing changed: the verdicts are remembered
    assert asked and len(net.judged) == asked


def test_a_check_with_nothing_new_is_not_written_again():
    """GTA San Andreas: its 60 unsettled audits wrote the game's 193 KB of settings again on every recheck."""
    net = FakeNet(functions(9), {})
    fn = net._function("f4", "game")
    net._update_cfg("game", lambda cfg: cfg.__setitem__("compiler_ranges", [
        {"start": fn["vram"], "end": fn["vram"] + fn["size"], "toolchain": CW[0], "flags": CW[1]}]))
    assert net.rangechecks.audit(net.proj) == 1
    writes, real = [], net._update_cfg
    net._update_cfg = lambda slug, change: writes.append(1) or real(slug, change)
    net.rangechecks.recheck("game", background=False)
    assert net.cfg(net.proj)["range_checks"][0]["status"] == "waiting" and not writes


def test_a_burst_of_rechecks_is_one_run(monkeypatch):
    """Matches come faster than a recheck runs: each one started its own run (92 queued at once on GTA San Andreas).
    Requests made close together wait for each other and run once."""
    import threading
    import time

    from decompnet.app import rangecheck

    monkeypatch.setattr(rangecheck, "RECHECK_DEBOUNCE_SECONDS", 0.05)
    net = FakeNet(functions(9), {"f4": {CW[0]}})
    start(net)
    net.rangechecks.background = True
    runs, done = [], threading.Event()
    real = net.rangechecks._run

    def run(slug, wanted):
        runs.append(set(wanted))
        real(slug, wanted)
        done.set()
    net.rangechecks._run = run
    for _ in range(20):
        net.rangechecks.recheck("game")
    net.rangechecks.recheck("game", 1)
    assert done.wait(5)
    time.sleep(0.2)
    assert runs == [{None, 1}]


def test_auto_adjust_is_a_setting_of_each_project(tmp_path):
    """Ridge Racer V's agents kept finding quirks while GTA San Andreas's ranges were real: one game wants proven
    suggestions to apply by themselves, another not. A project without its own setting keeps the app's older one."""
    from decompnet.app.engine import RomNetwork

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        for slug in ("rr5", "gta"):
            db.execute("INSERT INTO projects(slug, name, root, config) VALUES(?,?,?,?)",
                       (slug, slug, str(tmp_path), json.dumps({"kind": "rom", "compiler": {"toolchain": GCC[0], "flags": GCC[1]}})))
    net.auto_compiler = True  # the app's older, global setting
    assert net.auto_adjusts(net._project("rr5")) and net.auto_adjusts(net._project("gta"))
    net.set_auto_adjust("rr5", False)
    assert not net.auto_adjusts(net._project("rr5")) and net.auto_adjusts(net._project("gta"))
