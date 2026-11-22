"""App repair reviews an immutable patch built outside the running source tree."""

import shutil
import threading
import time

import pytest

from decompnet.app import app_repair, edition
from decompnet.app.support import Jobs
from decompnet.network import NetworkError

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="Git is required")


@pytest.fixture
def repair(tmp_path, monkeypatch):
    git = app_repair.git
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    (root / "app.py").write_text("bug\n")
    git(root, "add", "app.py")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Base")
    monkeypatch.setattr(edition, "DEV", True)
    monkeypatch.setattr(edition, "CHECKOUT_ROOT", root)
    monkeypatch.delenv("DECOMP_PACKAGE", raising=False)
    monkeypatch.setattr(app_repair, "find_uv", lambda: None)
    jobs = Jobs()
    jobs.jobs["failed"] = {"kind": "build", "state": "error", "error": "Failed to build.",
                           "log": ["failure details", "Recent application log:", "unrelated project log"]}
    return app_repair.AppRepair(jobs), root


def test_repair_keeps_source_untouched_until_review(repair, monkeypatch):
    service, root = repair
    monkeypatch.setattr(app_repair, "_which", lambda _: "stub")
    patched = threading.Event()
    finish = threading.Event()

    def fake_agent(self, exe, worktree, home, diagnostics, cancel, on_message, next_prompt):
        assert worktree != root and home.parent == worktree
        assert diagnostics == "Reviewed diagnostic."
        assert (worktree / "app.py").read_text() == "bug\n"
        assert app_repair.git(worktree, "rev-parse", "--git-common-dir").decode().strip() != str(root / ".git")
        (worktree / "app.py").write_text("fix\n")
        (worktree / "test_app.py").write_text("assert True\n")
        (home / "scratch-data").write_text("private\n")
        on_message("I reproduced the failure.")
        patched.set()
        assert finish.wait(10)
        assert next_prompt() == "Run the focused regression."
        assert next_prompt() is None
        return "Regression test passed."

    monkeypatch.setattr("decompnet.agents.codex.Codex.repair_source", fake_agent)
    preview = service.preview("failed")
    assert preview["log"] == "Failed to build.\nfailure details"
    jid = service.start("failed", "Reviewed diagnostic.")
    try:
        assert patched.wait(10)
        assert service.start("failed", "Reviewed diagnostic.") == jid
        assert service.preview("failed")["repair"] == jid
        assert app_repair.recent_repairs(service.jobs) == [{
            "source": "failed", "task": "build", "started": service.jobs.jobs[jid]["t"], "state": "running",
        }]
        assert service.jobs.jobs[jid]["progress"]["repair_messages"] == ["Codex: I reproduced the failure."]
        service.message(jid, "Run the focused regression.")
        assert service.jobs.jobs[jid]["progress"]["repair_pending"]
        with pytest.raises(NetworkError, match="cannot accept"):
            service.message(jid, "Duplicate message.")
        assert (root / "app.py").read_text() == "bug\n"
        with pytest.raises(NetworkError, match="Review a completed"):
            service.apply(jid)
    finally:
        finish.set()
    job = service.jobs.jobs[jid]
    deadline = time.monotonic() + 10
    while job["state"] == "running" and time.monotonic() < deadline:
        time.sleep(.01)
    assert job["state"] == "done", job.get("error")
    assert app_repair.recent_repairs(service.jobs)[0]["state"] == "done"
    assert service.preview("failed")["repair"] == jid
    assert "You: Run the focused regression." in job["progress"]["repair_messages"]
    with pytest.raises(NetworkError, match="no longer taking messages"):
        service.message(jid, "Too late.")
    assert "+fix" in job["result"]["patch"] and "test_app.py" in job["result"]["patch"]
    assert "scratch-data" not in job["result"]["patch"]
    assert job["result"]["summary"] == "Regression test passed."
    (root / "app.py").write_text("local edit\n")
    with pytest.raises(NetworkError, match="checkout has changed"):
        service.apply(jid)
    assert (root / "app.py").read_text() == "local edit\n"
    (root / "app.py").write_text("bug\n")
    service.apply(jid)
    service.apply(jid)
    assert (root / "app.py").read_text() == "fix\n"
    assert app_repair.git(root, "diff", "--cached", "--name-only").splitlines() == [b"app.py", b"test_app.py"]


def test_standalone_success_and_repair_failures_are_ineligible(repair, monkeypatch):
    service, _ = repair
    service.jobs.jobs["success"] = {"kind": "build", "state": "done"}
    service.jobs.jobs["repair"] = {"kind": app_repair.KIND, "state": "error"}
    for jid in ("missing", "success", "repair"):
        with pytest.raises(NetworkError, match="Choose a failed"):
            service.preview(jid)
    monkeypatch.setenv("DECOMP_PACKAGE", "macos")
    with pytest.raises(NetworkError, match="Git checkout"):
        service.preview("failed")


def test_codex_repair_reads_edited_diagnostics_and_returns_test_report(tmp_path):
    import os
    import sys

    from decompnet.agents.codex import Codex

    if os.name == "nt":
        pytest.skip("The stub is a POSIX executable")
    worktree = tmp_path / "worktree"
    home = worktree / ".repair-test-home"
    home.mkdir(parents=True)
    executable = tmp_path / "fake-codex"
    executable.write_text(f"""#!{sys.executable}
import json
import sys
from pathlib import Path

assert "--ignore-user-config" in sys.argv and "workspace-write" in sys.argv
assert "sandbox_workspace_write.network_access=false" in sys.argv
prompt = sys.stdin.read()
session = "b1ed0b1a-9649-4eca-b0fb-6a8645dc9ce3"
if "resume" in sys.argv:
    assert sys.argv[sys.argv.index("resume") + 1] == session
    assert "Check the edge case." in prompt
    text = "Checked the edge case."
else:
    assert json.dumps("Edited excerpt.") in prompt
    text = "I found the bug."
print(json.dumps(dict(type="thread.started", thread_id=session)), flush=True)
print(json.dumps(dict(type="item.completed", item=dict(type="agent_message", text=text))), flush=True)
Path("regression.py").write_text("assert 2 + 2 == 4")
exec(Path("regression.py").read_text())
Path(sys.argv[sys.argv.index("-o") + 1]).write_text("regression.py passed.")
""")
    executable.chmod(0o700)
    messages = []
    guidance = iter(["Check the edge case.", None])
    result = Codex().repair_source(
        str(executable), worktree, home, "Edited excerpt.", threading.Event(), messages.append, lambda: next(guidance),
    )
    assert messages == ["I found the bug.", "Checked the edge case."]
    assert result == "regression.py passed."
    assert (worktree / "regression.py").is_file()
