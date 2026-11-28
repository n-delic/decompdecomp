import json

import pytest

from decompnet.app import mcp_gateway
from decompnet.app.twins import rom_print, rom_prints_rust


def _sample_rows():
    return [
        {
            "id": 1,
            # lui $a0, 0x8001; addiu $a0, $a0, 0x1234 (unrecorded pair)
            "target": bytes.fromhex("3c04800124841234"),
            "relocs": "[]",
        },
        {
            "id": 2,
            # lui $a0, 0x8001; jal 0x80001000; addiu $a0, $a0, 0x1234 (delay slot pair + recorded jal)
            "target": bytes.fromhex("3c0480010c00040024841234"),
            "relocs": json.dumps([[4, 4, 0x80001000]]),
        },
        {
            "id": 3,
            "target": b"",
            "relocs": "[]",
        },
    ]


@pytest.mark.parametrize("big_endian", [True, False])
def test_rom_prints_rust_parity(big_endian):
    if mcp_gateway.program() is None:
        pytest.skip("decomp-mcp-gateway binary not built")
    rows = _sample_rows()
    label = lambda addr: f"sym_{addr:08x}"
    py_out = {r["id"]: rom_print(r, "mips", big_endian, label) for r in rows}
    rust_out = rom_prints_rust(rows, "mips", big_endian, label)
    assert rust_out is not None
    assert rust_out == py_out


def test_rom_prints_rust_fallback_when_missing_binary(monkeypatch):
    monkeypatch.setattr(mcp_gateway, "program", lambda: None)
    assert rom_prints_rust(_sample_rows(), "mips", True, lambda a: f"D_{a:08X}") is None
