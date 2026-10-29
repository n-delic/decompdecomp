"""Rooms: a host and a guest, each with their own copy of Demo Quest 64, working together."""

import json
import re
from contextlib import contextmanager

import pytest
from starlette.testclient import TestClient

from decompnet.app import collab
from decompnet.app.engine import RomNetwork
from decompnet.app.engine.rooms import RoomMixin
from decompnet.network import NetworkError
from decompnet.store import Store

from .test_app_engine import CLAMP, DAMAGE
from .test_rom import built  # noqa: F401


@pytest.fixture(scope="module")
def room(built, tmp_path_factory):  # noqa: F811
    h = tmp_path_factory.mktemp("host")
    host = RomNetwork(h / "d.db", h)
    slug = host.import_rom(built, confirmed=True, log=lambda *_: None)
    host.rooms[slug] = collab.Room(host, slug)
    code = host.rooms[slug].open_invite()
    client = TestClient(collab.host_app(host, host.rooms), base_url="https://abc.trycloudflare.com")
    g = tmp_path_factory.mktemp("guest")
    guest = RomNetwork(g / "d.db", g)
    link = f"https://abc.trycloudflare.com/join/{code}#room={slug}"
    gslug = collab.join_room(guest, link, "alex", built, log=lambda *_: None, client=client, start=False)
    return host, slug, guest, gslug, client, code, link


def test_parse_invite():
    assert collab.parse_invite("https://x-y.trycloudflare.com/join/abcdefghij#room=demo") == ("https://x-y.trycloudflare.com", "abcdefghij#demo")
    with pytest.raises(NetworkError):
        collab.parse_invite("https://example.com/whatever")


def test_joining_needs_the_same_rom_and_a_valid_invite(room, built, tmp_path):  # noqa: F811
    host, slug, guest, gslug, client, code, link = room
    other = RomNetwork(tmp_path / "o.db", tmp_path)
    changed = bytearray(built)
    changed[0x2000] ^= 0xFF
    with pytest.raises(NetworkError, match="same as the host"):
        collab.join_room(other, link, "eve", bytes(changed), log=lambda *_: None, client=client, start=False)
    with pytest.raises(NetworkError, match="no longer valid"):
        collab.join_room(other, link.replace(code, "x" * 22), "eve", built, log=lambda *_: None, client=client, start=False)
    assert [m["name"] for m in host.rooms[slug].members()] == ["alex"]


def test_state_carries_work_never_rom_data(room):
    host, slug, guest, gslug, client, code, link = room
    tok = guest.links[gslug].token
    st = client.get(f"/collab/state?room={slug}", headers={"authorization": f"Bearer {tok}"}).json()
    text = json.dumps(st)
    assert "glabel" not in text and "target" not in st
    assert not re.search(r"/\* [0-9A-F]+ [0-9A-F]{8} [0-9A-F]{8} \*/", text)  # no disassembly lines
    assert st["functions"] and st["members"][0]["name"] == "alex"
    assert client.get(f"/collab/state?room={slug}", headers={"authorization": "Bearer dn_nope"}).status_code == 400


def test_split_and_match_from_the_guest_reach_the_host(room):
    host, slug, guest, gslug, client, code, link = room
    agent, _ = guest.local_agent("claude-1")
    assert guest.split_function(agent, gslug, "func_800007C4", "0x800007D0").startswith("✅")
    assert host._function("func_800007D0", slug)  # split on the host...
    assert guest._function("func_800007D0", gslug)  # ...and mirrored locally
    brief = guest.claim(agent, gslug, "func_800007D0")
    assert "glabel func_800007D0" in brief  # the guest's own copy provides the assembly
    claims = host.live(slug)["claims"]
    assert claims and claims[0]["agent"] == "alex.claude-1"
    out = guest.compile_and_diff(agent, "func_800007D0", CLAMP, gslug)
    assert out.startswith("✅ MATCH") and "host confirmed" in out
    assert host._function("func_800007D0", slug)["status"] == "matched"


def test_host_work_reaches_the_guest(room):
    host, slug, guest, gslug, client, code, link = room
    hagent, _ = host.local_agent("codex-1")
    assert host.compile_and_diff(hagent, "func_80000808", DAMAGE, slug).startswith("✅ MATCH")
    assert host.rename_symbol(hagent, slug, "func_800007D0", "clamp_s32").startswith("✅")
    assert host.edit_context(hagent, slug, "", "s32 clamp_s32(s32 v, s32 lo, s32 hi);", "prototype").startswith("✅")
    guest.links[gslug].sync()
    assert guest._function("func_80000808", gslug)["status"] == "matched"
    assert guest._function("clamp_s32", gslug)["status"] == "matched"
    assert "clamp_s32(s32 v" in guest.get_context(gslug)
    feed = [e["kind"] for e in guest.live(gslug)["events"]]
    assert "match" in feed
    assert guest.overview(gslug)["room"]["role"] == "guest"


