"""Progress-folder renders in processes of their own (app/render_worker.py), through the ordinary publish path."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.app import render_worker, saves, source_runtime, tools
from decompnet.app.agents import Manager, Session
from decompnet.network import NetworkError
from tests.test_progress import project  # noqa: F401  (the fixture)
from tests.test_source_runtime import make_app, worker_checkouts


@pytest.fixture
def workers(project, tmp_path):  # noqa: F811
    net, folder, judged = project
    renderers = render_worker.Renderers(net, tmp_path / "internal", 1)
    net.progress.renderers = renderers
    yield net, folder, renderers
    renderers.stop()
    net.progress.renderers = None


def change(net):
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f(void) { return 1; }',best_score=7 WHERE name='f'")
        db.execute("UPDATE functions SET status='matched',matched_source='void g(void) {}',best_source=NULL "
                   "WHERE name='g'")


def test_a_publish_renders_in_a_worker(workers):
    net, folder, renderers = workers
    change(net)
    assert saves.Autosave(net).run_once() == ["game"]
    assert (folder / "best/f.c").read_text() == "int f(void) { return 1; }\n"
    assert (folder / "src/g.c").read_text() == "void g(void) {}\n"
    assert "g" in json.loads((folder / saves.MANIFEST).read_text())["functions"]
    assert renderers.served >= 1 and renderers.fallbacks == 0
    assert saves.Autosave(net).run_once() == []  # nothing new: nothing written


def test_without_a_worker_the_app_renders_as_before(workers, monkeypatch):
    net, folder, renderers = workers

    def down(*args, **kwargs):
        raise render_worker.Unavailable("no worker")
    monkeypatch.setattr(renderers, "render", down)
    change(net)
    assert saves.Autosave(net).run_once() == ["game"]
    assert (folder / "src/g.c").read_text() == "void g(void) {}\n"


def test_a_worker_that_died_is_replaced(workers):
    net, folder, renderers = workers
    renderers.render("game", folder, full=True)
    renderers.workers[0].process.kill()
    renderers.workers[0].process.wait()
    files, counts = renderers.render("game", folder, full=True)
    assert saves.MANIFEST in files and renderers.served == 2


def test_a_renders_expected_errors_come_back_as_themselves(workers):
    net, folder, renderers = workers
    with pytest.raises(NetworkError):
        renderers.render("no-such-project", folder, full=True)
    assert renderers.fallbacks == 0


def test_the_worker_renders_what_the_app_would(workers):
    net, folder, renderers = workers
    change(net)
    with net.progress.view():
        here, here_counts = saves._render(net, "game", write_readme=False)
        part_here = saves._render_partial(net, net._project("game"), folder, ["g"], headers=True, write_readme=False)
    there, there_counts = renderers.render("game", folder, full=True)
    part_there = renderers.render("game", folder, full=False, names=["g"], headers=True)

    def same(a, b):
        a, b = dict(a), dict(b)
        ma, mb = json.loads(a.pop(saves.MANIFEST)), json.loads(b.pop(saves.MANIFEST))
        ma.pop("saved_at"), mb.pop("saved_at")  # (the time of each render)
        return a == b and ma == mb
    assert same(here, there) and here_counts == there_counts
    assert same(part_here[0], part_there[0]) and part_here[1] == part_there[1]


@pytest.mark.parametrize("update_kind", ["source-update", "local-edit"])
def test_reload_finishes_the_save_and_preserves_the_running_agent(project, tmp_path, monkeypatch, update_kind):  # noqa: F811
    net, folder, _ = project
    worker_path = "src/decompnet/app/render_worker_child.py"
    original = render_worker.WORKER_SOURCE.read_text()
    before_reply = "        out.write(json.dumps(reply).encode()"
    assert before_reply in original
    began = tmp_path / "old-worker-has-rendered"
    finish = tmp_path / "old-worker-can-reply"
    updated = tmp_path / "new-worker-has-rendered"
    old_source = original.replace(before_reply, f"""        Path({str(began)!r}).touch()
        import time
        deadline = time.monotonic() + 30
        while not Path({str(finish)!r}).exists() and time.monotonic() < deadline:
            time.sleep(0.02)
{before_reply}""")
    new_source = original.replace(before_reply, f"        Path({str(updated)!r}).touch()\n{before_reply}")
    source, staged, revision = worker_checkouts(tmp_path, worker_path, old_source, new_source)
    monkeypatch.setattr(render_worker, "WORKER_SOURCE", source / worker_path)
    monkeypatch.setenv("DECOMP_LIVE_RELOAD", "0" if update_kind == "source-update" else "1")
    monkeypatch.setattr(source_runtime.edition, "CHECKOUT_ROOT", source)
    monkeypatch.setattr(source_runtime, "validate", lambda *args: (source, "git"))

    app = make_app(tmp_path)
    app.net = net
    app.manager = manager = Manager(net, app.home, "http://localhost/mcp")
    renderers = render_worker.Renderers(net, app.home, 1)
    net.progress.renderers = renderers
    agent, token = net.local_agent("render-reload-helper")
    session = Session("helper", "api", "Render reload helper", "game", agent.name, {}, state="working")
    manager.sessions[session.id] = session
    manager.assignments[agent.id] = session.slug
    session_id = manager._session_begin(session)
    runtime = source_runtime.Runtime(app)
    monkeypatch.setattr(runtime, "prepare", lambda *args: staged)
    monkeypatch.setattr(runtime, "drain", lambda: pytest.fail("the app must stay running"))
    tools.build_mcp(net, lambda ctx: (agent, session.slug), observe=manager.record_agent)

    def claims():
        return json.loads(tools.call(net, net.authenticate(token), session.slug, "get_claims", {}))["claims"]

    autosave = saves.Autosave(net)
    try:
        with ThreadPoolExecutor(max_workers=1) as agent_loop, ThreadPoolExecutor(max_workers=1) as saving:
            session.thread = agent_loop.submit(threading.current_thread).result(timeout=5)
            agent_loop.submit(net.claim_row, agent, "game", "f").result(timeout=5)
            held = agent_loop.submit(claims).result(timeout=5)
            assert held[0]["function"] == "f" and held[0]["claim_token"]
            change(net)
            pending = saving.submit(autosave.run_once)
            try:
                deadline = time.monotonic() + 15
                while not began.exists() and not pending.done() and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert began.exists() and not pending.done()
                old_worker = renderers.workers[0].process

                if update_kind == "source-update":
                    runtime.run(source, "git", "uv", tmp_path, "example/repo")
                    assert runtime.job["state"] == "done", runtime.job
                    assert app.build_id == revision
                else:
                    edited = (source / worker_path).with_suffix(".tmp")
                    edited.write_text(new_source)
                    edited.replace(source / worker_path)
                assert not pending.done() and old_worker.poll() is None
                assert not (folder / "src/g.c").exists()
                assert agent_loop.submit(claims).result(timeout=5) == held
            finally:
                finish.touch()

            assert pending.result(timeout=15) == ["game"]
            assert (folder / "src/g.c").read_text() == "void g(void) {}\n"
            assert "g" in json.loads((folder / saves.MANIFEST).read_text())["functions"]
            assert renderers.served == 1 and not updated.exists()
            with net.store.tx() as db:
                db.execute("UPDATE functions SET best_source='int f(void) { return 2; }' WHERE name='f'")
            assert autosave.run_once() == ["game"]
            assert (folder / "best/f.c").read_text() == "int f(void) { return 2; }\n"
            assert updated.exists() and renderers.workers[0].process.pid != old_worker.pid
            assert old_worker.poll() == 0 and renderers.served == 2 and renderers.fallbacks == 0

            assert agent_loop.submit(claims).result(timeout=5) == held
            assert agent_loop.submit(threading.current_thread).result(timeout=5) is session.thread
            assert manager.sessions[session.id] is session and session.state == "working" and not session.stop.is_set()
            sessions = net.project_store("game").all("SELECT id, ended_at FROM helper_sessions WHERE helper_id=?", session.id)
            assert [dict(row) for row in sessions] == [{"id": session_id, "ended_at": None}]
    finally:
        renderers.stop()
        net.progress.renderers = None
