"""Opt-in diagnostics provide evidence without changing judging or deleting old notes."""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import match_diagnostics as diag
from decompnet.app.engine.judging import JudgeMixin
from decompnet.app.support import Settings
from decompnet.elfobj import FunctionObj
from decompnet.network import Network
from decompnet.rom.link import LinkResult, record_mismatch


def test_operator_toggle_persists_and_judge_update_revalidates(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "settings.json")
    net = SimpleNamespace()
    monkeypatch.setattr(diag, "judge_stamp", lambda: "judge-a")
    monkeypatch.setattr(diag.time, "time", lambda: 100)
    diag.configure(settings, net)
    assert not settings.public()["match_diagnostics"]
    assert net.match_diagnostics_since == 0
    settings.data["match_diagnostics"] = True
    diag.configure(settings, net)
    assert net.match_diagnostics_since == 100
    loaded = Settings(settings.path)
    monkeypatch.setattr(diag.time, "time", lambda: 200)
    diag.configure(loaded, net)
    assert net.match_diagnostics_since == 100
    monkeypatch.setattr(diag, "judge_stamp", lambda: "judge-b")
    diag.configure(loaded, net)
    assert net.match_diagnostics_since == 200
    loaded.data["match_diagnostics"] = False
    diag.configure(loaded, net)
    assert net.match_diagnostics_since == 0


def test_notes_are_annotated_without_mutation_or_claiming_they_are_false():
    rows = [{"text": "Wait on a prerequisite", "created_at": 1},
            {"text": "Uses a signed short", "created_at": 1},
            {"text": "Relocation still differs in this new attempt", "created_at": 3}]
    result = diag.annotate(rows, 2)
    assert result[0]["text"].startswith("[Recheck:")
    assert result[1:] == rows[1:]
    assert rows[0]["text"] == "Wait on a prerequisite"


def test_evidence_window_finds_late_difference_and_is_bounded():
    expected = bytes(10000)
    actual = bytearray(expected)
    actual[9001] = 1
    out = []
    for _ in range(20):
        record_mismatch(out, "helper", 0x1000, expected, bytes(actual), True)
    assert len(out) == 3
    assert out[0].offset == 9001
    assert out[0].address == 0x1000 + 8992
    assert len(out[0].actual) == len(out[0].expected) == 1008


def result():
    obj = FunctionObj("caller", "mips", False, bytes(8), [])
    res = LinkResult(obj, False, ["helper differs"])
    record_mismatch(res.mismatches, "helper", 0x2000,
                    bytes.fromhex("010002240800e00300000000"),
                    bytes.fromhex("020002240800e00300000000"), True)
    return res


def test_toggle_changes_feedback_but_never_verdict():
    res = result()
    net = SimpleNamespace(is_rom=lambda p: True, console=lambda p: SimpleNamespace(
        link_function=lambda *a: res, cpu_variant=""), _rom_target=lambda f: None,
        symbols=lambda i: None, reader=lambda p: None)
    fn, proj = {"name": "caller"}, {"id": 1}
    off = JudgeMixin._evaluate(net, fn, proj, b"")
    net.match_diagnostics_since = 1
    on = JudgeMixin._evaluate(net, fn, proj, b"")
    assert off[:2] == on[:2] == (res.candidate, False)
    assert off[2] == ["helper differs"]
    assert any("Linked helper instruction window" in text for text in on[2])
    assert res.problems == ["helper differs"]


def test_data_evidence_is_hex_not_disassembled():
    res = result()
    res.mismatches[0].code = False
    text = "\n".join(diag.details(res))
    assert "Expected bytes:" in text and "Actual bytes:" in text
    assert "instruction window" not in text


def test_toggle_does_not_reuse_feedback_from_other_mode():
    net = object.__new__(Network)
    net._generation = {}
    fn = {"id": 1, "project_id": 1}
    off = net.compile_key(fn, "source")
    net.match_diagnostics_since = 100
    on = net.compile_key(fn, "source")
    assert on != off
    net.match_diagnostics_since = 0
    assert net.compile_key(fn, "source") == off



@pytest.mark.skipif(not shutil.which("node"), reason="needs Node for the GUI handler test")
def test_gui_toggle_sends_boolean_and_restores_saved_state():
    subprocess.run(["node", str(Path(__file__).with_name("gui_match_diagnostics.js"))], check=True, timeout=10)



def test_helper_diagnostic_prioritizes_instruction_change_over_shifted_branches():
    import struct

    # A long run of differing branch offsets precedes the useful opcode difference.
    expected = struct.pack("<" + "I" * 90, *([0x10000001] * 80 + [0x24020001] * 10))
    actual = struct.pack("<" + "I" * 90, *([0x10000002] * 80 + [0x00021880] * 10))
    res = result()
    res.mismatches = []
    record_mismatch(res.mismatches, "helper", 0x2000, expected, actual, True)
    text = "\n".join(diag.details(res))
    assert "Prioritizing an instruction/layout difference" in text
    assert "sll" in text
    assert len(text) < 6000



def test_existing_brief_note_path_respects_toggle_and_keeps_original_rows():
    rows = [{"text": "Blocked by a prerequisite", "created_at": 1, "name": "agent"}]
    net = object.__new__(Network)
    net.store = SimpleNamespace(all=lambda *args: rows)
    fn = {"id": 1, "status": "open", "best_score": None}
    normal = net.notes_for_brief(fn, when=False)
    net.match_diagnostics_since = 2
    assert "[Recheck:" in net.notes_for_brief(fn, when=False)[0]
    net.match_diagnostics_since = 0
    assert net.notes_for_brief(fn, when=False) == normal
    assert rows[0]["text"] == "Blocked by a prerequisite"
