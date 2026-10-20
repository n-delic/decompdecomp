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


def test_an_effort_goes_only_to_an_agent_that_offers_it(gui):
    assert gui["checks"]["efforts"] == ["opus#xhigh", "deepseek/v4", "m"]


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


def test_fork_and_join_say_what_is_wrong_before_anything_starts(gui):
    """A fork of "hello" reached git, and a join with the link "hello" showed an empty box: the page checks the
    shapes the app accepts first, says what's wrong in a sentence, and keeps the button off until it's right."""
    empty, word, ssh, other_host, github, gitlab = gui["checks"]["repo"]
    assert github == gitlab == ""
    assert "https://github.com/owner/repo" in word and "https address" in ssh and "GitHub, GitLab, Codeberg" in other_host
    assert empty and "Username" not in word
    bad_link, invite, bare_room = gui["checks"]["invite"]
    assert bad_link.startswith("That isn't an invite link") and invite == bare_room == ""
    no_copy, bad_name, ready = gui["checks"]["join"]
    assert "copy of the game" in no_copy and "letters, digits" in bad_name and ready == ""


def test_the_autopilots_run_and_its_summary_are_one_notification(gui):
    """The autopilot's matches and its summary ("20 of 95 functions matched from machine drafts") were two lines saying
    the same thing. The summary ends the run's line; an agent's match after it is a line of its own."""
    run, agent, alone = gui["checks"]["autopilot"]
    assert run == "Autopilot: 2 of 95 functions matched from machine drafts"
    assert agent == "claude-1 matched func_3" and alone.startswith("Autopilot: 0 of 1")


def test_searching_functions_looks_at_names_and_addresses_before_the_segment(gui):
    """Searching "ma" listed every function of the segment "main". A ROM's segment counts only when no name or address
    has the text; a fork's source file is shown in the list, so it always counts there."""
    rom_ma, fork_main, _, nothing = gui["checks"]["search"]
    assert rom_ma == ["main_loop"]
    assert fork_main == ["func_80000400", "lerp", "main_loop"] and nothing == []


def test_the_theme_button_goes_round_system_light_and_dark(gui):
    """The sidebar's button only flipped light and dark: once pressed, the system's choice was gone for good."""
    assert gui["checks"]["themes"] == ["light", "dark", ""]
    assert gui["checks"]["systemThemes"] == ["", "light", "dark", ""]


def test_each_computer_in_a_room_has_one_colour_on_every_screen(gui):
    """In a room, every agent's name had the same look whoever ran it. Each computer has a colour now, the same on
    the host's screen and every guest's: the host's first, then the members in the order they joined."""
    c = gui["checks"]["computers"]
    # the host's, alex's, kim's own (kim's screen), the autopilot (none), the host's with a dot in its model's name
    assert c["guest"] == [0, 1, 2, None, 0]
    assert c["host"] == [0, 1, 2, 0]  # the same colours on the host's screen
    assert c["alone"] is None  # no room, no colours
