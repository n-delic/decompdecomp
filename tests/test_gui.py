"""The GUI's own logic, run in node against a stand-in for the page (tests/gui_polls.js): no browser needed."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
GUI_TIMEOUT_SECONDS = 60


@pytest.fixture(scope="module")
def gui():
    if NODE is None:
        pytest.skip("needs node to run the GUI's scripts")
    out = subprocess.run([NODE, str(Path(__file__).with_name("gui_polls.js"))], capture_output=True, text=True,
                         timeout=GUI_TIMEOUT_SECONDS, check=True)
    result = json.loads(out.stdout)
    assert result["errors"] == []
    return result


def test_opening_another_project_shows_nothing_from_before(gui):
    """Two games with agents at work: opening the second one flooded it with notifications. The first game's poll,
    slow under load, kept the second one's first poll from running, and its next poll took the last 60 events as
    new. A project's poll toasts nothing now: news is the notification centre's, which starts where the feed was
    when the window opened, so the 60 old matches never show."""
    assert gui["toasts"] == []
    assert len(gui["notes"]) == 2  # only the news below


def test_the_notification_centre_tells_each_projects_news_in_one_line_per_burst(gui):
    """One feed for the whole app: the project's name, the agent and what happened; 14 matches in a row are one line
    that counts up ("+13 more matches"), and another project's paused helper is a line of its own."""
    matches, paused = gui["notes"]
    assert matches.startswith("Ridge Racer V") and "claude-2 matched func_200" in matches and "+13 more matches" in matches
    assert paused.startswith("Demo Quest 64") and "claude-1 paused: the provider is out of credit" in paused
    assert gui["unread"] == 2
