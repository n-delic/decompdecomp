"""Parity and fallback tests for the Rust `scoring-diff` gateway subcommand."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from decompnet import scoring
from decompnet.app import mcp_gateway
from decompnet.elfobj import FunctionObj, Reloc


def _mips_fn(words: list[int], *, name: str = "f", big_endian: bool = True, relocs: list[Reloc] | None = None) -> FunctionObj:
    fmt = ">" if big_endian else "<"
    code = b"".join(struct.pack(f"{fmt}I", w) for w in words)
    return FunctionObj(name=name, code=code, relocs=relocs or [], arch="mips", big_endian=big_endian)


def _ppc_fn(words: list[int], *, name: str = "f") -> FunctionObj:
    code = b"".join(struct.pack(">I", w) for w in words)
    return FunctionObj(name=name, code=code, relocs=[], arch="ppc", big_endian=True)


def _gateway_exe() -> Path | None:
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "gateway/target/debug/decomp-mcp-gateway",
        "gateway/target/release/decomp-mcp-gateway",
        "gateway/target/checkout/decomp-mcp-gateway",
    ):
        cand = root / rel
        if cand.is_file():
            return cand
    return mcp_gateway.program()


@pytest.mark.parametrize("details", [True, False])
def test_rust_scoring_diff_parity_mips_and_ppc(monkeypatch, details):
    exe = _gateway_exe()
    if exe is None:
        pytest.skip("decomp-mcp-gateway binary not built")

    cases = [
        # stack, regalloc, meaning change
        (_mips_fn([0x8FA20004, 0x00851021, 0xAC820004]), _mips_fn([0x8FA20008, 0x00861821, 0xAC820008])),
        # reordered + hidden byte diff (addu vs daddu both disassemble as move)
        (_mips_fn([0x00801021, 0xAC820004]), _mips_fn([0xAC820004, 0x0080102D])),
        # exact match
        (_mips_fn([0x03E00008, 0x00000000]), _mips_fn([0x03E00008, 0x00000000])),
        # aligned branch target
        (_mips_fn([0x10800001, 0x00000000, 0x03E00008]), _mips_fn([0x10800002, 0x00000000, 0x24020001, 0x03E00008])),
        # PowerPC r1 stack slot diff (lwz r3, 0x10(r1) vs lwz r3, 0x14(r1))
        (_ppc_fn([0x80610010]), _ppc_fn([0x80610014])),
    ]

    for target, candidate in cases:
        monkeypatch.setattr(mcp_gateway, "program", lambda: None)
        py_res = scoring._diff(target, candidate, details=details, evidence=False)
        py_ev = scoring._diff(target, candidate, details=False, evidence=True)

        monkeypatch.setattr(mcp_gateway, "program", lambda: exe)
        rs_res = scoring._diff(target, candidate, details=details, evidence=False)
        rs_ev = scoring._diff(target, candidate, details=False, evidence=True)

        assert rs_res == py_res
        assert rs_ev == py_ev


def test_rust_scoring_fallback_when_gateway_missing(monkeypatch):
    monkeypatch.setattr(mcp_gateway, "program", lambda: None)
    target = _mips_fn([0x8FA20004, 0x00851021, 0xAC820004])
    candidate = _mips_fn([0x8FA20008, 0x00861821, 0xAC820008])
    res = scoring._diff(target, candidate, details=True)
    assert res.score == scoring.PENALTY_STACKDIFF + scoring.PENALTY_REGALLOC + scoring.PENALTY_MEANING
    assert not res.bytes_match
    assert len(res.rows) == 3
