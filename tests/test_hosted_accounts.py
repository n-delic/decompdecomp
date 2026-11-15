"""Who may use a hosted server: removing users, replacing tokens, invite-only projects and per-user rates
(gateway/src/hosted.rs)."""
import json
import subprocess

import httpx
import pytest

from decompnet import hosted_export
from decompnet.app import mcp_gateway
from tests.test_hosted import gateway, project

pytestmark = pytest.mark.skipif(mcp_gateway.built() is None,
                                reason="the gateway isn't built (cargo build --release in gateway/)")


def run(folder, *args):
    return subprocess.run(gateway(*args, "--dir", str(folder)), capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def server(tmp_path, request):
    net = project(tmp_path)
    folder = tmp_path / "hosted"
    for slug in ("game", "other"):
        hosted_export.export(net, "game", folder / "projects" / f"{slug}.sqlite3", log=lambda _: None)
    admin = run(folder, "add-user", "--name", "root", "--role", "admin")
    rate = getattr(request, "param", 600)
    process = subprocess.Popen(gateway("serve", "--dir", str(folder), "--listen", "127.0.0.1:0", "--rate", str(rate)),
                               stdout=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{int(process.stdout.readline().split()[1])}"
    as_admin = {"Authorization": f"Bearer {admin}"}

    def user(name, role="agent"):
        reply = httpx.post(f"{base}/v1/users", json={"name": name, "role": role}, headers=as_admin)
        reply.raise_for_status()
        return reply.json()["token"]
    yield base, as_admin, user, folder
    process.terminate()
    process.wait(5)


def call(base, token, slug="game", name="project_info"):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
    return httpx.post(f"{base}/p/{slug}/mcp", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=10)


def test_invite_only_projects(server):
    base, as_admin, user, _ = server
    alice = user("alice")
    assert call(base, alice).status_code == 200  # open by default
    assert httpx.post(f"{base}/v1/policy", json={"access": "invite"}, headers=as_admin).json() == {"access": "invite"}
    assert call(base, alice).status_code == 403
    httpx.post(f"{base}/v1/users/alice/access", json={"project": "game"}, headers=as_admin).raise_for_status()
    assert call(base, alice).status_code == 200 and call(base, alice, "other").status_code == 403
    mine = httpx.get(f"{base}/v1/projects", headers={"Authorization": f"Bearer {alice}"}).json()
    assert mine == {"projects": ["game"]}
    listed = httpx.get(f"{base}/v1/users", headers=as_admin).json()
    assert listed["access"] == "invite" and {u["name"]: u["projects"] for u in listed["users"]}["alice"] == ["game"]
    httpx.post(f"{base}/v1/users/alice/access", json={"project": "game", "allow": False}, headers=as_admin)
    assert call(base, alice).status_code == 403
    # A verifier is given only the projects granted to it.
    checker = user("checker", "verifier")
    httpx.post(f"{base}/v1/users/checker/access", json={"project": "other"}, headers=as_admin)
    registered = httpx.post(f"{base}/v1/verifiers", json={"name": "box", "projects": ["game", "other"]},
                            headers={"Authorization": f"Bearer {checker}"}).json()
    assert registered["projects"] == ["other"]


def test_removed_users_and_replaced_tokens_stop_working(server):
    base, as_admin, user, _ = server
    bob, carol = user("bob"), user("carol")
    rotated = httpx.post(f"{base}/v1/users/bob/token", headers=as_admin).json()["token"]
    assert call(base, bob).status_code == 401 and call(base, rotated).status_code == 200
    assert httpx.delete(f"{base}/v1/users/carol", headers=as_admin).json() == {"removed": "carol"}
    assert call(base, carol).status_code == 401
    assert httpx.delete(f"{base}/v1/users/carol", headers=as_admin).status_code == 404
    # Only admins manage users.
    assert httpx.get(f"{base}/v1/users", headers={"Authorization": f"Bearer {rotated}"}).status_code == 403
    assert httpx.post(f"{base}/v1/policy", json={"access": "invite"},
                      headers={"Authorization": f"Bearer {rotated}"}).status_code == 403


@pytest.mark.parametrize("server", [5], indirect=True)
def test_a_user_has_a_rate_of_calls(server):
    base, _, user, _ = server
    dave, erin = user("dave"), user("erin")
    replies = [call(base, dave).json()["result"] for _ in range(7)]
    assert all(not r["isError"] for r in replies[:5])
    assert replies[5]["isError"] and "Too many calls" in replies[5]["content"][0]["text"]
    assert not call(base, erin).json()["result"]["isError"]  # each user has their own


def test_the_command_line_manages_users(tmp_path):
    folder = tmp_path / "hosted"
    token = run(folder, "add-user", "--name", "frank")
    assert token.startswith("dd_")
    assert run(folder, "rotate-token", "--name", "frank") != token
    assert run(folder, "policy", "--access", "invite") == "access: invite"
    assert "frank may use game" == run(folder, "grant", "--name", "frank", "--project", "game")
    users = json.loads(run(folder, "list-users"))
    assert users["access"] == "invite" and users["users"][0]["projects"] == ["game"]
    assert run(folder, "remove-user", "--name", "frank") == "removed frank"
    assert json.loads(run(folder, "list-users"))["users"] == []
    failed = subprocess.run(gateway("remove-user", "--name", "frank", "--dir", str(folder)), capture_output=True, text=True)
    assert failed.returncode == 1 and "no user" in failed.stderr
