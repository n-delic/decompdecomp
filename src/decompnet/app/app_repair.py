"""Turn a failed app job into a reviewable source patch."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from ..hostenv import NO_WINDOW, find_uv, stop_process_tree
from ..limits import capped
from ..network import NetworkError
from . import edition, window
from .agents import _which

KIND = "app-repair"
LOG_LIMIT = 16000
PATCH_LIMIT = 250000
MESSAGE_LIMIT = 4000


class RepairConversation:
    """Hold one follow-up at a time so patch review cannot race with an accepted message."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pending = None
        self.accepting = False
        self.transcript = []

    def publish(self, log, phase):
        log.progress({"phase": phase, "repair_messages": self.transcript.copy(),
                      "repair_accepting": self.accepting, "repair_pending": bool(self.pending)})

    def add(self, log, speaker, message):
        self.transcript = [*self.transcript[-29:], f"{speaker}: {message[-LOG_LIMIT:]}"]
        self.publish(log, "Codex is working in the repair worktree…")

    def agent_message(self, log, message):
        with self.lock:
            self.add(log, "Codex", message)

    def queue(self, message, job):
        with self.lock:
            if not self.accepting or self.pending:
                raise NetworkError("Codex cannot accept another message right now.")
            self.pending = message
            job["progress"] = {**job["progress"], "repair_pending": True}

    def next_prompt(self, log):
        with self.lock:
            message, self.pending = self.pending, None
            if message:
                self.add(log, "You", message)
            else:
                self.accepting = False
                self.publish(log, "Preparing patch for review…")
            return message

    def close(self, log):
        with self.lock:
            self.accepting = False
            if self.pending:
                self.add(log, "Undelivered message", self.pending)
                self.pending = None
            self.publish(log, "Preparing patch for review…")


