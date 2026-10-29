"""The GUI's own logic, run in node against a stand-in for the page (tests/gui_polls.js): no browser needed."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
GUI_TIMEOUT_SECONDS = 60


def test_foreground_progress_covers_delayed_starts_and_clears_on_every_outcome():
    if NODE is None:
        pytest.skip("needs node to run the GUI's scripts")
    subprocess.run([NODE, str(Path(__file__).with_name("gui_progress.js"))], check=True,
                   capture_output=True, text=True, timeout=GUI_TIMEOUT_SECONDS)


def test_saved_project_dialog_preserves_state_and_cancels_pending_requests():
    if NODE is None:
        pytest.skip("needs node to run the GUI's scripts")
    subprocess.run([NODE, str(Path(__file__).with_name("gui_restore.js"))], check=True,
                   capture_output=True, text=True, timeout=GUI_TIMEOUT_SECONDS)


def test_launcher_keeps_single_and_multiple_model_modes_for_every_provider():
    if NODE is None:
        pytest.skip("needs node to run the GUI's scripts")
    subprocess.run([NODE, str(Path(__file__).with_name("gui_launcher.js"))], check=True,
                   capture_output=True, text=True, timeout=GUI_TIMEOUT_SECONDS)


@pytest.fixture(scope="module")
def gui():
    if NODE is None:
        pytest.skip("needs node to run the GUI's scripts")
    out = subprocess.run([NODE, str(Path(__file__).with_name("gui_polls.js"))], capture_output=True, text=True,
                         timeout=GUI_TIMEOUT_SECONDS, check=True)
    result = json.loads(out.stdout)
    assert result["errors"] == []
    return result


def test_a_ds_rom_is_told_by_its_header_as_the_server_tells_it(gui):
    nds = gui["checks"]["nds"]
    assert nds["good"] == ["nds", "ADQE", "DEMOQUESTDS"]
    assert nds["broken"] is None and "checksum" in nds["said"]


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


def test_stopped_helpers_claims_do_not_read_as_agents_working(gui):
    """#99: the permuter claims a function for half an hour and its claim outlives the helper and a restart, so the
    header still said "1 agent working" with every helper stopped. The app's own workers (the permuter, the
    autopilot, decompdecomp, and a room host's) aren't agents anyone started; another computer's helper still is."""
    agents = gui["checks"]["agents"]
    assert agents["ownOnly"] == agents["nameless"] == 0
    assert agents["outside"] == 1


def test_a_helper_going_between_waiting_and_working_does_not_reorder_the_rows(gui):
    """#99: with four API helpers on one project the rows jumped every time one went from waiting to working (or
    back), so following one agent with the eye was impossible. Live helpers keep their name order; only starting or
    stopping one moves it, and decompiling helpers still come before polishing ones."""
    agents = gui["checks"]["agents"]
    assert agents["flipped"] == agents["again"] == ["alpha", "beta"]
    assert agents["rows"] == ["working-one", "stopped-one", "polishing", "paused-one"]


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


def test_an_unknown_compiler_says_the_project_was_made_with_another_tool(gui):
    """#118: a project from another tool names a compiler decompdecomp doesn't have. The banner called it a compiler
    with no build for this computer; it says what it is and where to pick one decompdecomp has."""
    said = gui["checks"]["unknownCompiler"]
    assert said["hidden"] is False and said["className"] == "notice bad"
    assert "msvcxx-x86_64 isn't a compiler decompdecomp has" in said["text"] and "another tool" in said["text"]
    assert "Settings" in said["text"] and "no build for this computer" not in said["text"]


def test_the_drop_zone_takes_playstation_programs_and_leaves_a_discs_console_to_its_system_cnf(gui):
    """A PS-X EXE is the PS1's; a disc image could be any PlayStation's until the server reads it."""
    assert gui["checks"]["dropped"] == ["ps", "playstation", "PlayStation 1, 2 or PSP", None]


