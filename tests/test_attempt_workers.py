"""Worker updates during real attempts leave recording and agent leases with the app."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet import toolchains
from decompnet.app import attempt_workers, source_runtime, tools
from decompnet.app.agents import Manager, Session
from decompnet.elfobj import extract_function
from tests.test_fork_judge import SCALE, fork  # noqa: F401
from tests.test_rom_explorer import project  # noqa: F401
from tests.test_source_runtime import make_app, worker_checkouts


def attempt_during_update(tmp_path, monkeypatch, net, slug, function, sources, exact, phases, update_kind):
    worker_path = "src/decompnet/app/attempt_worker_child.py"
    original = attempt_workers.WORKER_SOURCE.read_text()
    started = tmp_path / "attempt-started"
    release = tmp_path / "attempt-release"
    trace = tmp_path / "worker-trace"
    marker = "                    result = perform(reader, judge, build, *request)"
    assert marker in original

    def instrument(version):
        return original.replace(marker, f"""{marker}
                    with Path({str(trace)!r}).open("a", encoding="utf-8") as log:
                        log.write({version!r} + " " + request[0] + "\\n")
                    if {version!r} == "old" and request[0] == {phases[0]!r}:
                        import time
                        Path({str(started)!r}).touch()
                        deadline = time.monotonic() + 30
                        while not Path({str(release)!r}).exists() and time.monotonic() < deadline:
                            time.sleep(.02)""")

    source, staged, revision = worker_checkouts(tmp_path, worker_path, instrument("old"), instrument("new"))
    monkeypatch.setattr(attempt_workers, "WORKER_SOURCE", source / worker_path)
    monkeypatch.setattr(source_runtime.edition, "CHECKOUT_ROOT", source)
    monkeypatch.setattr(source_runtime, "validate", lambda *args: (source, "git"))

    app = make_app(tmp_path)
    app.net = net
    pool = net.attempt_workers = attempt_workers.Pool(app.home, size=1)
    agent, token = net.local_agent("attempt-reload-helper")
    app.manager = manager = Manager(net, app.home, "http://localhost/mcp")
    session = Session("helper", "api", "Attempt helper", slug, agent.name, {}, state="working")
    manager.sessions[session.id] = session
    manager.assignments[agent.id] = slug
    session_id = manager._session_begin(session)
    runtime = source_runtime.Runtime(app)
    monkeypatch.setattr(runtime, "prepare", lambda *args: staged)
    monkeypatch.setattr(runtime, "drain", lambda: pytest.fail("an attempt-worker update must keep the app running"))
    tools.build_mcp(net, lambda ctx: (agent, slug), observe=manager.record_agent)

    def claim():
        reply = tools.call(net, net.authenticate(token), slug, "get_claims", {})
        return [(item["function"], item["claim_token"]) for item in json.loads(reply)["claims"]]

    store = net.project_store(slug)

    def records():
        return store.all("SELECT score, compiled, source FROM attempts WHERE agent_id=? ORDER BY id", agent.id)

    try:
        with ThreadPoolExecutor(max_workers=1) as agent_loop:
            session.thread = agent_loop.submit(threading.current_thread).result(timeout=5)
            agent_loop.submit(net.claim_row, agent, slug, function).result(timeout=10)
            held = claim()
            assert held[0][0] == function and held[0][1]
            pending = agent_loop.submit(net.attempt, agent, function, sources[0], slug)
            try:
                deadline = time.monotonic() + 20
                while not started.exists() and not pending.done() and time.monotonic() < deadline:
                    time.sleep(.02)
                if pending.done():
                    pytest.fail(f"Attempt returned before the worker paused: {pending.result()}")
                assert started.exists() and not records()
                if update_kind == "source-update":
                    runtime.run(source, "git", "uv", tmp_path, "example/repo")
                    assert runtime.job["state"] == "done" and app.build_id == revision, runtime.job
                else:
                    edited = (source / worker_path).with_suffix(".tmp")
                    edited.write_text(instrument("new"))
                    edited.replace(source / worker_path)
                assert not pending.done() and claim() == held
            finally:
                release.touch()
            first = pending.result(timeout=45)
            assert first["score"] is not None and first["score"] > 0 and not first["matched"], first
            assert [(r["score"], r["compiled"]) for r in records()] == [(first["score"], 1)]
            before = trace.read_text().splitlines()
            assert all(f"old {phase}" in before for phase in phases) and all(line.startswith("old ") for line in before)

            second = agent_loop.submit(net.attempt, agent, function, sources[1], slug).result(timeout=45)
            assert second["score"] is not None and not second["matched"], second
            assert [(r["score"], r["compiled"]) for r in records()] == [(first["score"], 1), (second["score"], 1)]
            after = trace.read_text().splitlines()[len(before):]
            assert all(f"new {phase}" in after for phase in phases) and all(line.startswith("new ") for line in after)
            assert agent_loop.submit(claim).result(timeout=5) == held
            assert agent_loop.submit(threading.current_thread).result(timeout=5) is session.thread
            assert manager.sessions[session.id] is session and session.state == "working" and not session.stop.is_set()
            assert [dict(row) for row in store.all("SELECT id, ended_at FROM helper_sessions WHERE helper_id=?", session.id)] == [
                {"id": session_id, "ended_at": None}
            ]

            if "project-build" in phases:
                _, fp, _ = net.forks.get(slug)
                path = fp.root / "src" / "lit.c"
                kept = path.read_bytes()
                bad = "double scale(double x) { return x + missing_name; }"
                failed = agent_loop.submit(net.attempt, agent, function, bad, slug).result(timeout=45)
                assert failed["score"] is None and "BUILD ERROR" in failed["text"]
                assert path.read_bytes() == kept
                assert claim() == held

            verdict = agent_loop.submit(net.attempt, agent, function, exact, slug).result(timeout=45)
            assert verdict["matched"] and verdict["score"] == 0, verdict
            assert [(r["score"], r["compiled"]) for r in records()][-1] == (0, 1)
            assert claim() == []  # the ordinary match releases the lease
    finally:
        pool.close()


@pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None, reason="needs the pinned N64 compiler")
@pytest.mark.parametrize("update_kind", ["source-update", "local-edit"])
def test_rom_attempt_records_across_worker_update(project, tmp_path, monkeypatch, update_kind):  # noqa: F811
    net, pid, _ = project
    exact = "int reader(void) { return 789; }"
    cfg = json.loads(net.project_row("game")["config"])
    cfg["compiler"]["flags"] = ["-O2"]
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), pid))
    row = net._function("reader", "game")
    compiled = net._compile(net.project_row("game"), "", exact, row)
    assert compiled.ok, compiled.log
    obj = extract_function(compiled.obj, "reader")
    assert obj is not None and not obj.relocs
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id, version, header, created_at, unit) VALUES(?,1,'',0,'')", (pid,))
        db.execute("UPDATE functions SET target=?, relocs='[]', size=?, n_insns=? WHERE id=?",
                   (obj.code, len(obj.code), len(obj.code) // 4, row["id"]))
    attempt_during_update(tmp_path, monkeypatch, net, "game", "reader",
                          ("int reader(void) { return 123; }", "int reader(void) { return 456; }"),
                          exact, ("compile", "rom-link", "score"), update_kind)


@pytest.mark.skipif(any(toolchains.installed(t) is None for t in ("gcc-2.7.2-kmc", "objdiff-cli")),
                    reason="needs the pinned N64 compiler and objdiff")
@pytest.mark.parametrize("update_kind", ["source-update", "local-edit"])
def test_project_attempt_records_across_worker_update(fork, tmp_path, monkeypatch, update_kind):  # noqa: F811
    net, slug, _ = fork
    attempt_during_update(tmp_path, monkeypatch, net, slug, "scale",
                          ("double scale(double x) { return x * 3.0; }",
                           "double scale(double x) { return x * 7.0; }"),
                          SCALE, ("project-build", "project-diff"), update_kind)


def audit_rust_judge(methods, trace, set_method=setattr):
    """Instrument the worker to compare the Rust and Python paths on the same object."""
    from decompnet import scoring
    from decompnet.rom import match

    scoring.instruction_alignment._kernel = lambda: None
    native_link, native_diff = methods.link, methods.diff

    def link(judge, obj, target, symbols, read):
        token = match.rom_linker.set(None)
        try:
            expected = match.link(obj, target.name, target, symbols, read)
        finally:
            match.rom_linker.reset(token)
        actual = native_link(judge, obj, target, symbols, read)
        assert actual is not None and actual == expected, (actual, expected)
        with open(trace, "a", encoding="utf-8") as log:
            log.write("link\n")
        return actual

    def diff(judge, target, candidate, *, details=True):
        expected = scoring._diff(target, candidate, details=details)
        actual = native_diff(judge, target, candidate, details=details)
        assert actual is not None and actual == expected, (actual, expected)
        with open(trace, "a", encoding="utf-8") as log:
            log.write("diff\n")
        return actual

    set_method(methods, "link", link)
    set_method(methods, "diff", diff)


@pytest.mark.parametrize("toolchain", ["gcc-2.7.2-kmc", "ido-7.1"])
def test_n64_desktop_attempt_and_interactive_diff_use_rust(project, monkeypatch, toolchain):  # noqa: F811
    import hashlib

    from decompnet import scoring
    from decompnet.app import mcp_gateway
    from decompnet.rom.desktop_judge import RustJudge
    from decompnet.rom.match import RomTarget, link

    if not (program := mcp_gateway.built()) or not scoring._native_mips_decoder_compatible():
        pytest.skip("needs a built gateway and matching Capstone")
    if not toolchains.installed(toolchain):
        pytest.skip(f"{toolchain} is not installed")

    net, pid, root = project
    exact = """
        volatile int D_80000050[2] = {6, 7};
        int reader(int x) { if (x < 0) return D_80000050[0]; return D_80000050[1]; }
    """
    cfg = json.loads(net.project_row("game")["config"])
    cfg["compiler"] = {"toolchain": toolchain, "flags": list(toolchains.get(toolchain).flags)}
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id, version, header, created_at, unit) VALUES(?,1,'',0,'')", (pid,))
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), pid))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (pid, "D_80000050", 0x80000050, 8, "data"))

    row = net._function("reader", "game")
    compiled = net._compile(net.project_row("game"), "", exact, row)
    assert compiled.ok, compiled.log
    image = bytearray((root / "game.rom").read_bytes())
    image[0x50:0x58] = bytes.fromhex("0000000600000007")
    baseline = link(compiled.obj, "reader", RomTarget("reader", row["vram"], b"", []), net.symbols(pid),
                    lambda addr, size: bytes(image[addr - row["vram"]:addr - row["vram"] + size]))
    assert baseline.linked_code and not baseline.problems
    assert baseline.candidate.relocs and len(baseline.linked_code) <= 0x50
    image[:len(baseline.linked_code)] = baseline.linked_code
    (root / "game.rom").write_bytes(image)
    cfg["rom"]["sha1"] = hashlib.sha1(image).hexdigest()
    relocations = [(r.offset, r.type, 0x80000050 + r.addend) for r in baseline.candidate.relocs]
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), pid))
        db.execute("UPDATE functions SET target=?, relocs=?, size=?, n_insns=? WHERE id=?",
                   (baseline.linked_code, json.dumps(relocations), len(baseline.linked_code),
                    len(baseline.linked_code) // 4, row["id"]))

    trace = root / "rust-trace"
    worker = root / "audit_worker.py"
    original = attempt_workers.WORKER_SOURCE.read_text()
    worker.write_text(original.replace('    serve()', f'''
    from tests.test_attempt_workers import audit_rust_judge
    audit_rust_judge(RustJudge, {str(trace)!r})
    serve()'''))
    monkeypatch.setattr(attempt_workers, "WORKER_SOURCE", worker)
    monkeypatch.setenv("DECOMP_MCP_GATEWAY", str(program))
    monkeypatch.setattr(scoring.instruction_alignment, "_kernel", lambda: None)
    pool = attempt_workers.Pool(root, size=1)
    agent, _ = net.local_agent("rust-attempt")
    net.claim_row(agent, "game", "reader")

    try:
        # First a linked-data miss with identical instructions, then changed control flow,
        # then the same function and data as the ROM.
        cases = (exact.replace("6, 7", "6, 8"),
                 exact.replace("if (x < 0) return", "if (x == 5) return x; if (x > 0) return"),
                 exact)
        for index, source in enumerate(cases):
            net.attempt_workers = pool
            trace.write_text("")
            attempt = net.attempt(agent, "reader", source, "game")
            assert {"link", "diff"} <= set(trace.read_text().splitlines())
            assert attempt["matched"] == (index == 2) and attempt["score"] is not None, attempt
            net._diffs.clear()
            trace.write_text("")
            actual = net.function_diff("game", "reader")
            phases = trace.read_text().splitlines()
            assert "link" in phases and "diff" in phases
            net._diffs.clear()
            net.attempt_workers = None
            with monkeypatch.context() as python:
                python.setenv("DECOMP_MCP_GATEWAY", "0")
                expected = net.function_diff("game", "reader")
            assert actual == expected
            net._diffs.clear()
            trace.write_text("")
            with monkeypatch.context() as audit:
                audit_rust_judge(RustJudge, trace, audit.setattr)
                assert net.function_diff("game", "reader") == expected
                assert {"link", "diff"} <= set(trace.read_text().splitlines())
                if index == 0:
                    row = net._function("reader", "game")
                    proj = net.project_row("game", sync=False)
                    output = net._compile(proj, "", source, row)
                    assert output.ok, output.log
                    target = net._rom_target(row)
                    reader = net.reader(proj, row)
                    # Standalone exports supply their own reader instead of a project snapshot.
                    read_calls = []
                    def custom_read(address, size, calls=read_calls, read=reader):
                        calls.append((address, size))
                        return read(address, size)

                    linked = net.console(proj).link_function(output.obj, "reader", target, net.symbols(pid), custom_read)
                    assert not linked.exact and linked.instructions_exact and read_calls
                    assert scoring.score(net._target_obj(row, proj), linked.candidate).rows == []
            assert actual["score"] == attempt["score"]
            if index == 0:
                assert actual["score"] == 1 and actual["checks"]["instructions"]["exact"]
                assert actual["checks"]["data"]
                # Replace the image while the same native worker remains alive.
                with (root / "game.rom").open("r+b") as stream:
                    stream.seek(0x57)
                    stream.write(b"\x08")
                net._roms.pop(pid, None)
                net._diffs.clear()
                net.attempt_workers = pool
                refreshed = net.function_diff("game", "reader")
                assert refreshed["score"] == 0 and not refreshed["checks"]["data"]
                (root / "game.rom").write_bytes(image)
                net._roms.pop(pid, None)
            if index == 1:
                assert any(row[0] != " " for row in actual["rows"])
            if index == 2:
                assert actual["percent"] == 100
    finally:
        pool.close()