def test_join_shares_stronger_saved_progress(built, tmp_path, monkeypatch):  # noqa: F811
    host_home = tmp_path / "host"
    host_home.mkdir()
    host = RomNetwork(host_home / "d.db", host_home)
    slug = host.import_rom(built, confirmed=True, log=lambda *_: None)
    host_agent, _ = host.local_agent("claude-1")
    host.split_function(host_agent, slug, "func_800007C4", "0x800007D0")
    host.rooms[slug] = collab.Room(host, slug)
    code = host.rooms[slug].open_invite()
    client = TestClient(collab.host_app(host, host.rooms), base_url="https://abc.trycloudflare.com")
    link = f"https://abc.trycloudflare.com/join/{code}#room={slug}"

    guest = RomNetwork(tmp_path / "saved.db", tmp_path)
    gslug = guest.import_rom(built, confirmed=True, log=lambda *_: None)
    agent, _ = guest.local_agent("claude-1")
    guest.split_function(agent, gslug, "func_800007C4", "0x800007D0")
    assert guest.compile_and_diff(agent, "func_800007D0", CLAMP, gslug).startswith("✅ MATCH")
    best = guest.attempt(agent, "func_800007F4", "s32 func_800007F4(s32 v) { return v + 1; }", gslug)
    assert best["score"] and not best["matched"]

    share = collab._share_saved_progress

    def transient_failure(*_args, **_kwargs):
        raise NetworkError("the room's host isn't reachable right now")

    monkeypatch.setattr(collab, "_share_saved_progress", transient_failure)
    joined = collab.join_room(guest, link, "bea", built, log=lambda *_: None, client=client, start=False)

    assert joined == gslug
    assert guest.links[gslug].saved_progress
    assert "saved_progress" not in json.loads(guest.project_row(gslug)["config"])["link"]
    queued = guest.store.all("SELECT name, kind, source FROM saved_progress WHERE project_id=?",
                             guest.project_row(gslug)["id"])
    assert {row["name"] for row in queued} == {"func_800007D0", "func_800007F4"}
    assert host._function("func_800007D0", slug)["status"] != "matched"
    monkeypatch.setattr(collab, "_share_saved_progress", share)
    guest.links[gslug].sync()
    assert host._function("func_800007D0", slug)["status"] == "matched"
    received = host._function("func_800007F4", slug)
    assert received["best_score"] == best["score"]
    assert "v + 1" in received["best_source"]
    assert guest.store.all("SELECT name FROM saved_progress WHERE project_id=?", guest.project_row(gslug)["id"]) == []


def test_saved_progress_waits_through_restart_and_retries_transient_failure(built, tmp_path, monkeypatch):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path, approve=True)
    link_text = f"http://192.168.1.20:6481/join/{code}#room={slug}"
    guest, gslug = _guest(tmp_path, "sam", link_text, built, client)
    original = guest.links[gslug]
    candidates = [{"name": "func_800007F4", "vram": 0x800007F4, "size": 12, "status": "matched",
                   "matched_source": ABS, "best_score": None, "best_source": None}]
    original.saved_progress = (candidates, False)
    original._save_saved_progress()

    restarted = RomNetwork(tmp_path / "sam.db", tmp_path / "sam")
    info = json.loads(restarted.project_row(gslug)["config"])["link"]
    link = restarted.link_project(gslug, info, client=client, start=False)
    assert link.saved_progress == (candidates, False)
    link._req = lambda *_args, **_kwargs: {"e": 0, "a": 0, "behind": 0, "events": []}
    link.apply = lambda _state: None
    calls = 0

    def share(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise NetworkError("the room's host isn't reachable right now")

    monkeypatch.setattr(collab, "_share_saved_progress", share)
    with pytest.raises(NetworkError, match="isn't reachable"):
        link.sync()
    assert link.saved_progress == (candidates, False)
    queued = restarted.store.all("SELECT name, kind, source FROM saved_progress WHERE project_id=?",
                                 restarted.project_row(gslug)["id"])
    assert [(row["name"], row["kind"], row["source"]) for row in queued] == [
        ("func_800007F4", "match", ABS)]

    link.sync()
    assert calls == 2 and link.saved_progress is None
    assert restarted.store.all("SELECT name FROM saved_progress WHERE project_id=?",
                               restarted.project_row(gslug)["id"]) == []
    assert "saved_progress" not in json.loads(restarted.project_row(gslug)["config"])["link"]


def test_saved_progress_retry_preserves_fork_mode(tmp_path, monkeypatch):
    """A queue an older version kept in the link's config moves into its table, and retries keep fork mode."""
    candidate = {"name": "matched_fn", "status": "matched", "matched_source": "int matched_fn(void) { return 1; }"}
    config = {"link": {"room": "room", "saved_progress": {"candidates": [candidate], "best_comparable": False}}}
    store = Store(tmp_path / "room.db")
    with store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                   ("project", "Project", str(tmp_path), json.dumps(config)))

    class Net:
        def project_row(self, _slug):
            return store.one("SELECT * FROM projects WHERE slug='project'")

        def fork_slug(self, _slug):
            return "fork"

    net = Net()
    net.store = store
    link = collab.Link(net, "project", "http://host", "room", "token", "member", client=object())
    assert link.saved_progress == ([candidate], False)
    assert [(row["name"], row["kind"], row["source"]) for row in
            store.all("SELECT name, kind, source FROM saved_progress")] == [
        ("matched_fn", "match", candidate["matched_source"])]
    assert "saved_progress" not in json.loads(store.one("SELECT config FROM projects")["config"])["link"]
    link._req = lambda *_args, **_kwargs: {"e": 0, "a": 0, "behind": 0, "events": []}
    link.apply = lambda _state: None
    calls = []

    def share(_net, _slug, _link, _candidates, *, fork, **_kwargs):
        calls.append(fork)
        if len(calls) == 1:
            raise NetworkError("temporary host failure")

    monkeypatch.setattr(collab, "_share_saved_progress", share)
    with pytest.raises(NetworkError, match="temporary host failure"):
        link.sync()
    assert [row["name"] for row in store.all("SELECT name FROM saved_progress")] == ["matched_fn"]

    link.sync()
    assert calls == [True, True]
    assert store.all("SELECT name FROM saved_progress") == []
    assert "saved_progress" not in json.loads(store.one("SELECT config FROM projects")["config"])["link"]