def test_the_drop_zone_tells_psp_games_from_the_ps2s_and_says_what_to_do_with_what_it_cant_read(gui):
    """A PSP's UMD image starts like a PS2 disc and its programs are MIPS ELFs like the PS2's, and a .cso starts with
    CISO like a compressed GameCube disc: the drop zone reads further, so a PSP game isn't taken for another
    console's. A .cso is a PlayStation disc whose console the server reads inside it; a .zso or an EBOOT.PBP (not
    readable yet) says what to drop instead."""
    psp = gui["checks"]["psp"]
    # a UMD image, a PS2 disc, a GameCube CISO, a .cso, a PRX module, an Allegrex program, a PS2 program
    assert psp["platforms"] == ["psp", "playstation", "gcwii", "playstation", "psp", "psp", "ps2"]
    assert psp["cso"] == "CSO disc image"
    zso, pbp = psp["said"]
    assert "ZSO" in zso and "decompress it to .iso" in zso
    assert "EBOOT.PBP" in pbp and "encrypted" in pbp and ".iso" in pbp
    assert psp["name"] == "PlayStation 1, 2 or PSP"


def test_the_drop_zone_tells_64_bit_arm_and_dos_programs_apart(gui):
    """It said "a 64-bit or non-x86 Windows program (or a DOS one)" for all three: it says which, and when 64-bit
    games come, before uploading hundreds of megabytes the import would refuse."""
    x64, arm, x86, dos = gui["checks"]["windows"]
    assert x64[0] is None and "64-bit Windows program" in x64[1] and "Newer Windows games" in x64[1]
    assert arm[0] is None and "ARM" in arm[1]
    assert x86[0] == "win32"  # a 32-bit x86 one is the PC console's (notSupported is asked only otherwise)
    assert dos[0] is None and "DOS program" in dos[1]


def test_each_computer_in_a_room_has_one_colour_on_every_screen(gui):
    """In a room, every agent's name had the same look whoever ran it. Each computer has a colour now, the same on
    the host's screen and every guest's: the host's first, then the members in the order they joined."""
    c = gui["checks"]["computers"]
    # the host's, alex's, kim's own (kim's screen), the autopilot (none), the host's with a dot in its model's name
    assert c["guest"] == [0, 1, 2, None, 0]
    assert c["host"] == [0, 1, 2, 0]  # the same colours on the host's screen
    assert c["alone"] is None  # no room, no colours


def test_polish_is_offered_once_half_the_games_own_code_matches(gui):
    """An Xbox game had all 33 of its own functions matched and no Polish to choose: the launcher weighed them against
    all 274, its 241 set-aside ones (library code, assembly) included, which the header leaves out."""
    assert gui["checks"]["polish"] == [True, False, False, False]


def test_the_import_offers_every_compiler_of_the_games_console(gui):
    """The import's compiler test can only try what's installed: a game linked by CodeWarrior was guessed right only
    because its GCC was there. The import offers the console's missing compilers, every PlayStation's for a disc."""
    assert gui["checks"]["missing"] == [["ee-gcc-2.96", "mwcps2-3.0.1"], 3, 0]


def test_a_dropped_pc_game_is_offered_the_visual_cpp_pack_its_program_names(gui):
    """A game of 2005-2010 was offered the 6.0-7.1 pack, and its import then asked for 2005-2010's: the drop reads the
    Rich header (or the linker's version) as the import does, and offers the pack the import will build with."""
    from decompnet.consoles.pc import scratch

    packs = gui["checks"]["pcPack"]
    for head, pack, offered in packs:
        built = scratch.describe(bytes.fromhex(head))
        assert pack == offered == scratch.compiler_pack(built.pack_version), (head, built)
    assert [p for _, p, _ in packs] == ["msvc2005", "msvc", "msvc", "msvc2005", "msvc2005", "msvc"]


def test_the_drop_zone_knows_the_visual_cpp_ids_the_import_knows():
    """home.js reads a dropped program's Rich header for the pack to offer (pcPack): its ids are scratch's."""
    from decompnet.consoles.pc import scratch

    text = (Path(__file__).resolve().parent.parent / "src" / "decompnet" / "app" / "static" / "js" / "home.js").read_text()
    table = re.search(r"const RICH_COMPILERS = \{(.*?)\};", text, re.S).group(1)
    js = {v: {i for lo, hi in re.findall(r"\[(0x[0-9a-f]+), (0x[0-9a-f]+)\]", ranges) for i in range(int(lo, 16),
                                                                                                    int(hi, 16))}
          for v, ranges in re.findall(r'"([\d.]+)": (\[\[.*?\]\])', table)}
    assert js == {v: {*compilers, *glued} for v, compilers, glued in scratch.RICH_COMPILERS}
    linkers = re.search(r"const RICH_LINKERS = \[(.*?)\];", text, re.S).group(1)
    assert {int(x, 16) for x in re.findall(r"0x[0-9a-f]+", linkers)} == scratch.RICH_LINKERS
