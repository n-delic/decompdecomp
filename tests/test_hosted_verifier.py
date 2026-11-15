"""Verifiers: compiler servers that register with a hosted server, lease submissions, judge them and post verdicts
(gateway/src/verifier.rs), with decompdecomp's judge behind them (hosted_judge.py)."""
import json
import subprocess
import sys
import textwrap
import threading
from types import SimpleNamespace

import httpx
import pytest

from decompnet import hosted_export, hosted_judge
from decompnet.app import mcp_gateway
from tests.test_hosted import gateway, project, tool

pytestmark = pytest.mark.skipif(mcp_gateway.built() is None,
                                reason="the gateway isn't built (cargo build --release in gateway/)")

# A stand-in judge speaking hosted_judge.py's lines: "MATCH" in the C matches, "BROKEN" doesn't compile, "ELSEWHERE"
# can't be judged on this computer, anything else scores 7. Each judged submission is logged to see none twice.
JUDGE = textwrap.dedent("""
    import json, sys
    log = open(sys.argv[1], "a")
    for line in sys.stdin:
        r = json.loads(line)
        if r["op"] == "describe":
            out = {"projects": ["game"], "toolchains": ["gcc-2.7.2-kmc", "ido-7.1"], "version": "test"}
        else:
            log.write(r["source"] + "\\n"); log.flush()
            s = r["source"]
            out = ({"error": "another build of the game"} if "ELSEWHERE" in s else
                   {"score": None, "detail": "syntax error"} if "BROKEN" in s else
                   {"score": 0 if "MATCH" in s else 7, "detail": ""})
        print(json.dumps(out), flush=True)
""")


