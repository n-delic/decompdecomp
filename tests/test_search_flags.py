"""Flags searched, not only compilers (search_flags): the C that looks right but won't match is often built with the
wrong flags. Each variant is judged like an attempt and recorded nowhere."""

import json

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError

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
    snc = net.flag_variants("pspsnc-1.2.7503", ["-O2", "-G8"])  # SNC has no -Os (it refuses to build with it)
    assert ["-O4", "-G8"] in snc and ["-O2", "-G0"] in snc and ["-O2", "-G8", "-Xsched=0"] in snc
    assert not any("-Os" in v or "-fno-strength-reduce" in v for v in snc)


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


def test_named_flags_are_the_compilers_own_list():
    net = RomNetwork(":memory:", ".")
    base = ["-G0", "-mips3", "-O1"]
    assert net.named_flag_variants("gcc-2.7.2-kmc", base, "-O2 -G8") == [
        base, ["-mips3", "-O2", "-G8"], ["-G0", "-mips3", "-O2"], ["-mips3", "-O1", "-G8"]]
    assert net.named_flag_variants("gcc-2.7.2-kmc", base, "-fno-strength-reduce")[1] == [*base, "-fno-strength-reduce"]
    assert net.named_flag_variants("ps3dev-gcc", ["-O2"], "-mminimal-toc", ("-mminimal-toc",))[1] == ["-O2",
                                                                                                    "-mminimal-toc"]
    for bad in ("-O7", "-fplugin=x.so", "-B/tmp", "-specs=x", "-o /tmp/x", "@args", "-include x.h", "-Wl,-x"):
        with pytest.raises(NetworkError, match="isn't a flag search_flags tries"):
            net.named_flag_variants("gcc-2.7.2-kmc", base, bad)
    with pytest.raises(NetworkError, match="one choice"):
        net.named_flag_variants("gcc-2.7.2-kmc", base, "-O2 -O3")
    with pytest.raises(NetworkError, match="isn't a flag"):
        net.named_flag_variants("pspsnc-1.2.7503", ["-O2"], "-Os")  # (SNC has none)


def test_a_rom_projects_agent_names_the_flags_to_try(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    net._update_cfg(slug, lambda cfg: cfg["compiler"].__setitem__(
        "flags", [f if f != "-O2" else "-O1" for f in cfg["compiler"]["flags"]]))
    said = net.search_flags(agent, slug, "func_800007D0", source=CLAMP, flags="-O2")
    assert "2 flag variants" in said and "MATCHES with" in said, said
    with pytest.raises(NetworkError, match="isn't a flag search_flags tries"):
        net.search_flags(agent, slug, "func_800007D0", source=CLAMP, flags="/GS-")
    with pytest.raises(NetworkError, match="builds with -O1 already"):
        net.search_flags(agent, slug, "func_800007D0", source=CLAMP, flags="-O1")
    assert json.loads(net._project(slug)["config"])["compiler"]["flags"][-1] == "-O1"
