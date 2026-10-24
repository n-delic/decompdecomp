"""Rooms: a host and a guest, each with their own copy of Demo Quest 64, working together."""

import json
import re
import shutil

import pytest
from starlette.testclient import TestClient

from decompnet import toolchains
from decompnet.app import collab
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError

from .test_app_engine import CLAMP, DAMAGE
from .test_rom import built  # noqa: F401

pytestmark = pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None or not shutil.which("ld.lld"),
                                reason="needs gcc-2.7.2-kmc")


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


def test_join_shares_stronger_saved_progress(built, tmp_path):  # noqa: F811
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

    joined = collab.join_room(guest, link, "bea", built, log=lambda *_: None, client=client, start=False)

    assert joined == gslug
    assert host._function("func_800007D0", slug)["status"] == "matched"
    received = host._function("func_800007F4", slug)
    assert received["best_score"] == best["score"]
    assert "v + 1" in received["best_source"]


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
