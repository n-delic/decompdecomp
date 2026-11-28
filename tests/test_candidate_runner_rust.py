"""Parity and fallback tests for the Rust parallel candidate runner."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from decompnet import candidate_runner
from decompnet.app import mcp_gateway
from decompnet.candidate_runner import CandidateJob, SourceFile


def _py_cmd(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _make_parity_jobs(tmp_path: Path) -> list[CandidateJob]:
    src = tmp_path / "sub" / "cand.c"
    return [
        CandidateJob(
            id="ok",
            argv=_py_cmd("import sys; print('hello'); print('warn', file=sys.stderr)"),
            timeout_ms=5000,
        ),
        CandidateJob(
            id="nonzero",
            argv=_py_cmd("import sys; sys.exit(9)"),
            timeout_ms=5000,
        ),
        CandidateJob(
            id="env_stdin",
            argv=_py_cmd("import os, sys; data = sys.stdin.read(); print(f'{data}:{os.environ[\"DECOMP_TEST_K\"]}')"),
            env={"DECOMP_TEST_K": "v123"},
            stdin="payload",
            timeout_ms=5000,
        ),
        CandidateJob(
            id="cwd_and_source",
            argv=_py_cmd("from pathlib import Path; print(Path('cand.c').read_text().strip())"),
            cwd=str(tmp_path / "sub"),
            source_file=SourceFile(path=str(src), content="int x = 42;\n"),
            timeout_ms=5000,
        ),
        CandidateJob(
            id="timeout",
            argv=_py_cmd("import time; time.sleep(5)"),
            timeout_ms=150,
        ),
    ]


def test_python_fallback_when_gateway_unavailable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mcp_gateway, "program", lambda: None)
    jobs = _make_parity_jobs(tmp_path)
    results = candidate_runner.run_batch(jobs, max_concurrency=2)

    assert len(results) == len(jobs)
    assert results[0].id == "ok"
    assert results[0].exit_code == 0
    assert results[0].stdout.strip() == "hello"
    assert results[0].stderr.strip() == "warn"
    assert not results[0].timed_out

    assert results[1].id == "nonzero"
    assert results[1].exit_code == 9
    assert not results[1].timed_out

    assert results[2].id == "env_stdin"
    assert results[2].exit_code == 0
    assert results[2].stdout.strip() == "payload:v123"

    assert results[3].id == "cwd_and_source"
    assert results[3].exit_code == 0
    assert results[3].stdout.strip() == "int x = 42;"

    assert results[4].id == "timeout"
    assert results[4].timed_out is True
    assert results[4].exit_code is None


def test_rust_and_python_parity(monkeypatch, tmp_path: Path):
    exe = mcp_gateway.program() or mcp_gateway.built()
    if exe is None:
        pytest.skip("decomp-mcp-gateway binary not built")
    monkeypatch.setattr(mcp_gateway, "program", lambda: exe)

    rust_dir = tmp_path / "rust"
    py_dir = tmp_path / "py"
    rust_dir.mkdir()
    py_dir.mkdir()

    rust_results = candidate_runner.run_batch(_make_parity_jobs(rust_dir), max_concurrency=3, use_rust=True)
    py_results = candidate_runner.run_batch(_make_parity_jobs(py_dir), max_concurrency=3, use_rust=False)

    assert len(rust_results) == len(py_results)
    for r_res, p_res in zip(rust_results, py_results, strict=True):
        assert r_res.id == p_res.id
        assert r_res.exit_code == p_res.exit_code
        assert r_res.timed_out == p_res.timed_out
        assert r_res.error == p_res.error
        if not r_res.timed_out:
            assert r_res.stdout == p_res.stdout
            assert r_res.stderr == p_res.stderr


def test_concurrency_limit_and_empty_batch():
    assert candidate_runner.run_batch([]) == []

    jobs = [
        CandidateJob(id=f"j{i}", argv=_py_cmd("import time; time.sleep(0.12)"), timeout_ms=5000)
        for i in range(3)
    ]
    started = time.monotonic()
    results = candidate_runner.run_batch(jobs, max_concurrency=1)
    elapsed = time.monotonic() - started

    assert len(results) == 3
    assert all(r.exit_code == 0 and not r.timed_out for r in results)
    assert elapsed >= 0.30
