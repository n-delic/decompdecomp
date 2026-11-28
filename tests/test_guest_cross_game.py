"""Manual cross-game search ('Matches from other games') works on room guest projects."""
import threading
from contextlib import nullcontext
from types import SimpleNamespace

from decompnet.app import similar_matches


def test_guest_cross_game_route_does_not_reject_room_guest():
    """POST /api/cross-game/run starts a job even when the project is a room guest."""
    from decompnet.app.routes.projects import register

    started = []
    registered = {}

    class DummyJobs:
        jobs = {}
        closing = False

        def start(self, name, work, **kw):
            started.append((name, kw.get("project")))
            return "job-1"

    net = SimpleNamespace(
        progress=SimpleNamespace(view=nullcontext),
        project_row=lambda slug: {"id": 1, "slug": slug, "name": slug},
        links={"guest-game": SimpleNamespace(slug="guest-game")},
        twins=SimpleNamespace(_lock=threading.Lock(), jobs={}, manual_jobs={}),
        autopilots={},
        cascades=SimpleNamespace(wake=lambda: None),
    )

    class DummyMcp:
        def custom_route(self, path, methods=None):
            def decorator(fn):
                registered[path] = fn
                return fn
            return decorator

    class DummyApp:
        def __init__(self):
            self.net = net
            self.jobs = DummyJobs()
            self.mcp = DummyMcp()
            self.manager = SimpleNamespace()
            self.call = None
            self.read = None
            self.fail = None
            self.guard = None
            self.settings = None
            self.body = None
            self.stream_call = None

    app = DummyApp()
    register(app)
    assert "/api/cross-game/run" in registered
    # Call the inner p_cross_run handler directly (stored as __wrapped__ or via closure)
    handler = registered["/api/cross-game/run"].__closure__
    p_cross_run = next(c.cell_contents for c in handler if getattr(c.cell_contents, "__name__", "") == "p_cross_run")
    result = p_cross_run("guest-game", SimpleNamespace(query_params={}), {})
    assert result == {"job": "job-1"}
    assert started == [("cross-game", "guest-game")]


def test_guest_gates_block_automatic_but_allow_manual_cross_game():
    """Automatic passes still skip room guests; manual cross_exhaustive passes run on guests."""
    proj = {"id": 1, "slug": "guest-game", "arch": "mips", "big_endian": True}
    net = SimpleNamespace(
        links={"guest-game": object()},
        fork_slug=lambda slug: None,
        is_rom=lambda p: True,
    )
    assert similar_matches.destination_supported(net, proj) is False
    assert similar_matches.destination_supported(net, proj, manual=True) is True


def test_try_quietly_forwards_match_to_room_host(monkeypatch):
    """When a room guest's try_quietly finds a match, it sends it to the room host via room_matches.send."""
    from decompnet.app.engine.autopilot import AutopilotMixin

    sent = []
    monkeypatch.setattr("decompnet.app.room_matches.packet", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr("decompnet.app.room_matches.send",
                        lambda link, agent, name, source, evidence=None: sent.append((name, source, evidence)))
    monkeypatch.setattr("decompnet.app.deep_room.offline", lambda link: False)
    monkeypatch.setattr("decompnet.app.boundary_repairs.blocked", lambda *a, **kw: set())
    monkeypatch.setattr("decompnet.app.exact_match_inputs.capture", lambda *a, **kw: {})
    monkeypatch.setattr("decompnet.app.compiler_diagnostics.output_fingerprint", lambda obj: "fp")
    monkeypatch.setattr("decomprepair.repair_localisation.bind", lambda *a, **kw: [])
    monkeypatch.setattr("decompnet.source_lines.mismatches", lambda *a, **kw: [])
    monkeypatch.setattr("decompnet.scoring.diff", lambda target, cand: SimpleNamespace(
        score=0, max_score=10, rows=[], bytes_match=True, percent=100.0))

    fn = {"id": 1, "project_id": 1, "name": "func_1", "unit": "u", "status": "open", "best_score": 50}
    proj = {"id": 1, "slug": "guest-game", "arch": "mips"}
    agent = SimpleNamespace(id=1, name="autopilot")
    link = SimpleNamespace(slug="guest-game", url="http://host", room="r", error="")

    class DummyNet(AutopilotMixin):
        links = {"guest-game": link}

        def project_store(self, pid):
            return SimpleNamespace(one=lambda *a, **kw: fn)

        def _context(self, pid, unit):
            return {"header": "", "version": 1}

        def _compile(self, proj, header, source, fn):
            return SimpleNamespace(ok=True, obj=object(), log="")

        def _evaluate(self, fn, proj, obj):
            return SimpleNamespace(code=b"\x00", relocs=[]), True, None

        def _target_obj(self, fn, proj):
            return object()

        def _record(self, *a, **kw):
            pass

        def _search_rows(self, *a, **kw):
            return []

    res = DummyNet()._try_quietly(agent, proj, fn, "void func_1(void) {}", exact_only=True)
    assert res["matched"] is True
    assert sent == [("func_1", "void func_1(void) {}", {"ok": True})]