def test_saved_progress_waits_for_approval_then_arrives(built, tmp_path):  # noqa: F811
    """A guest waiting to be let in keeps its saved work queued; the first sync after approval still asks for the
    host's function list (a fresh state with an advanced cursor) and sends it."""
    host, slug, room, code, client = _host(built, tmp_path, approve=True)
    link_text = f"http://192.168.1.20:6481/join/{code}#room={slug}"
    guest = RomNetwork(tmp_path / "waiting.db", tmp_path / "waiting")
    gslug = guest.import_rom(built, confirmed=True, log=lambda *_: None)
    agent, _ = guest.local_agent("claude-1")
    assert guest.compile_and_diff(agent, "func_800007F4", ABS, gslug).startswith("✅ MATCH")

    collab.join_room(guest, link_text, "kim", built, log=lambda *_: None, client=client, start=False)
    waiting = guest.links[gslug]
    assert waiting.saved_progress
    assert host._function("func_800007F4", slug)["status"] != "matched"

    room.approve("kim")
    waiting.sync()
    assert host._function("func_800007F4", slug)["status"] == "matched"
    assert waiting.saved_progress is None
    assert guest.store.all("SELECT name FROM saved_progress WHERE project_id=?",
                           guest.project_row(gslug)["id"]) == []


def test_one_refused_saved_candidate_does_not_wedge_the_others(built, tmp_path, monkeypatch):  # noqa: F811
    """A candidate the host refuses (its function was claimed meanwhile) is dropped on its own; the candidate after
    it still arrives and the queue empties."""
    host, slug, room, code, client = _host(built, tmp_path)
    guest = RomNetwork(tmp_path / "mixed.db", tmp_path / "mixed")
    gslug = guest.import_rom(built, confirmed=True, log=lambda *_: None)
    agent, _ = guest.local_agent("claude-1")
    assert guest.compile_and_diff(agent, "func_80000808", DAMAGE, gslug).startswith("✅ MATCH")
    assert guest.compile_and_diff(agent, "func_800007F4", ABS, gslug).startswith("✅ MATCH")
    candidates = collab._saved_progress(guest, slug=gslug)
    bad = next(candidate for candidate in candidates if candidate["name"] == "func_80000808")
    good = next(candidate for candidate in candidates if candidate["name"] == "func_800007F4")
    monkeypatch.setattr(collab, "_saved_progress", lambda *_args, **_kwargs: [bad, good])
    host_agent, _ = host.local_agent("claude-1")
    host.claim(host_agent, slug, "func_80000808")  # the guest's attempt on it will be refused

    said = []
    collab.join_room(guest, f"http://192.168.1.20:6481/join/{code}#room={slug}", "lee", built,
                     log=said.append, client=client, start=False)
    assert host._function("func_80000808", slug)["status"] != "matched"
    assert host._function("func_800007F4", slug)["status"] == "matched"
    assert guest.links[gslug].saved_progress is None
    assert guest.store.all("SELECT name FROM saved_progress WHERE project_id=?",
                           guest.project_row(gslug)["id"]) == []
    assert any("refused saved progress for func_80000808" in line for line in said), said


