"""Flags searched, not only compilers (search_flags): the C that looks right but won't match is often built with the
wrong flags. Each variant is judged like an attempt and recorded nowhere."""

import json

import pytest

from decompnet.app.engine import RomNetwork

from .test_app_engine import CLAMP
from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)


def test_the_variants_change_one_flag_each():
    net = RomNetwork(":memory:", ".")
    base = ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"]
    variants = net.flag_variants("gcc-2.7.2-kmc", base)
    assert variants[0] == base and ["-G0", "-mips3", "-mgp32", "-mfp32", "-O1"] in variants
    assert ["-G8", "-mips3", "-mgp32", "-mfp32", "-O2"] in variants and [*base, "-fno-strength-reduce"] in variants
    assert all(sum(a != b for a, b in zip(v, base)) <= 1 for v in variants if len(v) == len(base))
    cw = net.flag_variants("mwcps2-3.0.1", ["-O3", "-sdatathreshold", "8"])
    assert ["-O3", "-sdatathreshold", "0"] in cw and ["-O4", "-sdatathreshold", "8"] in cw


def test_a_project_with_the_wrong_flags_hears_which_ones_match(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    net._update_cfg(slug, lambda cfg: cfg["compiler"].__setitem__(
        "flags", [f if f != "-O2" else "-O1" for f in cfg["compiler"]["flags"]]))  # the project's flags are wrong
    said = net.search_flags(agent, slug, "func_800007D0", source=CLAMP)
    assert "MATCHES with" in said and "-O2" in said.split("MATCHES with")[1].split(".")[0], said
    assert json.loads(net._project(slug)["config"])["compiler"]["flags"][-1] == "-O1"  # it only says, changes nothing
    with pytest.raises(Exception, match="no attempt yet"):
        net.search_flags(agent, slug, "func_800007D0")
