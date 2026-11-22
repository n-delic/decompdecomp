"""similar.py (the match-function skill): the app's find_similar look-alikes with the saved C of the best matched ones,
over a session's MCP connection (a stub gateway here; setup.session_gateway finds the session)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"


def load(name):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"skill_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


similar = load("similar")
setup = similar.setup

ROM_TEXT = """Functions that look like func_8001:
- func_8002  93% similar  matched  (40 insns, 0x80002000)
- func_8003  81% similar  best 120  (38 insns, 0x80003000)
- func_8004  70% similar  matched  (44 insns, 0x80004000)
- func_8005  65% similar  not started  (41 insns, 0x80005000)

Other games and local SDK signatures (unverified suggestions):
- osSendMesg in other: sdk, 90% similarity

Matched donor func_8002; source SHA-256 abc.
Complete matching C:
```c
- not a hit line
```"""
FORK_TEXT = "Functions that look like f (index covers 3 of 3 source files):\n- Foo::bar  88% similar  matched  (src/a.c, 20 insns)\n"


class Gateway:
    def __init__(self, text, sources=None):
        self.text, self.sources, self.calls = text, sources or {}, []

    def call(self, tool, arguments):
        self.calls.append((tool, arguments))
        if tool == "find_similar":
            return self.text
        if arguments["function"] not in self.sources:
            raise setup.McpError("get_function: no saved source")
        return self.sources[arguments["function"]]


def test_parse_reads_rom_and_project_lines_and_stops_at_other_games():
    hits = similar.parse(ROM_TEXT)
    assert [(h["name"], h["percent"], h["matched"], h["score"]) for h in hits] == [
        ("func_8002", 93, True, None), ("func_8003", 81, False, 120), ("func_8004", 70, True, None),
        ("func_8005", 65, False, None)]
    assert hits[0]["rest"] == "(40 insns, 0x80002000)"
    assert [h["name"] for h in similar.parse(FORK_TEXT)] == ["Foo::bar"]


def test_matched_look_alikes_are_listed_with_the_sources_of_the_best_few():
    gateway = Gateway(ROM_TEXT, {"func_8002": "void func_8002(void) { a(); }\n", "func_8004": "void func_8004(void) {}\n"})
    lines = []
    assert similar.run(gateway, "demo", "func_8001", 10, 1, False, None, 6000, lines.append) == 0
    text = "\n".join(lines)
    assert "93%  matched" in text and "func_8004" in text and "func_8003" not in text
    assert "--- func_8002 (93% similar, matched) ---\nvoid func_8002(void) { a(); }" in text and "func_8004(void) {}" not in text
    assert gateway.calls[0] == ("find_similar", {"function": "func_8001", "limit": 10})
    assert gateway.calls[1] == ("get_function", {"function": "func_8002", "section": "matched", "limit": 6000})
    assert len(gateway.calls) == 2


def test_all_lists_unmatched_ones_and_a_missing_source_is_said_not_fatal():
    lines = []
    similar.run(Gateway(ROM_TEXT), "demo", "func_8001", 99, 2, True, None, 6000, lines.append)
    text = "\n".join(lines)
    assert "best 120" in text and "not started" in text and text.count("(no saved source") == 2


def test_with_no_matched_look_alike_it_says_so_and_still_lists_the_rest():
    lines = []
    text = "- g1  60% similar  best 5  (9 insns)\n"
    assert similar.run(Gateway(text), "demo", "g", 5, 3, False, None, 100, lines.append) == 0
    assert "none matched yet" in lines[0] and "best 5" in "\n".join(lines)


def test_no_look_alikes_exits_one_with_the_apps_sentence():
    lines = []
    assert similar.run(Gateway("No look-alikes found for g."), "demo", "g", 5, 3, False, None, 100, lines.append) == 1
    assert lines == ["No look-alikes found for g."]


def test_out_writes_each_source_to_a_file_and_refuses_odd_names(tmp_path):
    gateway = Gateway("- ok_name  90% similar  matched  (1 insns)\n- ../evil  80% similar  matched  (1 insns)\n",
                      {"ok_name": "int ok_name(void){return 0;}\n", "../evil": "x"})
    lines = []
    similar.run(gateway, "demo", "f", 5, 5, False, str(tmp_path / "out"), 100, lines.append)
    assert (tmp_path / "out" / "ok_name.c").read_text().startswith("int ok_name")
    assert not (tmp_path / "evil.c").exists() and "not written" in "\n".join(lines)


def test_main_asks_for_at_most_fifteen_and_reports_a_refused_call(capsys):
    gateway = Gateway(ROM_TEXT, {"func_8002": "int x;"})
    assert similar.main(["demo", "func_8001", "--limit", "99", "--sources", "0"], gateway=gateway) == 0
    assert gateway.calls == [("find_similar", {"function": "func_8001", "limit": 15})]

    class Refusing:
        def call(self, tool, arguments):
            raise setup.McpError("find_similar: no function g")

    with pytest.raises(SystemExit, match="no function g"):
        similar.main(["demo", "g"], gateway=Refusing())


def make_session(root, holder, slug, token="t"):
    folder = root / f"dd-{holder}"
    folder.mkdir(parents=True)
    (folder / "connection.json").write_text(json.dumps({"holder": holder, "slug": slug, "gateway_url": "http://127.0.0.1:1/mcp",
                                                        "token": token}))


def test_session_gateway_picks_the_one_session_of_the_project_and_never_guesses_between_several(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "TMP", tmp_path)
    monkeypatch.setattr(setup.Gateway, "start", lambda self: None)
    make_session(tmp_path, "a", "demo", "ta")
    make_session(tmp_path, "b", "other", "tb")
    gateway, info = setup.session_gateway("demo")
    assert info["holder"] == "a" and gateway.token == "ta"
    make_session(tmp_path, "c", "demo", "tc")
    with pytest.raises(SystemExit, match="several sessions match"):
        setup.session_gateway("demo")
    assert setup.session_gateway("demo", "c")[1]["holder"] == "c"
    with pytest.raises(SystemExit, match="no setup.py session"):
        setup.session_gateway("nothing")
    with pytest.raises(SystemExit, match="no setup.py session"):
        setup.session_gateway("demo", "zzz")
