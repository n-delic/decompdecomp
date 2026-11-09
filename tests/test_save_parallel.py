"""Saved verification overlaps compilation, never the judge or project writes."""

import threading
from concurrent.futures import Future

import pytest

from decompnet.app import restore_compilation, saves, workers
from decompnet.app.engine import RomNetwork

from .test_rom import ROM, built  # noqa: F401
from .test_saves import worked  # noqa: F401


def test_saved_compiles_overlap_but_normal_judge_records_stay_on_coordinator(worked, tmp_path, monkeypatch):  # noqa: F811
    net, slug, _ = worked
    saves.save(net, slug, tmp_path / "saved")
    fresh = RomNetwork(tmp_path / "fresh.db", tmp_path / "fresh")
    coordinator = threading.get_ident()
    compile_threads, record_threads = [], []
    barrier = threading.Barrier(2)
    compile_source, record = fresh._compile, fresh._record

    def compile_parallel(*args):
        compile_threads.append(threading.get_ident())
        barrier.wait(timeout=10)  # Serial compilation or a stale snapshot fallback would fail here.
        return compile_source(*args)

    def record_serial(*args, **kwargs):
        record_threads.append(threading.get_ident())
        return record(*args, **kwargs)

    monkeypatch.setattr(fresh, "_compile", compile_parallel)
    monkeypatch.setattr(fresh, "_record", record_serial)
    monkeypatch.setattr(workers, "WORKERS", 1)
    monkeypatch.setattr(workers, "pool", lambda: pytest.fail("shared workers can be waiting for the project lock"))
    result = saves.restore(fresh, str(tmp_path / "saved"), ROM, log=lambda *_: None)
    assert result["matched"] == 1 and result["best"] == 1 and not result["lost"]
    assert len(compile_threads) == 2 and len(set(compile_threads)) == 2
    assert coordinator not in compile_threads
    assert len(record_threads)==2 and len(set(record_threads))==1
    assert not set(record_threads) & set(compile_threads)
    assert fresh.store.one("SELECT COUNT(*) AS n FROM claims")["n"] == 0
    fn = fresh._function("clamp_s32", result["slug"])
    assert fresh._agent_name(fn["matched_agent_id"]) == "claude-opus-5.5-1"


@pytest.fixture
def inputs(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,'{}')",
                         (str(tmp_path),)).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'header','')", (pid,))
        for i in range(4):
            db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit) "
                       "VALUES(?,?,?,4,1,'open','')", (pid, f"f{i}", 4096 + i*4))
    proj = dict(net._project("game"))
    rows = {row["name"]: dict(row) for row in net.store.all("SELECT * FROM functions")}
    saved = {name: {"vram": row["vram"], "best_by": "author"} for name, row in rows.items()}
    files = {f"best/{name}.c": f"void {name}(void) {{}}" for name in rows}
    return net, proj, rows, saved, files


@pytest.mark.parametrize("changed", ["config", "root", "name", "meta", "header", "source"])
def test_precompiled_object_is_rejected_when_inputs_change(inputs, monkeypatch, changed):
    net, proj, rows, _, _ = inputs
    fn, header, source = rows["f0"], "header", "void f0(void) {}"
    prepared = Future()
    prepared.set_result("background")
    snapshot = net._compile_snapshot(proj, fn, header, source)
    monkeypatch.setattr(net, "_compile", lambda *args: "fresh")
    with net.compiled_ahead((snapshot, prepared)):
        assert net._attempt_compilation(proj, header, source, fn) == "background"
        if changed in ("config", "root"):
            proj = {**proj, changed: "changed"}
        elif changed in ("name", "meta"):
            fn = {**fn, changed: "changed"}
        elif changed == "header":
            header = "changed"
        else:
            source = "changed"
        assert net._attempt_compilation(proj, header, source, fn) == "fresh"
    assert net._attempt_compilation(proj, "header", "void f0(void) {}", rows["f0"]) == "fresh"


