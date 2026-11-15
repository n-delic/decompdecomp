"""A big ROM project's progress sync stays cheap when only a little changed (#101).

The project is made directly in the database, as tests/test_progress.py does, so no compiler or game is needed: what
is measured is the folder synchronization, not the judge. The fixture times its full first sync and the timing
assertions are fractions of that same-run reference, not absolute milliseconds on an unknown CI runner.
"""

import json
import time

import pytest

from decompnet.app import saves
from decompnet.app.engine import RomNetwork

FUNCTIONS = 5000
# Timing limits are fractions of the full first sync measured in the same run: absolute milliseconds depend on the
# runner's shared CPU and disk. The one absolute bound is only a catastrophic-regression guard.
NOOP_FRACTION = 50
FORCED_NOOP_FRACTION = 5
ONE_MATCH_FRACTION = 3
CATASTROPHIC_MILLISECONDS = 2000


def _milliseconds(call, repeat=1):
    best = None
    result = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = call()
        elapsed = (time.perf_counter() - start) * 1000
        best = elapsed if best is None else min(best, elapsed)
    return best, result


def _file_stats(folder):
    return {path.relative_to(folder).as_posix(): (path.stat().st_mtime_ns, path.stat().st_size)
            for path in folder.rglob("*") if path.is_file()}


@pytest.fixture
def project(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    cfg = {"kind": "rom", "platform": "n64", "rom": {"sha1": "demo"}, "instructions": "old"}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                         (str(tmp_path / "game"), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'',0,'')",
                   (pid,))
        rows = [(pid, f"f{index}", 0x1000 + index * 4, 4, 1, "matched", "",
                 f"int f{index}(void) {{ return {index}; }}", f"int f{index}(void) {{ return {index}; }}", 0, index)
                for index in range(FUNCTIONS)]
        db.executemany("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit,matched_source,"
                       "best_source,best_score,matched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.execute("UPDATE functions SET status='open',matched_source=NULL,best_source=?,best_score=50 "
                   "WHERE project_id=? AND name=?", (f"int f{FUNCTIONS - 1}(void) {{ return 1; }}", pid,
                                                     f"f{FUNCTIONS - 1}"))
    net.apply_remote_layout = lambda *args: None
    judged = []
    net.attempt = lambda agent, name, source, slug: judged.append((name, source)) or {"matched": False}
    start = time.perf_counter()
    net.progress.sync("game", force=True)  # the first save: a full render and publish of every file
    full_sync_ms = (time.perf_counter() - start) * 1000
    return net, saves.default_folder(net, "game"), pid, judged, full_sync_ms


def test_a_noop_check_and_a_forced_noop_are_cheap(project):
    net, _, _, _, full_sync_ms = project
    progress = net.progress
    timings = []
    for _ in range(3):
        progress.checked.pop("game", None)  # measure the cheap check, not the 0.25 s throttle
        elapsed, result = _milliseconds(lambda: net._project("game"))
        assert result["slug"] == "game"
        timings.append(elapsed)
    assert min(timings) < full_sync_ms / NOOP_FRACTION, \
        f"a non-forced no-op took {min(timings):.1f} ms; a full sync here takes {full_sync_ms:.0f} ms"
    elapsed, published = _milliseconds(lambda: progress.sync("game", force=True), repeat=3)
    assert published is False
    assert elapsed < full_sync_ms / FORCED_NOOP_FRACTION, \
        f"a forced no-op took {elapsed:.1f} ms; a full sync here takes {full_sync_ms:.0f} ms"


def test_one_match_changed_writes_its_file_and_the_manifest(project):
    net, folder, pid, _, full_sync_ms = project
    before = _file_stats(folder)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE project_id=? AND name='f0'",
                   ("int f0(void) { return 5000; }", pid))
    elapsed, published = _milliseconds(lambda: net.progress.sync("game", force=True))
    assert published is True
    assert elapsed < full_sync_ms / ONE_MATCH_FRACTION, \
        f"one changed match took {elapsed:.1f} ms; a full sync here takes {full_sync_ms:.0f} ms"
    assert elapsed < CATASTROPHIC_MILLISECONDS
    after = _file_stats(folder)
    written = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    assert written == {"src/f0.c", saves.MANIFEST}
    assert (folder / "src/f0.c").read_text(encoding="utf-8") == "int f0(void) { return 5000; }\n"