def test_a_failure_before_the_join_leaves_no_member_or_project(built, tmp_path, monkeypatch):  # noqa: F811
    """#80: what the join carries is worked out before the host registers the member, so a corrupt progress folder
    can't leave a member on the host and an imported project here."""
    host, slug, room, code, client = _host(built, tmp_path)
    guest = RomNetwork(tmp_path / "broken.db", tmp_path / "broken")

    def corrupt(*_args, **_kwargs):
        raise RuntimeError("its progress folder is corrupt")

    monkeypatch.setattr(collab, "_saved_progress", corrupt)
    with pytest.raises(RuntimeError, match="progress folder is corrupt"):
        collab.join_room(guest, f"http://192.168.1.20:6481/join/{code}#room={slug}", "kim", built,
                         log=lambda *_: None, client=client, start=False)
    assert guest.store.all("SELECT slug FROM projects") == []
    assert [m["name"] for m in room._members_now()] == []


def test_saved_progress_for_another_room_stays_there(tmp_path):
    """A queue belongs to the room it was made for: linking the project elsewhere must not send it there."""
    store = Store(tmp_path / "rooms.db")
    config = {"link": {"url": "http://new-host", "room": "new-room"}}
    with store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                   ("project", "Project", str(tmp_path), json.dumps(config)))
        pid = db.execute("SELECT id FROM projects WHERE slug='project'").fetchone()["id"]
        db.execute("INSERT INTO saved_progress(project_id,room,url,name,kind,source,score,best_comparable) "
                   "VALUES(?,?,?,?,?,?,?,?)",
                   (pid, "old-room", "http://old-host", "saved_fn", "match", "int saved_fn(void) {}", None, 0))

    class Net:
        def project_row(self, _slug):
            return store.one("SELECT * FROM projects WHERE slug='project'")

        def fork_slug(self, _slug):
            return None

    net = Net()
    net.store = store
    link = collab.Link(net, "project", "http://new-host", "new-room", "token", "member", client=object())
    assert link.saved_progress is None
    assert store.all("SELECT name FROM saved_progress") == []


