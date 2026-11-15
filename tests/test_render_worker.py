"""Progress-folder renders in processes of their own (app/render_worker.py), through the ordinary publish path."""
import json

import pytest

from decompnet.app import render_worker, saves
from decompnet.network import NetworkError
from tests.test_progress import project  # noqa: F401  (the fixture)


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
