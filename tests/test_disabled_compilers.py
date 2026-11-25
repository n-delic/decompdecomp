"""A compiler the user disabled in Compilers & tools is skipped by compiler checks and agents."""
import pytest

from decompnet import consoles, toolchains


def test_disable_and_enable_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path))
    assert not toolchains.is_disabled("mipspro-7.4.4")
    toolchains.set_disabled("mipspro-7.4.4", True)
    assert toolchains.is_disabled("mipspro-7.4.4")
    assert next(r for r in toolchains.status() if r["id"] == "mipspro-7.4.4")["disabled"]
    toolchains.set_disabled("mipspro-7.4.4", False)
    assert not toolchains.is_disabled("mipspro-7.4.4")


def test_unknown_compiler_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path))
    with pytest.raises(toolchains.ToolchainError):
        toolchains.set_disabled("no-such-compiler", True)


def test_disabled_compiler_leaves_the_consoles_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path))
    n64 = consoles.get("n64")
    first = n64.compilers[0].id
    assert first in {c.id for c in n64.compilers_for({})}
    toolchains.set_disabled(first, True)
    assert first not in {c.id for c in n64.compilers_for({})}
    assert first not in {c.id for c in n64.enabled_compilers()}
    assert first in {c.id for c in n64.compilers}  # (still known: matched code verifies with it)
