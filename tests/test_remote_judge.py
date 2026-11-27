import json
from types import SimpleNamespace

import httpx

from decompnet.app import judge_pool, remote_judge


def test_background_batch_merges_remote_and_local_results_in_source_order(monkeypatch, tmp_path):
    config = tmp_path / "judges.json"
    config.write_text(json.dumps({"game": {"server": "https://example.test", "remote": "published",
                                          "token": "test", "verifier_token": "worker", "pack_sha256": "a" * 64}}))
    monkeypatch.setenv("DECOMP_REMOTE_JUDGES", str(config))
    requests = []

    def respond(request):
        assert request.url.path == "/v1/p/published/judge-trials"
        assert request.headers["Authorization"] == "Bearer test"
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["op"] == "enqueue":
            return httpx.Response(200, json={"ids": [4, 5, 6]})
        return httpx.Response(200, json={"results": [
            {"id": 6, "state": "verified", "score": 0},
            {"id": 5, "state": "unverifiable", "score": None},
            {"id": 4, "state": "rejected", "score": None},
        ]})

    client = httpx.Client
    monkeypatch.setattr(remote_judge.httpx, "Client",
                        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    started = []
    net = SimpleNamespace(_context=lambda *_: {"header": "typedef int integer;"},
                          local_verifiers=SimpleNamespace(ensure=lambda *args: started.append(args)))
    fn = {"name": "f", "target": b"bytes", "vram": 0, "unit": "", "project_id": 1}
    with judge_pool.JudgePool("game", fn, "ido", ["-O2"], net=net) as pool:
        local = []
        monkeypatch.setattr(pool, "judge", lambda source: local.append(source) or 7)
        assert pool.judge_many(["invalid", "fallback", "exact"]) == [None, 7, 0]
    assert local == ["fallback"]
    assert started == [("https://example.test", "worker")]
    assert requests[0]["sources"] == ["invalid", "fallback", "exact"]
    assert requests[0]["function"]["target"] == b"bytes".hex()
    assert requests[0]["compiler"] == {"toolchain": "ido", "flags": ["-O2"]}
    assert requests[0]["header_sha256"]


def test_local_verifier_is_reused_and_stops_with_the_desktop(monkeypatch, tmp_path):
    from unittest.mock import Mock

    from decompnet.app import mcp_gateway

    child = Mock()
    child.poll.return_value = None
    launch = Mock(return_value=child)
    monkeypatch.setattr(mcp_gateway, "program", lambda: "/gateway")
    monkeypatch.setattr(remote_judge.subprocess, "Popen", launch)
    local = remote_judge.LocalVerifiers(tmp_path)
    local.ensure("https://example.test", "verifier credential")
    local.ensure("https://example.test", "verifier credential")
    launch.assert_called_once()
    command = launch.call_args.args[0]
    assert command[:4] == ["/gateway", "verifier", "--server", "https://example.test"]
    assert "--trials-only" in command and "--parent-pipe" in command
    assert "verifier credential" not in command
    assert launch.call_args.kwargs["env"]["DECOMP_VERIFIER_TOKEN"] == "verifier credential"
    local.close()
    child.stdin.close.assert_called_once()
    child.wait.assert_called_once_with(timeout=2)
    local.ensure("https://example.test", "verifier credential")
    launch.assert_called_once()
