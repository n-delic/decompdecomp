"""Overlays share load addresses, but helper verification must use the caller's ROM mapping."""

from types import SimpleNamespace

import pytest

from decompnet.app.engine.core import Core
from decompnet.app.engine.judging import JudgeMixin
from decompnet.network import NetworkError
from decompnet.rom.split import Segment


def engine(segments=None):
    net = object.__new__(Core)
    net._roms = {1: (b"AAAABBBBCCCCDDDDshared!!", segments or [
        Segment("first", 0, 8, 0x1000),
        Segment("second", 8, 16, 0x1000),
        Segment("resident", 16, 24, 0x2000),
    ])}
    return net


def caller(offset=12):
    return {"name": "caller", "vram": 0x1004, "size": 4, "rom_offset": offset}


@pytest.mark.parametrize("reverse", [False, True])
def test_caller_mapping_selects_helpers_independent_of_segment_order(reverse):
    net = engine()
    if reverse:
        net._roms[1][1].reverse()
    read = net.reader({"id": 1}, caller())
    assert read(0x1000, 4) == b"CCCC"
    assert read(0x1004, 4) == b"DDDD"
    assert read(0x2000, 8) == b"shared!!"
    assert read(0x3000, 4) is None


def test_two_callers_do_not_share_cached_overlay_selection():
    net = engine()
    first = net.reader({"id": 1}, caller(4))
    second = net.reader({"id": 1}, caller(12))
    assert first(0x1000, 4) == b"AAAA"
    assert second(0x1000, 4) == b"CCCC"
    assert first(0x1000, 4) == b"AAAA"


@pytest.mark.parametrize("offset", [None, 100])
def test_unknown_caller_mapping_cannot_skip_or_guess_verification(offset):
    read = engine().reader({"id": 1}, caller(offset))
    with pytest.raises(NetworkError, match="multiple ROM overlays"):
        read(0x1000, 4)


def test_ambiguous_data_outside_caller_overlay_is_rejected():
    net = engine()
    net._roms[1][1].extend([Segment("other-a", 0, 4, 0x3000), Segment("other-b", 8, 12, 0x3000)])
    with pytest.raises(NetworkError, match="multiple ROM overlays"):
        net.reader({"id": 1}, caller())(0x3000, 4)


def test_duplicate_mapping_is_not_ambiguous():
    net = engine([Segment("one", 0, 8, 0x1000), Segment("alias", 0, 8, 0x1000)])
    assert net.reader({"id": 1}, caller(4))(0x1000, 4) == b"AAAA"


def test_address_only_reader_keeps_existing_behavior():
    assert engine().reader({"id": 1})(0x1000, 4) == b"AAAA"


@pytest.mark.parametrize("helper", [b"CCCC", b"AAAA"])
def test_judge_passes_caller_context_and_does_not_accept_foreign_helper(helper):
    net = engine()
    def link(obj, name, target, symbols, read):
        exact = obj == read(0x1000, 4)
        return SimpleNamespace(candidate=obj, exact=exact, problems=[] if exact else ["helper differs"])
    net.is_rom = lambda proj: True
    net.console = lambda proj: SimpleNamespace(link_function=link)
    net._rom_target = lambda fn: None
    net.symbols = lambda project_id: None
    candidate, exact, problems = JudgeMixin._evaluate(net, caller(), {"id": 1}, helper)
    assert candidate == helper
    assert exact == (helper == b"CCCC")
    assert bool(problems) == (not exact)