def test_link_refresh_keeps_saved_progress_only_in_the_same_room(monkeypatch):
    queued = {"candidates": [{"name": "saved_fn"}], "best_comparable": False}
    config = {"link": {"url": "http://host-a", "room": "room-a", "saved_progress": queued}}

    class Store:
        @contextmanager
        def tx(self):
            class DB:
                def execute(_self, _query, params):
                    updated = json.loads(params[0])
                    config.clear()
                    config.update(updated)

            yield DB()

    class Net(RoomMixin):
        store = Store()
        links = {}

        def _project(self, _slug):
            return {"id": 1, "config": json.dumps(config)}

    class StubLink:
        def __init__(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(collab, "Link", StubLink)
    net = Net()
    net.link_project("project", {"url": "http://host", "room": "room-a", "token": "t", "member": "m",
                                  "saved_progress": queued}, start=False)
    assert "saved_progress" not in config["link"]  # a different host with the same room slug is not this room

    config["link"] = {"url": "http://host-a", "room": "room-a", "saved_progress": queued}
    net.link_project("project", {"url": "http://host-a", "room": "room-a", "token": "t", "member": "m"}, start=False)
    assert config["link"]["saved_progress"] == queued

    net.link_project("project", {"url": "http://host-a", "room": "room-b", "token": "t", "member": "m"}, start=False)
    assert "saved_progress" not in config["link"]


def test_saved_fork_progress_excludes_projects_linked_to_another_room(monkeypatch):
    projects = {
        "local": {"identity": "same-game"},
        "same-room": {"identity": "same-game", "link": {"url": "http://host", "room": "target"}},
        "other-room": {"identity": "same-game", "link": {"url": "http://other", "room": "other"}},
        "same-slug-other-host": {"identity": "same-game", "link": {"url": "http://other", "room": "target"}},
        "different-game": {"identity": "different-game"},
    }
    rows = {
        "local": [{"name": "local_fn", "status": "matched"}],
        "same-room": [{"name": "room_fn", "status": "matched"}],
        "other-room": [{"name": "other_fn", "status": "matched"}],
        "same-slug-other-host": [{"name": "other_host_fn", "status": "matched"}],
        "different-game": [{"name": "unrelated_fn", "status": "matched"}],
    }

    class Store:
        def all(self, query, project_id=None):
            if "SELECT slug, config FROM projects" in query:
                return [{"slug": slug, "config": json.dumps(config)} for slug, config in projects.items()]
            return rows[next(slug for slug, pid in ids.items() if pid == project_id)]

    ids = {slug: index for index, slug in enumerate(projects, 1)}

    class Net:
        store = Store()

        def project_row(self, slug):
            return {"id": ids[slug]}

    monkeypatch.setattr(collab.forkroom, "project_identity", lambda config: config["identity"])
    candidates = collab._saved_progress(Net(), identity="same-game", room="target", url="http://host")
    assert {candidate["name"] for candidate in candidates} == {"local_fn", "room_fn"}


def test_guest_edits_go_through_the_host(room):
    host, slug, guest, gslug, client, code, link = room
    agent, _ = guest.local_agent("claude-2")
    out = guest.rename_symbol(agent, gslug, "func_80000808", "player_take_damage")
    assert out.startswith("✅")
    assert host._function("player_take_damage", slug)["status"] == "matched"
    assert guest._function("player_take_damage", gslug)
    guest.add_note(agent, gslug, "this player's struct is at 0x8004AE34", "player_take_damage")
    assert "0x8004AE34" in host.search_notes(slug, "player's struct")
    # a note on no function is knowledge for every agent: through the host, it needs two models' agreement too
    guest.links[gslug].call("/collab/note", agent, text="Every menu screen has Init, Update and Draw functions")
    assert [k["status"] for k in host.knowledge.listing(slug)["items"]] == ["proposed"]


ABS = "s32 func_800007F4(s32 v) { return v < 0 ? -v : v; }"


def test_two_friends_work_together(room, built, tmp_path):  # noqa: F811
    host, slug, guest, gslug, client, code, link = room
    bea = RomNetwork(tmp_path / "bea.db", tmp_path)
    bslug = collab.join_room(bea, link, "bea", built, log=lambda *_: None, client=client, start=False)
    assert sorted(m["name"] for m in host.rooms[slug].members()) == ["alex", "bea"]
    a_agent, _ = guest.local_agent("codex-1")
    b_agent, _ = bea.local_agent("claude-1")
    guest.claim(a_agent, gslug)
    bea.claim(b_agent, bslug)
    claims = {c["agent"]: c["name"] for c in host.live(slug)["claims"]}
    assert claims["alex.codex-1"] != claims["bea.claude-1"]  # the host never hands out the same function twice
    guest.release(a_agent, claims["alex.codex-1"], "", gslug)
    out = guest.compile_and_diff(a_agent, "func_800007F4", ABS, gslug)
    assert out.startswith("✅ MATCH"), out
    bea.links[bslug].sync()
    assert bea._function("func_800007F4", bslug)["status"] == "matched"  # bea sees alex's match


def test_reconnect_after_the_host_restarts(room, built):  # noqa: F811
    host, slug, guest, gslug, client, code, link = room
    host.rooms[slug].close()  # the host quits: the tunnel and its address are gone
    new_code = host.rooms[slug].open_invite()  # ...and shares again from a new address
    new_client = TestClient(collab.host_app(host, host.rooms), base_url="https://new-address.trycloudflare.com")
    new_link = f"https://new-address.trycloudflare.com/join/{new_code}#room={slug}"
    before = sorted(m["name"] for m in host.rooms[slug].members())
    again = collab.join_room(guest, new_link, "alex", b"", log=lambda *_: None, client=new_client, start=False)
    assert again == gslug
    assert sorted(m["name"] for m in host.rooms[slug].members()) == before  # same member, not "alex2"
    link_obj = guest.links[gslug]
    assert link_obj.url == "https://new-address.trycloudflare.com" and link_obj.member == "alex"
    link_obj.sync()
    agent, _ = guest.local_agent("claude-9")
    assert "CLAIMED" in guest.claim(agent, gslug)


def test_closing_the_room_locks_guests_out(room):
    host, slug, guest, gslug, client, code, link = room
    host.rooms[slug].close()
    with pytest.raises(NetworkError):
        collab.join_room(RomNetwork(":memory:", __import__("pathlib").Path("/tmp")), link, "late", b"", client=client, start=False)


def _host(built, tmp_path, **settings):  # noqa: F811
    host = RomNetwork(tmp_path / "h.db", tmp_path / "h")
    slug = host.import_rom(built, confirmed=True, log=lambda *_: None)
    room = collab.Room(host, slug)
    room.configure(**settings)
    host.rooms[slug] = room
    code = room.open_invite()
    client = TestClient(collab.host_app(host, host.rooms), base_url="http://192.168.1.20:6481")
    return host, slug, room, code, client


def _guest(tmp_path, name, link, built, client):  # noqa: F811
    g = RomNetwork(tmp_path / f"{name}.db", tmp_path / name)
    return g, collab.join_room(g, link, name, built, log=lambda *_: None, client=client, start=False)


def test_a_join_that_never_connected_takes_over_its_empty_member(built, tmp_path):  # noqa: F811
    """#80: a join that stopped while splitting the game left its member on the host, and the retry got the next
    number. A member of this room that never connected is taken over by the same name again."""
    host, slug, room, code, client = _host(built, tmp_path)
    sha1 = json.loads(host.project_row(slug)["config"])["rom"]["sha1"]
    old_token, old_name = room.join("tiredunc", sha1)
    assert old_name == "tiredunc"
    answer = client.post(f"/collab/join?room={slug}", json={"invite": code, "name": "TiredUnc", "sha1": sha1})
    assert answer.status_code == 200
    joined = answer.json()
    assert joined["name"] == "tiredunc" and joined["token"] != old_token
    old = client.get(f"/collab/whoami?room={slug}", headers={"authorization": f"Bearer {old_token}"})
    assert old.status_code == 400  # the lost token was retired
    new = client.get(f"/collab/whoami?room={slug}", headers={"authorization": f"Bearer {joined['token']}"})
    assert new.status_code == 200 and new.json()["name"] == "tiredunc"
    assert [m["name"] for m in room._members_now()] == ["tiredunc"]


def test_a_member_that_has_connected_still_gets_a_number(built, tmp_path):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path)
    sha1 = json.loads(host.project_row(slug)["config"])["rom"]["sha1"]
    token, _ = room.join("kim", sha1)
    assert client.get(f"/collab/whoami?room={slug}", headers={"authorization": f"Bearer {token}"}).status_code == 200
    joined = client.post(f"/collab/join?room={slug}", json={"invite": code, "name": "kim", "sha1": sha1}).json()
    assert joined["name"] == "kim2"
    assert [m["name"] for m in room._members_now()] == ["kim", "kim2"]


