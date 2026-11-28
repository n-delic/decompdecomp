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


def test_compile_sources_batch_rust_and_fallback(monkeypatch, tmp_path: Path):
    from decompnet import compiler

    fake_cc = tmp_path / "fake_cc.py"
    fake_cc.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "src, obj = Path(sys.argv[1]), Path(sys.argv[2])\n"
        "text = src.read_text()\n"
        "if 'FAIL' in text:\n"
        "    print('syntax error', file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "if 'SLEEP' in text:\n"
        "    time.sleep(5)\n"
        "obj.write_bytes(b'OBJ:' + text.splitlines()[-1].encode())\n",
        encoding="utf-8",
    )
    cfg = compiler.CompilerConfig(
        cmd=[sys.executable, str(fake_cc), "{src}", "{obj}"],
        timeout=0.4,
        sandbox="none",
    )
    sources = [
        "int a(void) { return 1; }",
        "int b(void) { FAIL; }",
        "int c(void) { __asm__(\"nop\"); }",
        "int d(void) { return 4; }",
    ]

    # Fallback when gateway binary is unavailable.
    monkeypatch.setattr(mcp_gateway, "program", lambda: None)
    assert compiler.compile_sources_batch(cfg, "/* hdr */\n", sources) is None

    # Fallback when disabled via env or unsupported modes.
    exe = mcp_gateway.built()
    if exe is None:
        pytest.skip("decomp-mcp-gateway binary not built")
    monkeypatch.setattr(mcp_gateway, "program", lambda: exe)
    monkeypatch.setenv("DECOMP_RUST_CANDIDATE_RUNNER", "0")
    assert compiler.compile_sources_batch(cfg, "/* hdr */\n", sources) is None
    monkeypatch.delenv("DECOMP_RUST_CANDIDATE_RUNNER", raising=False)

    assert compiler.compile_sources_batch(cfg, "/* hdr */\n", sources[:1]) is None
    assert compiler.compile_sources_batch(
        compiler.CompilerConfig(cmd=cfg.cmd, sandbox="none", wsl=True), "/* hdr */\n", sources
    ) is None
    assert compiler.compile_sources_batch(
        compiler.CompilerConfig(cmd=cfg.cmd, sandbox="none", cplusplus=True), "/* hdr */\n", sources
    ) is None

    # Real Rust candidate-runner batch compile.
    results = compiler.compile_sources_batch(cfg, "/* hdr */\n", sources, max_concurrency=2)
    assert results is not None
    assert len(results) == 4
    assert results[0] is not None and results[0].ok and results[0].obj == b"OBJ:int a(void) { return 1; }"
    assert results[1] is not None and not results[1].ok and "syntax error" in results[1].log
    assert results[2] is not None and not results[2].ok and "rejected:" in results[2].log
    assert results[3] is not None and results[3].ok and results[3].obj == b"OBJ:int d(void) { return 4; }"


def test_deep_compilation_pool_parallel_batch_and_fallback(monkeypatch):
    import threading
    from types import MethodType

    from decompnet import compiler
    from decompnet.app import deep_compilation, deep_permuter, permute
    from decompnet.network import Network
    from tests.test_deep_permuter import Log
    from tests.test_permute import AGENT, FakeNet

    monkeypatch.setattr(compiler, "concurrency_limit", lambda: 2)
    net = FakeNet()
    net._compile_ahead_local = threading.local()
    net.compiled_ahead = MethodType(Network.compiled_ahead, net)
    net._attempt_compilation = MethodType(Network._attempt_compilation, net)
    net._compile_snapshot = Network._compile_snapshot
    net.keep_diff = lambda *args: None

    parallel_calls: list[list[str]] = []
    single_calls: list[str] = []

    def parallel_batch(proj, header, sources, fn, *, max_concurrency=None):
        parallel_calls.append(list(sources))
        return [compiler.CompileResult(True, s.encode(), "") for s in sources]

    def single_compile(proj, header, source, fn):
        single_calls.append(source)
        return compiler.CompileResult(True, source.encode(), "")

    net._compile_parallel_batch = parallel_batch
    net._compile = single_compile

    search = deep_permuter.Search(net, 1, 10, Log(), net.function())
    search.compile_pool = deep_compilation.Pool(count=2, remote=True)
    judged: list[str] = []

    def score(self, agent, proj, fn, source, best, fork):
        res = net._attempt_compilation(proj, net._context(1, "")["header"], source, fn)
        assert res.obj == source.encode()
        judged.append(source)
        return 40

    monkeypatch.setattr(permute.Permuter, "_score_work", score)
    sources = [f"int f(){{return {i};}}" for i in range(4)]

    try:
        for source in search._stream_variants((c for c in [sources]), AGENT, net.project_row("game"), net.function(), None):
            assert search._score(AGENT, net.project_row("game"), net.function(), source, 40, None) == 40
        assert judged == sources
        assert len(parallel_calls) == 1 and parallel_calls[0] == sources
        assert single_calls == []

        # Now return None from parallel_batch to verify seamless fallback to single_compile.
        net._compile_parallel_batch = lambda *a, **kw: None
        judged.clear()
        sources2 = [f"int g(){{return {i};}}" for i in range(4)]
        for source in search._stream_variants((c for c in [sources2]), AGENT, net.project_row("game"), net.function(), None):
            assert search._score(AGENT, net.project_row("game"), net.function(), source, 40, None) == 40
        assert judged == sources2
        assert sorted(single_calls) == sorted(sources2)
    finally:
        search.compile_pool.close()
