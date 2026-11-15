"""Already placed game files remain intact, while version checks still run."""

import hashlib
import json
import os

import pytest

from decompnet.forks.project import ForkError, ForkProject


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("alias", ["same", "hardlink", "symlink"])
def test_an_already_placed_game_is_validated_without_copying_or_moving(tmp_path, move, alias):
    data = b"synthetic game input"
    dest = tmp_path / "orig" / "game.bin"
    dest.parent.mkdir()
    dest.write_bytes(data)
    (tmp_path / ".decomp.wtf.json").write_text(json.dumps({
        "game": {"path": "orig/", "sha1": hashlib.sha1(data).hexdigest()}}))
    src = dest
    if alias != "same":
        src = tmp_path / "selected.bin"
        try:
            if alias == "hardlink":
                os.link(dest, src)
            else:
                src.symlink_to(dest)
        except OSError:
            pytest.skip("host does not allow this kind of link")
    project = ForkProject(tmp_path)
    assert project.place_game(src, "game.bin", move=move) == dest
    assert src.exists() and dest.read_bytes() == data
    dest.write_bytes(b"wrong version")
    with pytest.raises(ForkError, match="Expected SHA-1:"):
        project.place_game(src, "game.bin", move=move)


def test_an_already_placed_file_target_is_left_in_place(tmp_path):
    dest = tmp_path / "baserom.bin"
    dest.write_bytes(b"synthetic input")
    (tmp_path / ".decomp.wtf.json").write_text(json.dumps({"game": {"path": "baserom.bin"}}))
    assert ForkProject(tmp_path).place_game(dest, dest.name) == dest
    assert dest.read_bytes() == b"synthetic input"


@pytest.mark.parametrize("fmt", ["z64", "v64", "n64", "container", "truncated"])
def test_n64_mismatch_reports_source_hash_byte_order_and_conversion(tmp_path, fmt):
    expected = b"\x80\x37\x12\x40" + bytes(range(64))
    wrong = expected[:-1] + b"\xff"
    if fmt in ("v64", "n64"):
        width = 2 if fmt == "v64" else 4
        supplied = b"".join(wrong[i:i + width][::-1] for i in range(0, len(wrong), width))
    elif fmt == "container":
        supplied = b"not a raw cartridge image"
    elif fmt == "truncated":
        supplied = b"\x37\x80\x40\x12x"
    else:
        supplied = wrong
    source = tmp_path / "uploaded.arc"  # detect by header, never the supplied extension
    source.write_bytes(supplied)
    digest = hashlib.sha1(expected).hexdigest()
    (tmp_path / ".decomp.wtf.json").write_text(json.dumps({"game": {"path": "baserom.z64", "sha1": digest}}))
    with pytest.raises(ForkError) as error:
        ForkProject(tmp_path).place_game(source, source.name)
    message = str(error.value)
    assert f"Expected SHA-1: {digest}" in message
    assert f"Supplied file SHA-1: {hashlib.sha1(supplied).hexdigest()}" in message
    if fmt in ("v64", "n64"):
        assert f"attempted from .{fmt} to big endian" in message
        assert f"Big-endian (.z64) SHA-1: {hashlib.sha1(wrong).hexdigest()}" in message
    elif fmt == "z64":
        assert "already big endian" in message
    elif fmt == "container":
        assert "not recognized as a raw N64 ROM" in message and "not attempted" in message
        assert "archive or container" in message
    else:
        assert "failed:" in message and "truncated" in message
    assert source.read_bytes() == supplied
    assert not (tmp_path / "baserom.z64").exists()


def test_successful_n64_conversion_logs_both_hashes(tmp_path):
    original = b"\x80\x37\x12\x40" + bytes(range(64))
    supplied = b"".join(original[i:i + 2][::-1] for i in range(0, len(original), 2))
    source = tmp_path / "copy.v64"
    source.write_bytes(supplied)
    (tmp_path / ".decomp.wtf.json").write_text(json.dumps(
        {"game": {"path": "baserom.z64", "sha1": hashlib.sha1(original).hexdigest()}}))
    log = []
    dest = ForkProject(tmp_path).place_game(source, source.name, log=log.append)
    assert dest.read_bytes() == original and source.read_bytes() == supplied
    assert hashlib.sha1(supplied).hexdigest() in log[0]
    assert hashlib.sha1(original).hexdigest() in log[0]
    assert "attempted from .v64" in log[0]