def test_compile_window_is_bounded_and_cancellation_discards_queued_work(inputs, monkeypatch, tmp_path):
    net, proj, rows, saved, files = inputs
    submitted = []

    class Pool:
        def __init__(self, *args, **kwargs):
            pass

        def shutdown(self, **kwargs):
            pass

        def submit(self, *args):
            future = Future()
            submitted.append(future)
            return future

    monkeypatch.setattr(workers, "WORKERS", 2)
    monkeypatch.setattr(restore_compilation, "Pool", Pool)
    monkeypatch.setattr(restore_compilation, "width", lambda:2)
    checks = saves._saved_checks(net, proj, tmp_path, files, rows, {}, saved, True, lambda: None)
    next(checks)
    assert len(submitted) == 2
    checks.close()
    assert all(future.cancelled() for future in submitted)


def test_metadata_only_sync_does_not_schedule_compiles(inputs, monkeypatch, tmp_path):
    net, proj, rows, saved, files = inputs
    monkeypatch.setattr(workers, "pool", lambda: pytest.fail("metadata-only sync compiled source"))
    result = saves._judge_saved(net, proj["slug"], tmp_path, {}, rows, saved, lambda _: None,
                               files=files, judge_code=False)
    assert result == {"matched": 0, "best": 4, "lost": []}


def test_abandoning_verification_cancels_running_compiler_context(inputs, monkeypatch, tmp_path):
    from decompnet import compiler
    from decompnet.network import NetworkError

    net, proj, rows, saved, files = inputs
    started, finish = threading.Event(), threading.Event()

    def compile_wait(*args):
        started.set()
        assert finish.wait(5)
        compiler._CANCEL_CHECK.get()()  # The cancellation callback must reach the compiler worker.

    monkeypatch.setattr(workers, "WORKERS", 1)
    monkeypatch.setattr(restore_compilation, "width", lambda:1)
    monkeypatch.setattr(net, "_compile", compile_wait)
    checks = saves._saved_checks(net, proj, tmp_path, files, rows, {}, saved, True, lambda: None)
    item = next(checks)
    assert started.wait(5)
    checks.close()
    finish.set()
    with pytest.raises(NetworkError, match="compilation stopped"):
        item[-1][1].result(timeout=5)


def test_compile_snapshots_keep_per_unit_headers_and_range_settings(inputs, monkeypatch, tmp_path):
    import json

    net, proj, rows, saved, files = inputs
    cfg = {"compiler_ranges": [{"start": 4100, "end": 4104, "toolchain": "example", "flags": ["-O1"]}]}
    proj["config"] = json.dumps(cfg)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET unit='other' WHERE name='f1'")
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,2,'other header','other')",
                   (proj["id"],))
    captured = {}

    def compile_capture(proj, header, source, fn):
        captured[fn["name"]] = (header, source, json.loads(proj["config"]), fn["vram"])
        return fn["name"]

    monkeypatch.setattr(net, "_compile", compile_capture)
    checks = saves._saved_checks(net, proj, tmp_path, files, rows, {}, saved, True, lambda: None)
    for item in checks:
        item[-1][1].result(timeout=5)
    assert captured["f0"] == ("header", files["best/f0.c"], cfg, 4096)
    assert captured["f1"] == ("other header", files["best/f1.c"], cfg, 4100)


def test_metadata_scan_uses_one_status_snapshot_and_no_compiler_pool(inputs, monkeypatch, tmp_path):
    net, proj, rows, saved, files = inputs
    monkeypatch.setattr(restore_compilation, 'Pool', lambda *a, **k: pytest.fail('metadata scan started a pool'))
    one = net.store.one
    def no_function_queries(sql, *args):
        assert 'FROM functions' not in sql
        return one(sql, *args)
    monkeypatch.setattr(net.store, 'one', no_function_queries)
    lines, updates = [], []
    def log(line): lines.append(line)
    log.progress = lambda **fields: updates.append(fields)
    log.check_cancel = lambda: None
    result = saves._judge_saved(net, proj['slug'], tmp_path, {}, rows, saved, log,
                               files=files, judge_code=False)
    assert result == {'matched':0, 'best':4, 'lost':[]}
    assert len(lines) == 2 and len(updates) == 2
    assert updates[-1]['done'] == 4
