"""The console says what a starting app is doing and how long each step took (app/startup_steps.py)."""

import pytest

from decompnet.app import startup_steps


def test_steps_print_and_log(capsys, caplog, monkeypatch):
    clock = iter([0.0, 2.5, 3.0, 3.2, 4.0])
    monkeypatch.setattr(startup_steps.time, "monotonic", lambda: next(clock))
    caplog.set_level("INFO", logger="decompdecomp")
    with startup_steps.step("Opening databases"):
        pass
    startup_steps.begin("Quick step")
    startup_steps.end()
    out = capsys.readouterr().out
    assert "  Opening databases…\n    done in 2.5s\n" in out
    assert "  Quick step…\n" in out and "done in 0.2s" not in out  # a quick step only says it started
    assert "startup: Opening databases took 2.5s" in caplog.text
    assert "startup: Quick step took 0.2s" in caplog.text


def test_failed_step_is_logged(caplog):
    caplog.set_level("INFO", logger="decompdecomp")
    with pytest.raises(RuntimeError), startup_steps.step("Broken"):
        raise RuntimeError("no")
    assert "startup: Broken failed after" in caplog.text