def test_a_claim_or_an_attempt_that_did_not_improve_the_best_does_not_render(project, monkeypatch):
    net, _, pid, _, _ = project
    monkeypatch.setattr(saves, "_render", lambda *args, **kwargs: pytest.fail("rendered a claim"))
    monkeypatch.setattr(saves, "_render_partial", lambda *args, **kwargs: pytest.fail("rendered a claim"))
    with net.store.tx() as db:
        db.execute("UPDATE functions SET attempts=attempts+1 WHERE project_id=? AND name='f1'", (pid,))
        db.execute("INSERT INTO claims(function_id,agent_id,claimed_at,expires_at) "
                   "SELECT id,1,0,0 FROM functions WHERE project_id=? AND name='f2'", (pid,))
        db.execute("INSERT INTO attempts(function_id,agent_id,score,compiled,context_version,source,created_at) "
                   "SELECT id,1,10,1,1,'not better',0 FROM functions WHERE project_id=? AND name='f3'", (pid,))
        db.execute("INSERT INTO events(project_id,kind,detail,created_at) VALUES(?,'test','x',0)", (pid,))
    assert net.progress.sync("game", force=True) is False


def test_a_manifest_only_pull_neither_judges_nor_rewrites_sources(project):
    net, folder, _, judged, _ = project
    before = _file_stats(folder)
    data = json.loads((folder / saves.MANIFEST).read_text(encoding="utf-8"))
    data["instructions"] = "from a pull"
    data["priority"] = ["f1"]
    (folder / saves.MANIFEST).write_text(json.dumps(data), encoding="utf-8")
    judged.clear()
    assert net.progress.sync("game", force=True) is True
    assert judged == []
    assert net.instructions("game") == "from a pull"
    after = _file_stats(folder)
    sources = {name: stat for name, stat in after.items() if name.startswith(("src/", "best/"))}
    assert sources == {name: stat for name, stat in before.items() if name.startswith(("src/", "best/"))}


def test_tool_calls_waiting_on_one_check_share_the_next_one(project):
    """Each helper's tool call forces a full check of the save folder before and after its work; with 25 helpers
    they queued on the project's lock and each then made its own identical check (4 s of a 5.6 s call). A call that
    waited while a check was running needs only one check that starts after it asked: the first waiter's serves
    every other waiter."""
    import threading
    net, _, _, _, _ = project
    progress = net.progress
    checks = []
    activity = progress._activity

    def counting(slug, **kwargs):
        if kwargs.get("phase") == "Reading saved files":
            checks.append(slug)
        return activity(slug, **kwargs)
    progress._activity = counting
    lock = progress._project_lock("game")
    with lock:  # a check in progress: these calls ask while it runs
        callers = [threading.Thread(target=progress.sync, args=("game",), kwargs={"force": True}) for _ in range(4)]
        for caller in callers:
            caller.start()
        time.sleep(0.3)
    for caller in callers:
        caller.join(30)
    assert len(checks) == 1, f"{len(checks)} full checks for 4 waiting calls"
    checks.clear()
    progress.sync("game", force=True)  # asked after that check finished: it checks again
    assert len(checks) == 1


def test_a_tool_calls_ordinary_check_does_not_queue_behind_a_running_one(project):
    """An agent's claim waited almost five minutes on the project's lock while the progress-commit job's sync held it.
    A tool call's ordinary check (its first engine lookup) skips the folder when one ran under CHECK_SECONDS ago, so
    while another thread's check holds the lock it returns at once. Forced checks (the judge's) and ordinary checks
    outside a tool call still wait for it."""
    import threading
    net, _, _, _, _ = project
    progress = net.progress
    lock = progress._project_lock("game")
    held, release = threading.Event(), threading.Event()

    def background_check():
        with lock:
            held.set()
            release.wait(30)
    holder = threading.Thread(target=background_check)
    holder.start()
    assert held.wait(5)
    finished = {}

    def run(key, call):
        call()
        finished[key] = time.monotonic()
    try:
        start = time.monotonic()
        with progress.deferring():
            assert progress.sync("game") is False
        assert time.monotonic() - start < 1, "a tool call's ordinary check waited for the running one"
        with progress.foreground():
            assert progress.sync("game") is False
        assert time.monotonic() - start < 1
        waiters = [threading.Thread(target=run, args=("forced", lambda: _deferred(progress, force=True))),
                   threading.Thread(target=run, args=("background", lambda: progress.sync("game")))]
        for waiter in waiters:
            waiter.start()
        time.sleep(0.3)
        assert finished == {}, "a forced or background check skipped the running one"
    finally:
        released = time.monotonic()
        release.set()
        holder.join(30)
    for waiter in waiters:
        waiter.join(30)
    assert set(finished) == {"forced", "background"}
    assert all(when >= released for when in finished.values())


def _deferred(progress, **kwargs):
    with progress.deferring():
        return progress.sync("game", **kwargs)
