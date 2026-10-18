"""The GUI's own logic, run in node against a stand-in for the page (tests/gui_polls.js): no browser needed."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
GUI_TIMEOUT_SECONDS = 60


@pytest.mark.skipif(NODE is None, reason="needs node to run the GUI's scripts")
def test_opening_another_project_shows_only_what_happens_after():
    """Two games with agents at work: opening the second one flooded it with notifications. The first game's poll,
    slow under load, kept the second one's first poll from running, and its next poll took the last 60 events as
    new. Polls belong to the project they were made for, and only what happened after it opened is shown."""
    out = subprocess.run([NODE, str(Path(__file__).with_name("gui_polls.js"))], capture_output=True, text=True,
                         timeout=GUI_TIMEOUT_SECONDS, check=True)
    result = json.loads(out.stdout)
    assert result["toasts"] == ["<code>func_new</code>&nbsp;matched by gemini"], result["errors"]
