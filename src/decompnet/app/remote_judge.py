"""Shared hosted queue for score-only background ROM batches."""

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)


class LocalVerifiers:
    """Start one local queue consumer per configured hosted service on demand."""

    def __init__(self, internal):
        self.internal = Path(internal)
        self.name = f"desktop-{uuid.uuid4().hex[:16]}"
        self.lock = threading.Lock()
        self.processes = {}
        self.closed = False

    def ensure(self, server, token):
        from . import mcp_gateway

        with self.lock:
            if self.closed:
                return
            self.processes = {key: child for key, child in self.processes.items() if child.poll() is None}
            key = (server, hashlib.sha256(token.encode()).hexdigest())
            if key in self.processes:
                return
            gateway = mcp_gateway.program()
            if gateway is None:
                return
            cache = self.internal / "verifier-packs" / hashlib.sha256(
                f"{server}/{key[1]}".encode()).hexdigest()
            logs = self.internal / "logs"
            try:
                logs.mkdir(parents=True, exist_ok=True)
                with (logs / "local-verifier.log").open("ab") as output:
                    child = subprocess.Popen(
                        [str(gateway), "verifier", "--server", server, "--name", self.name,
                         "--slots", "2", "--packs", str(cache), "--poll", "0.25", "--idle-exit", "20",
                         "--trials-only", "--parent-pipe", "--judge", sys.executable,
                         "--judge-arg", "-m", "--judge-arg", "decompnet.app.cli", "--judge-arg", "judge-worker"],
                        env=dict(os.environ, DECOMP_VERIFIER_TOKEN=token, DECOMP_WINDOW="0", DECOMP_TRAY="0"),
                        stdin=subprocess.PIPE, stdout=output, stderr=output,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                self.processes[key] = child
            except OSError:
                log.warning("Could not start a local verifier for the shared judge queue")

    def close(self):
        with self.lock:
            self.closed = True
            children = list(self.processes.values())
            self.processes.clear()
        for child in children:
            try:
                child.stdin.close()
            except OSError:
                pass
        for child in children:
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


class RemoteJudge:
    def __init__(self, settings, request, local_verifiers=None):
        slug = quote(settings.get("remote") or request["slug"], safe="")
        self.server = settings["server"].rstrip("/")
        self.url = f"{self.server}/v1/p/{slug}/judge-trials"
        self.local_verifiers = local_verifiers
        self.verifier_token = settings.get("verifier_token")
        self.token = settings["token"]
        self.request = {**request, "pack_sha256": settings["pack_sha256"]}

    @classmethod
    def configured(cls, net, slug, fn, request):
        path = os.environ.get("DECOMP_REMOTE_JUDGES")
        if not path or net is None:
            return None
        try:
            settings = json.loads(Path(path).read_text()).get(slug)
            if settings is None:
                return None
            header = net._context(fn["project_id"], fn["unit"])["header"]
            return cls(settings, {**request, "header_sha256": hashlib.sha256(header.encode()).hexdigest()},
                       getattr(net, "local_verifiers", None))
        except (OSError, KeyError, ValueError, TypeError, AttributeError):
            log.warning("Could not read remote judge settings")
            return None

    def batch(self, sources, check):
        """Return completed scores indexed by source position, including compile failures."""
        completed = {}
        try:
            check()
            with httpx.Client(headers={"Authorization": f"Bearer {self.token}"}, timeout=3) as client:
                def post(body):
                    response = client.post(self.url, json=body)
                    response.raise_for_status()
                    return response.json()

                ids = post({**self.request, "op": "enqueue", "sources": sources})["ids"]
                if len(ids) != len(sources):
                    return completed
                if self.local_verifiers is not None and self.verifier_token:
                    self.local_verifiers.ensure(self.server, self.verifier_token)
                pending = {job: index for index, job in enumerate(ids)}
                deadline = time.monotonic() + 15
                while pending and time.monotonic() < deadline:
                    check()
                    for result in post({"op": "results", "ids": list(pending)})["results"]:
                        job = result["id"]
                        if job not in pending or result["state"] == "reported":
                            continue
                        index = pending.pop(job)
                        if result["state"] == "verified":
                            completed[index] = result["score"]
                        elif result["state"] == "rejected" and result["score"] is None:
                            completed[index] = None
                    if pending:
                        time.sleep(.25)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            log.info("Remote judge unavailable. Continuing with local judges.")
        return completed