@pytest.mark.parametrize("left", ["removed", "declined"])
def test_a_member_that_left_the_room_still_gets_a_number(built, tmp_path, left):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path, approve=left == "declined")
    sha1 = json.loads(host.project_row(slug)["config"])["rom"]["sha1"]
    token, _ = room.join("kim", sha1)
    if left == "removed":
        room.remove("kim")
    else:
        room.approve("kim", False)
    joined = client.post(f"/collab/join?room={slug}", json={"invite": code, "name": "kim", "sha1": sha1}).json()
    assert joined["name"] == "kim2" and joined["token"] != token


def test_a_taken_over_member_waits_for_the_host_again(built, tmp_path):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path, approve=True)
    sha1 = json.loads(host.project_row(slug)["config"])["rom"]["sha1"]
    old_token, _ = room.join("kim", sha1)
    assert [p["name"] for p in room.pending()] == ["kim"]
    answer = client.post(f"/collab/join?room={slug}", json={"invite": code, "name": "kim", "sha1": sha1})
    assert answer.status_code == 200
    joined = answer.json()
    assert joined["name"] == "kim" and joined["waiting"] is True and joined["token"] != old_token
    assert [p["name"] for p in room.pending()] == ["kim"]  # one member, still waiting
    waiting = client.get(f"/collab/whoami?room={slug}", headers={"authorization": f"Bearer {joined['token']}"})
    assert waiting.status_code == 400 and waiting.json()["error"] == collab.WAITING
    room.approve("kim")
    assert client.get(f"/collab/whoami?room={slug}",
                      headers={"authorization": f"Bearer {joined['token']}"}).status_code == 200
    assert [m["name"] for m in room._members_now()] == ["kim"]


def test_join_room_checks_the_name_before_importing(built, tmp_path, monkeypatch):  # noqa: F811
    """#80: a name the host refuses shouldn't cost a full split first (a typo waited out the import, then lost it)."""
    host, slug, room, code, client = _host(built, tmp_path)
    g = RomNetwork(tmp_path / "refused.db", tmp_path / "refused")
    imported = []
    monkeypatch.setattr(g, "import_rom", lambda *args, **kwargs: imported.append(1))
    with pytest.raises(NetworkError, match="24 letters"):
        collab.join_room(g, f"http://192.168.1.20:6481/join/{code}#room={slug}", "a b", built,
                         log=lambda *_: None, client=client, start=False)
    assert imported == []  # no split was started
    assert g.store.all("SELECT slug FROM projects") == []  # no project was left behind
    assert [m["name"] for m in room._members_now()] == []  # nor a member on the host


def test_join_room_removes_the_import_when_the_host_refuses_the_join(built, tmp_path, monkeypatch):  # noqa: F811
    """#80: the guest imports first now, so another refusal from the host must remove what it just imported."""
    host, slug, room, code, client = _host(built, tmp_path)
    g = RomNetwork(tmp_path / "refused.db", tmp_path / "refused")
    imported = []
    real = g.import_rom

    def record(*args, **kwargs):
        imported.append(real(*args, **kwargs))
        return imported[-1]

    def refuse(name, sha1):
        raise NetworkError("the host said no")

    monkeypatch.setattr(g, "import_rom", record)
    monkeypatch.setattr(room, "join", refuse)
    with pytest.raises(NetworkError, match="host said no"):
        collab.join_room(g, f"http://192.168.1.20:6481/join/{code}#room={slug}", "kim", built,
                         log=lambda *_: None, client=client, start=False)
    assert imported  # the game was split before the host was asked...
    assert g.store.all("SELECT slug FROM projects") == []  # ...and the refused join left no project...
    assert not (g.workspace / imported[0]).exists()  # ...or its folder behind
    assert [m["name"] for m in room._members_now()] == []  # ...nor a member on the host


