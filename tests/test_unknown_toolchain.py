"""#118: a ROM project whose compiler decompdecomp doesn't have still opens, says so, and can be switched to one it has.

The report: a project made with another tool named a 64-bit Visual C++ compiler; opening it raised the registry's
"unknown toolchain 'msvcxx-x86_64'; known: ..." error and the whole page failed, with nothing saying what to do.
"""

import shutil

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError
from decompnet.project import compiler_config

from .test_rom import built  # noqa: F401  (fixture that builds the demo ROM)

UNKNOWN = "msvcxx-x86_64"
SLUG = "demo-quest-64"
HAS_COMPILER = pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None or not shutil.which("ld.lld"),
                                  reason="needs gcc-2.7.2-kmc and ld.lld to build the demo ROM")


@pytest.fixture(scope="module")
def net(built, tmp_path_factory):  # noqa: F811
    ws = tmp_path_factory.mktemp("unknown-compiler")
    net = RomNetwork(ws / "decomp.db", ws)
    assert net.import_rom(built, confirmed=True, log=lambda *_: None) == SLUG
    return net


@pytest.fixture
def unknown(net):
    """The project set to a compiler decompdecomp doesn't have, as another tool's project would be."""
    net._update_cfg(SLUG, lambda cfg: cfg.__setitem__(
        "compiler", {"toolchain": UNKNOWN, "flags": ["/O2", "/GS-"], "timeout": 20}))
    return net


@HAS_COMPILER
def test_a_project_with_an_unknown_compiler_opens_and_says_it_isnt_here(unknown):
    ov = unknown.overview(SLUG)
    assert ov["compiler"]["toolchain"] == UNKNOWN and ov["compiler"]["name"] == UNKNOWN
    assert ov["compiler"]["known"] is False
    assert ov["compiler"]["installed"] is False and ov["compiler"]["supported"] is False
    assert ov["compiler"]["flags"] == ["/O2", "/GS-"]
    # the sidebar's summary reads the project from the database alone: it says the same, without raising
    assert unknown.summary(SLUG)["compiler"]["name"] == UNKNOWN
    # Settings' compiler panel asks for the compiler answer: it names the project's compiler too
    assert unknown.compiler_answer(SLUG)["compiler"] == UNKNOWN


@HAS_COMPILER
def test_switching_to_a_compiler_decompdecomp_has_works(unknown):
    assert unknown.overview(SLUG)["compiler"]["known"] is False
    unknown.set_compiler(SLUG, "gcc-2.7.2-kmc")
    ov = unknown.overview(SLUG)
    assert ov["compiler"]["known"] is True and ov["compiler"]["toolchain"] == "gcc-2.7.2-kmc"
    assert ov["compiler"]["name"] != UNKNOWN
    assert unknown.summary(SLUG)["compiler"]["name"] != UNKNOWN


@HAS_COMPILER
def test_compiling_says_which_compiler_to_pick_and_where(unknown):
    agent, _ = unknown.local_agent("issue-118")
    pid = unknown._project(SLUG)["id"]
    name = unknown.store.one("SELECT name FROM functions WHERE project_id=? AND status='open' "
                             "ORDER BY n_insns DESC LIMIT 1", pid)["name"]
    try:
        with pytest.raises(toolchains.ToolchainError) as said:
            unknown.compile_and_diff(agent, name, f"void {name}(void) {{ }}", SLUG)
    finally:
        unknown.release(agent, name, "the project's compiler is unknown", SLUG)
    text = str(said.value)
    assert UNKNOWN in text and "Settings" in text
    assert "known:" not in text  # the registry's list of every id isn't an instruction


@HAS_COMPILER
def test_autopilot_says_the_unknown_compiler_needs_a_choice(unknown):
    with pytest.raises(NetworkError) as said:
        unknown.start_autopilot(SLUG)
    assert UNKNOWN in str(said.value) and "Settings" in str(said.value)


def test_the_compile_command_itself_carries_the_sentence(tmp_path):
    """A project's compile command is built before every compile, autopilot included: the error is made there, so no
    caller can show the registry's list instead."""
    with pytest.raises(toolchains.ToolchainError) as said:
        compiler_config(tmp_path, {"compiler": {"toolchain": UNKNOWN, "flags": [], "timeout": 20}})
    text = str(said.value)
    assert UNKNOWN in text and "Settings" in text and "known:" not in text