@pytest.fixture
def server(tmp_path):
    net = project(tmp_path)
    folder = tmp_path / "hosted"
    hosted_export.export(net, "game", folder / "projects" / "game.sqlite3", log=lambda _: None)
    admin = subprocess.run(gateway("add-user", "--dir", str(folder), "--name", "root", "--role", "admin"),
                           capture_output=True, text=True, check=True).stdout.strip()
    process = subprocess.Popen(gateway("serve", "--dir", str(folder), "--listen", "127.0.0.1:0"),
                               stdout=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{int(process.stdout.readline().split()[1])}"

    def user(name, role):
        reply = httpx.post(f"{base}/v1/users", json={"name": name, "role": role},
                           headers={"Authorization": f"Bearer {admin}"})
        reply.raise_for_status()
        return reply.json()["token"]
    yield SimpleNamespace(base=base, admin=admin, user=user, tmp=tmp_path)
    process.terminate()
    process.wait(5)


def verifier(server, token, name):
    script = server.tmp / "judge.py"
    script.write_text(JUDGE)
    return subprocess.Popen(
        [str(mcp_gateway.built()), "verifier", "--server", server.base, "--token", token, "--name", name,
         "--slots", "3", "--poll", "0.2", "--idle-exit", "2", "--judge", sys.executable,
         "--judge-arg", str(script), "--judge-arg", str(server.tmp / "judged.log")],
        stderr=subprocess.PIPE, text=True)


def submit(server, agent, function, source):
    receipt, error = tool(server.base, agent, "claim_function", function=function)
    assert not error, receipt
    token = next(line.split(": ")[1] for line in receipt.splitlines() if line.startswith("claim_token: "))
    score = 0 if "MATCH" in source or "ELSEWHERE" in source or "BROKEN" in source else 7
    assert not tool(server.base, agent, "submit_result", function=function, claim_token=token, source=source,
                    score=score)[1]


def test_two_verifiers_judge_every_submission_once(server):
    agent = server.user("alice", "agent")
    plan = {"f0": "int f0(void){return 0;} /* MATCH */", "f1": "int f1(void){return 1;}",
            "f2": "int f2( /* BROKEN */", "f3": "int f3(void){} /* ELSEWHERE */"}
    plan |= {f"f{k}": f"int f{k}(void){{return {k};}} /* MATCH */" for k in range(4, 16)}
    for function, source in plan.items():
        submit(server, server.user(f"agent-{function}", "agent"), function, source)  # (at most 6 claims each)
    # Compiler servers are registered by an admin, over HTTP.
    verifiers = [verifier(server, server.user(name, "verifier"), name) for name in ("mac-studio", "linux-box")]
    for process in verifiers:
        assert process.wait(60) == 0, process.stderr.read()
    listed = httpx.get(f"{server.base}/v1/verifiers", headers={"Authorization": f"Bearer {server.admin}"}).json()
    assert sorted(v["name"] for v in listed["verifiers"]) == ["linux-box", "mac-studio"]
    assert listed["verifiers"][0]["toolchains"] == ["gcc-2.7.2-kmc", "ido-7.1"]
    judged = (server.tmp / "judged.log").read_text().splitlines()
    others = [line for line in judged if "ELSEWHERE" not in line]
    assert len(others) == len(set(others)) == len(plan) - 1  # each once
    assert sum("ELSEWHERE" in line for line in judged) == 3  # tried three times, then given up
    info, _ = tool(server.base, agent, "project_info")
    assert "14/30 functions matched" in info  # f29 was matched already, f0 and f4..f15 now
    summary = tool(server.base, agent, "get_function", function="f1")[0]
    assert "best verified score 7" in summary
    assert "best verified score" not in tool(server.base, agent, "get_function", function="f2")[0]


def test_a_verifier_needs_a_verifier_token(server):
    agent = server.user("bob", "agent")
    reply = httpx.post(f"{server.base}/v1/verifiers", json={"name": "x", "projects": ["game"]},
                       headers={"Authorization": f"Bearer {agent}"})
    assert reply.status_code == 403
    assert httpx.post(f"{server.base}/v1/users", json={"name": "eve", "role": "admin"},
                      headers={"Authorization": f"Bearer {agent}"}).status_code == 403


# ------------------------------------------------------------------------------------------------- the judge itself

def test_the_judge_uses_this_computers_copy_only_when_it_is_the_same_game(tmp_path, monkeypatch):
    net = project(tmp_path)
    calls = []

    def judge_with(proj, fn, toolchain, flags, source):
        calls.append((fn["name"], toolchain, flags))
        if "MATCH" in source:
            return True, None, ""
        if "BROKEN" in source:
            return False, None, "error: expected ';'"
        return False, SimpleNamespace(score=12), ""
    monkeypatch.setattr(net, "_judge_with", judge_with)
    good = {"name": "f1", "vram": 0x80001100, "target": bytes([1, 3, 0xE0, 8]).hex()}
    compiler = {"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"]}
    ask = lambda function, source, slug="game": hosted_judge.judge(  # noqa: E731
        net, {"slug": slug, "function": function, "compiler": compiler, "source": source})
    assert ask(good, "MATCH") == {"score": 0, "detail": "byte-identical"}
    assert ask(good, "close")["score"] == 12
    assert ask(good, "BROKEN") == {"score": None, "detail": "error: expected ';'"}
    assert calls[0] == ("f1", "gcc-2.7.2-kmc", ["-O2"])
    assert "another build" in ask(good | {"target": "00000000"}, "MATCH")["error"]
    assert "no project" in ask(good, "MATCH", slug="other")["error"]
    assert "0 functions" in ask(good | {"vram": 1}, "MATCH")["error"]
    assert len(calls) == 3  # nothing compiled for what can't be judged here


def test_the_judge_speaks_one_line_per_request(tmp_path, monkeypatch):
    net = project(tmp_path)
    monkeypatch.setattr(hosted_judge, "describe", lambda net: {"projects": ["game"], "toolchains": [], "version": "x"})
    out = []
    lines = [json.dumps({"op": "describe"}) + "\n", "not json\n", json.dumps({"op": "nope"}) + "\n"]
    writer = SimpleNamespace(write=out.append, flush=lambda: None)
    thread = threading.Thread(target=hosted_judge.serve, args=(net, lines, writer))
    thread.start()
    thread.join(10)
    replies = [json.loads(line) for line in out]
    assert replies[0]["projects"] == ["game"] and "error" in replies[1] and "unknown op" in replies[2]["error"]
