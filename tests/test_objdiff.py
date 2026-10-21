"""The per-function judge must not mistake a reference to unwritten code for its implementation."""

import json

import pytest

from decompnet.forks import objdiff


def _instruction(text="blr", kind=None):
    return {"instruction": {"formatted": text, "size": 4}, **({"diff_kind": kind} if kind else {})}


def _function(**fields):
    return {"name": "demo_function", "kind": "SYMBOL_FUNCTION", "size": "4", "instructions": [_instruction()], **fields}


def _diff(monkeypatch, tmp_path, left, right, symbol: str | None = "demo_function"):
    data = {"left": {"symbols": [left]}, "right": {"symbols": [right] if right else []}}
    monkeypatch.setattr(objdiff, "_run", lambda *args, **kwargs: json.dumps(data))
    # No object files are needed: these tests exercise the JSON reader, not the external tool or literals.
    return objdiff.diff(None, None, symbol, cwd=tmp_path)


@pytest.mark.parametrize(
    "right",
    [
        {"name": "demo_function", "flags": {"global": True}},
        {"name": "demo_function", "flags": {"global": True}, "match_percent": 100.0},
        {"name": "demo_function", "kind": "SYMBOL_OBJECT", "size": "4"},
        _function(instructions=[]),
        _function(instructions=[], match_percent=100.0),
        None,
    ],
)
def test_an_undefined_or_empty_base_symbol_is_not_a_matched_function(monkeypatch, tmp_path, right):
    """objdiff lists an extern reference by name too, with no function kind or instructions. Without a paired
    implementation, its target instructions have no diff markers: that does not mean every instruction matches."""
    result = _diff(monkeypatch, tmp_path, _function(), right)
    assert result.match_percent is None
    assert not result.matched
    assert result.unit_scores["demo_function"] is None
    assert result.target_size == 4
    assert "blr" in result.target_asm()


def test_an_undefined_base_symbol_does_not_count_as_built_in_unit_scores(monkeypatch, tmp_path):
    result = _diff(
        monkeypatch,
        tmp_path,
        _function(match_percent=100.0),
        {"name": "demo_function", "flags": {"global": True}},
        symbol=None,
    )
    assert result.unit_scores["demo_function"] is None


@pytest.mark.parametrize("percent", [100.0, 75.0, 0.0])
def test_a_defined_functions_explicit_match_percentage_is_preserved(monkeypatch, tmp_path, percent):
    result = _diff(monkeypatch, tmp_path, _function(match_percent=percent), _function(match_percent=percent))
    assert result.match_percent == percent
    assert result.matched == (percent == 100.0)
    assert result.unit_scores["demo_function"] == percent


def test_equal_compiled_instructions_can_still_supply_a_missing_percentage(monkeypatch, tmp_path):
    result = _diff(monkeypatch, tmp_path, _function(), _function())
    assert result.match_percent == 100.0
    assert result.matched


def test_different_instructions_without_a_percentage_do_not_match(monkeypatch, tmp_path):
    result = _diff(
        monkeypatch,
        tmp_path,
        _function(instructions=[_instruction(kind="DIFF_REPLACE")]),
        _function(instructions=[_instruction("nop", "DIFF_REPLACE")]),
    )
    assert result.match_percent == 0.0
    assert not result.matched


def test_an_empty_comparison_is_not_a_match(monkeypatch, tmp_path):
    result = _diff(monkeypatch, tmp_path, _function(instructions=[]), _function(instructions=[]))
    assert result.match_percent is None
    assert not result.matched


def test_unpaired_instructions_do_not_supply_a_perfect_match(monkeypatch, tmp_path):
    result = _diff(monkeypatch, tmp_path, _function(instructions=[_instruction(), _instruction()]), _function())
    assert result.match_percent == 0.0
    assert not result.matched