def test_join_room_says_when_the_room_gave_the_name_a_number(built, tmp_path):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path)
    sha1 = json.loads(host.project_row(slug)["config"])["rom"]["sha1"]
    token, _ = room.join("kim", sha1)
    client.get(f"/collab/whoami?room={slug}", headers={"authorization": f"Bearer {token}"})  # kim has been here
    g = RomNetwork(tmp_path / "again.db", tmp_path / "again")
    said = []
    gslug = collab.join_room(g, f"http://192.168.1.20:6481/join/{code}#room={slug}", "kim", built,
                             log=said.append, client=client, start=False)
    assert g.links[gslug].member == "kim2"
    assert any("name kim was taken in this room" in line and "kim2" in line for line in said), said


def test_join_room_does_not_say_the_name_was_taken_when_only_the_case_changed(built, tmp_path):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path)
    sha1 = json.loads(host.project_row(slug)["config"])["rom"]["sha1"]
    room.join("tiredunc", sha1)  # an empty member left by a join that stopped before its project was linked
    g = RomNetwork(tmp_path / "case.db", tmp_path / "case")
    said = []
    gslug = collab.join_room(g, f"http://192.168.1.20:6481/join/{code}#room={slug}", "TiredUnc", built,
                             log=said.append, client=client, start=False)
    assert g.links[gslug].member == "tiredunc"
    assert not any("taken in this room" in line for line in said), said


def test_a_fork_join_checks_the_name_before_asking_the_host():  # noqa: F811
    calls = []

    class NoHost:
        def post(self, *args, **kwargs):
            calls.append(1)
            raise AssertionError("posted a refused name")

    with pytest.raises(NetworkError, match="24 letters"):
        collab._join_fork(object(), NoHost(), "url", "code", {}, "a b", b"", lambda *_: None, None, False)
    assert calls == []


def test_reconnect_says_when_the_room_gave_the_name_a_number(built, tmp_path):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path)
    link = f"http://192.168.1.20:6481/join/{code}#room={slug}"
    g, gslug = _guest(tmp_path, "kim", link, built, client)
    room.remove("kim")  # the host no longer knows kim's token: joining again has to ask for the name
    said = []
    collab.reconnect(g, gslug, link, log=said.append, client=client, start=False)
    assert any("name kim was taken in this room" in line and "kim2" in line for line in said), said


def test_public_rooms_need_no_invite_code(built, tmp_path):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path, visibility="public")
    g, gslug = _guest(tmp_path, "pub", f"http://192.168.1.20:6481/join/open#room={slug}", built, client)
    assert [m["name"] for m in room.members()] == ["pub"]
    room.configure(visibility="private")  # back to private: the bare address isn't enough any more
    with pytest.raises(NetworkError, match="no longer valid"):
        _guest(tmp_path, "late", f"http://192.168.1.20:6481/join/open#room={slug}", built, client)


def test_the_host_lets_people_in_and_can_take_them_out(built, tmp_path):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path, approve=True)
    link = f"http://192.168.1.20:6481/join/{code}#room={slug}"
    g, gslug = _guest(tmp_path, "kim", link, built, client)
    assert [p["name"] for p in room.pending()] == ["kim"] and room.members() == []
    with pytest.raises(NetworkError, match="waiting for the host"):
        g.links[gslug].sync()
    room.approve("kim")
    g.links[gslug].sync()
    assert [m["name"] for m in room.members()] == ["kim"]
    room.remove("kim")
    with pytest.raises(NetworkError):
        g.links[gslug].sync()
    assert room.members() == []


def test_the_guests_screen_says_whether_it_waits_is_in_or_is_out(built, tmp_path):  # noqa: F811
    """A laptop that asked to join said "Room offline" beside an empty project: the host's answer (waiting to be let
    in) never reached its screen. A declined or removed guest was told "invalid API token"."""
    host, slug, room, code, client = _host(built, tmp_path, approve=True)
    g, gslug = _guest(tmp_path, "kim", f"http://192.168.1.20:6481/join/{code}#room={slug}", built, client)
    link = g.links[gslug]
    with pytest.raises(NetworkError):
        link.sync()
    assert g._room_info(gslug)["state"] == "waiting"
    room.approve("kim")
    link.sync()
    assert g._room_info(gslug)["state"] == "connected"
    room.remove("kim")
    with pytest.raises(NetworkError, match="not a member"):
        link.sync()
    assert g._room_info(gslug)["state"] == "out"


