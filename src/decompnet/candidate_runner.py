"""Parallel candidate compilation/evaluation coordinator.

Dispatches a batch of candidate compile/command jobs to the Rust
`decomp-mcp-gateway candidate-runner` subcommand when available, and falls back
seamlessly to a Python `ThreadPoolExecutor` + `subprocess.run` implementation
when the gateway binary is unavailable or disabled.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from decompnet.app import mcp_gateway
from decompnet.hostenv import NO_WINDOW


@dataclass(frozen=True)
class SourceFile:
    path: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "content": self.content}


@dataclass(frozen=True)
class CandidateJob:
    argv: list[str]
    id: str | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    stdin: str | None = None
    source_file: SourceFile | None = None
    timeout_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"argv": list(self.argv)}
        if self.id is not None:
            payload["id"] = self.id
        if self.cwd is not None:
            payload["cwd"] = self.cwd
        if self.env:
            payload["env"] = dict(self.env)
        if self.stdin is not None:
            payload["stdin"] = self.stdin
        if self.source_file is not None:
            payload["source_file"] = self.source_file.to_dict()
        if self.timeout_ms is not None:
            payload["timeout_ms"] = int(self.timeout_ms)
        return payload


@dataclass(frozen=True)
class CandidateResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    elapsed_ms: int
    id: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateResult:
        return cls(
            id=data.get("id"),
            exit_code=data.get("exit_code"),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            timed_out=bool(data.get("timed_out", False)),
            elapsed_ms=int(data.get("elapsed_ms", 0)),
            error=data.get("error"),
        )


def _normalize_job(job: CandidateJob | dict[str, Any]) -> dict[str, Any]:
    if isinstance(job, CandidateJob):
        return job.to_dict()
    return dict(job)


def _run_one_python(job: dict[str, Any]) -> CandidateResult:
    started = time.monotonic()
    job_id = job.get("id")
    argv = list(job.get("argv") or [])
    if not argv:
        return CandidateResult(
            id=job_id,
            exit_code=None,
            stdout="",
            stderr="",
            timed_out=False,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error="empty argv",
        )

    source_file = job.get("source_file")
    if source_file:
        try:
            path = Path(source_file["path"])
            if path.parent and str(path.parent) not in ("", "."):
                path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source_file["content"], encoding="utf-8")
        except OSError as exc:
            return CandidateResult(
                id=job_id,
                exit_code=None,
                stdout="",
                stderr="",
                timed_out=False,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error=f"failed to write source file {source_file.get('path')}: {exc}",
            )

    env = None
    if job.get("env"):
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in job["env"].items()})

    timeout_ms = job.get("timeout_ms")
    timeout_s = (timeout_ms / 1000.0) if timeout_ms is not None else None
    stdin_text = job.get("stdin")

    try:
        proc = subprocess.run(
            argv,
            cwd=job.get("cwd"),
            env=env,
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
            creationflags=NO_WINDOW,
        )
        return CandidateResult(
            id=job_id,
            exit_code=proc.returncode,
            stdout=proc.stdout.decode("utf-8", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
            timed_out=False,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return CandidateResult(
            id=job_id,
            exit_code=None,
            stdout=out,
            stderr=err,
            timed_out=True,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except OSError as exc:
        return CandidateResult(
            id=job_id,
            exit_code=None,
            stdout="",
            stderr="",
            timed_out=False,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=f"failed to spawn {argv[0]}: {exc}",
        )


def _run_batch_python(
    jobs: list[dict[str, Any]],
    *,
    max_concurrency: int | None = None,
) -> list[CandidateResult]:
    workers = max_concurrency if (max_concurrency and max_concurrency > 0) else (os.cpu_count() or 4)
    workers = max(1, min(workers, len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_run_one_python, jobs))


def _run_batch_rust(
    exe: Path,
    jobs: list[dict[str, Any]],
    *,
    max_concurrency: int | None = None,
) -> list[CandidateResult] | None:
    payload: dict[str, Any] = {"jobs": jobs}
    if max_concurrency is not None and max_concurrency > 0:
        payload["max_concurrency"] = int(max_concurrency)
    try:
        proc = subprocess.run(
            [str(exe), "candidate-runner"],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            creationflags=NO_WINDOW,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout.decode("utf-8"))
        results = data.get("results")
        if not isinstance(results, list) or len(results) != len(jobs):
            return None
        return [CandidateResult.from_dict(item) for item in results]
    except (UnicodeDecodeError, ValueError, TypeError):
        return None


def run_batch(
    jobs: list[CandidateJob | dict[str, Any]],
    *,
    max_concurrency: int | None = None,
    use_rust: bool | None = None,
) -> list[CandidateResult]:
    """Run a batch of candidate jobs in parallel, returning results in input order.

    Uses `decomp-mcp-gateway candidate-runner` when available (unless `use_rust=False`),
    falling back to Python `ThreadPoolExecutor` + `subprocess.run` otherwise.
    """
    if not jobs:
        return []
    normalized = [_normalize_job(job) for job in jobs]
    if use_rust is not False:
        exe = mcp_gateway.program()
        if exe is not None:
            rust_results = _run_batch_rust(exe, normalized, max_concurrency=max_concurrency)
            if rust_results is not None:
                return rust_results
    return _run_batch_python(normalized, max_concurrency=max_concurrency)