def git(root, *args, patch=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", *args], cwd=root, env=env, input=patch or b"",
        capture_output=True, timeout=60, creationflags=NO_WINDOW,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()[-1200:]
        raise NetworkError(f"Git could not complete the repair operation. {detail}")
    return result.stdout


def available():
    return edition.DEV and not os.environ.get("DECOMP_PACKAGE")


def desktop(request, app):
    return secrets.compare_digest(window.request_token(request), app.secret)


def checkout():
    if not available():
        raise NetworkError("Run decompdecomp from a Git checkout to propose an app repair.")
    root = edition.CHECKOUT_ROOT
    if not shutil.which("git"):
        raise NetworkError("Install Git to repair app source.")
    return root


class AppRepair:
    def __init__(self, jobs):
        self.jobs = jobs
        self.apply_lock = threading.Lock()
        self.conversations = {}

    def preview(self, source):
        checkout()
        failed = self.jobs.jobs.get(source)
        if not failed or failed["state"] != "error" or failed["kind"] == KIND:
            raise NetworkError("Choose a failed app job to investigate.")
        # The generic job runner appends all recent app activity. Send only the selected job's diagnostics.
        lines = failed["log"]
        if "Recent application log:" in lines:
            lines = lines[:lines.index("Recent application log:")]
        excerpt = "\n".join(lines)[-LOG_LIMIT:]
        log = f"{failed['error'] or 'Job failed.'}\n{excerpt}"[:LOG_LIMIT]
        return {"log": log, "repair": next((job["id"] for job in reversed(self.jobs.jobs.copy().values())
                                          if job.get("repair_source") == source), None)}

    def start(self, source, diagnostics):
        from ..agents import get

        root = checkout()
        connector = get("codex")
        exe = _which(connector.command)
        if not exe:
            raise NetworkError("Install and sign in to Codex CLI to repair app source.")
        if not isinstance(diagnostics, str) or not diagnostics.strip() or len(diagnostics) > LOG_LIMIT:
            raise NetworkError("Enter a diagnostic excerpt of up to 16,000 characters.")
        self.preview(source)  # Validate the failed job and check the checkout before reserving work.
        with self.jobs._lock:
            existing = next((job["id"] for job in reversed(self.jobs.jobs.values())
                             if job.get("repair_source") == source), None)
            if existing and self.jobs.jobs[existing]["state"] in {"running", "done"}:
                return existing
            conversation = RepairConversation()
            jid = self.jobs.start(
                KIND, lambda log: propose(root, connector, exe, diagnostics, log, conversation),
                include_recent_log=False, cancel_message="App repair cancelled.",
            )
            self.conversations[jid] = conversation
            self.jobs.jobs[jid]["repair_source"] = source
            return jid

    def message(self, jid, message):
        if not isinstance(message, str) or not message.strip() or len(message) > MESSAGE_LIMIT:
            raise NetworkError("Enter a message of up to 4,000 characters.")
        with self.jobs._lock:
            job = self.jobs.jobs.get(jid)
            conversation = self.conversations.get(jid)
            if not conversation or job["state"] != "running" or self.jobs.cancel_events[jid].is_set():
                raise NetworkError("This repair is no longer taking messages.")
            conversation.queue(message.strip(), job)

    def apply(self, jid):
        root = checkout()
        with self.apply_lock:
            self.jobs.check_start()
            job = self.jobs.jobs.get(jid)
            if not job or job["kind"] != KIND or job["state"] != "done" or not job["result"]["patch"]:
                raise NetworkError("Review a completed app repair before applying it.")
            result = job["result"]
            if result.get("applied"):
                return
            if git(root, "rev-parse", "HEAD").decode().strip() != result["base"] or git(
                root, "status", "--porcelain", "--untracked-files=all"
            ):
                raise NetworkError("The source checkout has changed. Restore the reviewed revision and a clean checkout.")
            git(root, "apply", "--index", "--whitespace=nowarn", patch=result["patch"].encode())
            result["applied"] = True


def propose(root, connector, exe, diagnostics, log, conversation):
    log("Preparing a separate worktree from the app revision…")
    base = git(root, "rev-parse", "HEAD").decode().strip()
    with tempfile.TemporaryDirectory(prefix="decomp-app-repair-") as folder:
        scratch = Path(folder)
        repository, worktree = scratch / "repository", scratch / "worktree"
        # Keep Git metadata separate too. The agent's repository shares no refs with the running checkout.
        git(scratch, "clone", "--quiet", "--no-local", "--no-checkout", "--", str(root), str(repository))
        git(repository, "worktree", "add", "--quiet", "--detach", str(worktree), base)
        home = worktree / ".repair-test-home"
        home.mkdir()
        prepare_tests(worktree, log)
        log("Codex is investigating, patching and testing in the worktree…")
        with conversation.lock:
            conversation.accepting = True
            conversation.publish(log, "Codex is working in the repair worktree…")
        try:
            summary = connector.repair_source(
                exe, worktree, home, diagnostics, log.cancel_event,
                lambda message: conversation.agent_message(log, message),
                lambda: conversation.next_prompt(log),
            )
        finally:
            conversation.close(log)
        log.check_cancel()
        git(worktree, "add", "--all", "--", ".", ":(exclude).repair-test-home", ":(exclude).repair-test-home/**")
        raw = git(worktree, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--full-index",
                  "--no-renames", "--no-color", base, "--")
        stats = git(worktree, "diff", "--cached", "--numstat", "--no-renames", base, "--")
        modes = git(worktree, "diff", "--cached", "--raw", "--no-renames", base, "--")
        if (len(raw) > PATCH_LIMIT or any(line.startswith(b"-\t-\t") for line in stats.splitlines())
                or any(set(line.split(maxsplit=2)[:2]) & {b":120000", b":160000", b"120000", b"160000"}
                       for line in modes.splitlines())):
            raise NetworkError("The patch is too large or contains binary files, links or submodules. Make a smaller text patch.")
        try:
            patch = raw.decode()
        except UnicodeDecodeError as error:
            raise NetworkError("The proposed changes include text that cannot be shown in the diff.") from error
        log("Patch ready to review." if patch else "Codex returned without a source patch.")
        return {"base": base, "patch": patch, "summary": summary}


def prepare_tests(worktree, log):
    """Reuse cached dependencies from the trusted base before the agent edits any code."""
    uv = find_uv()
    if not uv:
        log("uv is unavailable. Codex will report any checks it can run.")
        return
    env = {key: value for key, value in os.environ.items() if not key.startswith(("UV_", "GIT_", "DECOMP"))}
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(key, None)
    env["UV_PROJECT_ENVIRONMENT"] = str(worktree / ".venv")
    log("Preparing an isolated test environment from cached dependencies…")
    with subprocess.Popen(capped([uv, "sync", "--locked", "--offline"]), cwd=worktree, env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW) as proc:
        try:
            for _ in range(240):
                if proc.poll() is not None:
                    break
                if log.cancel_event.wait(.25):
                    log.check_cancel()
        finally:
            stop_process_tree(proc)
        if proc.returncode:
            log("Cached dependencies could not prepare the test environment. Review Codex’s test report for gaps.")
