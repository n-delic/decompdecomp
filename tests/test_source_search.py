"""Source search semantics and bounded reads, without a compiler, a game or a grep executable."""

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app.forkengine import ForkEngine
from decompnet.app.tools import HEAVY_TOOLS
from decompnet.forks.project import STATE


@pytest.fixture
def search(tmp_path):
    engine = SimpleNamespace(get=lambda slug: (None, SimpleNamespace(root=tmp_path), {}))
    return lambda pattern, glob="": ForkEngine.search(engine, "game", pattern, glob)


@pytest.mark.parametrize("pattern,expected", [
    (r"\d", "digits 123"),
    (r"(?<=digits )\d+", "digits 123"),
    ("-i", "option -i"),
    ("func(", "func(argument)"),  # invalid regex falls back to a literal
    ("missing", None),
])
@pytest.mark.parametrize("glob", ["", "src/**/*.c"])
def test_search_keeps_python_regex_and_literal_semantics(search, tmp_path, pattern, expected, glob):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.c").write_text("ordinary word\ndigits 123\noption -i\nfunc(argument)\n")
    result = search(pattern, glob)
    if expected is None:
        assert result == "no matches"
    else:
        lines = result.splitlines()
        assert len(lines) == 1 and lines[0].endswith(": " + expected), result


def test_search_prunes_build_and_state_trees_and_doesnt_run_grep(search, tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.c").write_text("needle\n")
    for directory in ("build", ".git", STATE):
        nested = tmp_path / directory / "nested"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "hidden.c").write_text("needle\n")
    (tmp_path / "ignored.txt").write_text("needle\n")
    from decompnet.app import forkengine

    commands = []
    monkeypatch.setattr(forkengine.subprocess, "run", lambda *a, **kw: commands.append(a))
    visited = []
    walk = forkengine.os.walk

    def walking(root):
        for folder, dirs, names in walk(root):
            visited.append(Path(folder).relative_to(tmp_path).parts)
            yield folder, dirs, names

    monkeypatch.setattr(forkengine.os, "walk", walking)
    assert search("needle") == "src/main.c:1: needle"
    assert commands == []  # no regex dialect, subprocess buffering, console-window or tool-cap dependency
    assert not any({"build", ".git", STATE} & set(parts) for parts in visited)


def test_search_streams_only_the_first_eighty_matches(search, tmp_path, monkeypatch):
    source = tmp_path / "large.c"
    source.touch()
    opened = []
    original = Path.open

    class FirstEighty(io.StringIO):
        def read(self, *args):
            pytest.fail("search read the entire file before limiting matches")

        def __next__(self):
            assert self.tell() < 80 * len("needle\n"), "search read past its 80-match limit"
            return super().__next__()

    def opening(path, *args, **kwargs):
        if path == source:
            stream = FirstEighty("needle\n" * 81)
            opened.append(stream)
            return stream
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opening)
    result = search("needle").splitlines()
    assert len(result) == 81 and result[79] == "large.c:80: needle"
    assert result[-1] == "... (more matches; narrow the pattern)"
    assert len(opened) == 1 and opened[0].closed


def test_source_search_shares_the_heavy_tool_limiter():
    assert "search_code" in HEAVY_TOOLS
    assert {"claim_function", "release_function", "read_file"}.isdisjoint(HEAVY_TOOLS)