def test_a_new_guest_starts_at_the_newest_and_catches_up_page_by_page(built, tmp_path, monkeypatch):  # noqa: F811
    """The room's history landed in a new guest's feed (and its notifications) all at once, as if it had just happened,
    and again after every restart: the sync started from nothing each time. A new guest starts at the newest event;
    one that was away catches up on what it missed a page at a time, its screen counting it down."""
    host, slug, room, code, client = _host(built, tmp_path)
    for i in range(3):
        host.record_event(slug, "collab", f"before {i}")
    g, gslug = _guest(tmp_path, "sam", f"http://192.168.1.20:6481/join/{code}#room={slug}", built, client)
    assert g._room_info(gslug)["state"] == "syncing"  # nothing from the host yet
    g.links[gslug].sync()
    assert g._room_info(gslug)["state"] == "connected"
    assert not g.store.one("SELECT 1 FROM events WHERE detail LIKE 'before %'")  # history isn't news
    monkeypatch.setattr(collab, "EVENTS_PAGE", 2)
    for i in range(5):
        host.record_event(slug, "collab", f"away {i}")
    again = RomNetwork(tmp_path / "sam.db", tmp_path / "sam")  # sam's app starts again, without the link's thread
    link = again.link_project(gslug, json.loads(again.project_row(gslug)["config"])["link"], client=client, start=False)
    link.sync()
    st = again._room_info(gslug)
    assert (st["state"], st["received"], st["behind"]) == ("syncing", 2, 3)
    while link.behind:
        link.sync()
    assert again._room_info(gslug)["state"] == "connected"
    assert again.store.one("SELECT COUNT(*) AS n FROM events WHERE detail LIKE 'away %'")["n"] == 5  # each once


def test_a_git_projects_join_waits_to_be_let_in(built, tmp_path, monkeypatch):  # noqa: F811
    """Joining a git project's room asks for the host's files at once, which the host gives only to members: with
    "let people in" on, the join failed. It waits for the host now, and stops when the host says no."""
    import threading

    host, slug, room, code, client = _host(built, tmp_path, approve=True)
    monkeypatch.setattr(collab, "LET_IN_EVERY", 0.05)
    sha1 = json.loads(host.project_row(slug)["config"])["rom"]["sha1"]

    def ask(name):
        token = client.post(f"/collab/join?room={slug}", json={"invite": code, "name": name, "sha1": sha1}).json()["token"]
        return {"authorization": f"Bearer {token}", "x-room": slug}

    threading.Timer(0.2, room.approve, ("kim",)).start()
    collab._wait_to_be_let_in(client, "", slug, ask("kim"))  # returns once kim is let in
    assert [m["name"] for m in room.members()] == ["kim"]
    threading.Timer(0.2, room.approve, ("eve", False)).start()
    with pytest.raises(NetworkError, match="didn't let you in"):
        collab._wait_to_be_let_in(client, "", slug, ask("eve"))


def test_better_attempts_reach_the_host_without_a_compile_there(built, tmp_path):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path)
    g, gslug = _guest(tmp_path, "lee", f"http://192.168.1.20:6481/join/{code}#room={slug}", built, client)
    agent, _ = g.local_agent("claude-1")
    g.claim(agent, gslug, "func_800007F4")
    res = g.attempt(agent, "func_800007F4", "s32 func_800007F4(s32 v) { return v + 1; }", gslug)
    assert res["score"] and not res["matched"]
    for _ in range(50):  # reported from a background thread
        row = host.store.one("SELECT best_score, best_source FROM functions WHERE name='func_800007F4'")
        if row["best_score"] is not None:
            break
        __import__("time").sleep(0.1)
    assert row["best_score"] == res["score"] and "v + 1" in row["best_source"]
    assert host.store.one("SELECT via FROM attempts ORDER BY id DESC LIMIT 1")["via"] == "room"


def test_a_crowd_in_one_room(built, tmp_path):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path, visibility="public")
    link = f"http://192.168.1.20:6481/join/open#room={slug}"
    guests = [_guest(tmp_path, f"m{i}", link, built, client) for i in range(12)]
    agents = [g.local_agent("claude-1")[0] for g, _ in guests]
    claimed = [g.claim(a, s).splitlines()[0] for (g, s), a in zip(guests, agents)]
    assert len(set(claimed)) == len(claimed)  # never the same function twice
    members = room._members_now()  # members() is cached for a few seconds: everyone polls it
    assert len(members) == 12 and all(m["working"] for m in members)


def test_members_stay_in_the_room_when_their_app_restarts(built, tmp_path):  # noqa: F811
    host, slug, room, code, client = _host(built, tmp_path)
    g, gslug = _guest(tmp_path, "sam", f"http://192.168.1.20:6481/join/{code}#room={slug}", built, client)
    again = RomNetwork(tmp_path / "sam.db", tmp_path / "sam")  # the member's app starts again
    again.restore_links()
    assert gslug in again.links and again.links[gslug].member == "sam"
    again.links[gslug].client = client
    again.links[gslug].stop()
    again.links[gslug].sync()
    assert [m["name"] for m in room._members_now()] == ["sam"]
