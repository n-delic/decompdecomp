"""callers.py (the match-function skill): the app's get_callers over a session's MCP connection (a stub gateway here)."""
import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"


def load(name):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"skill_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


callers = load("callers")

TEXT = """func_8001 is called from 2 places.
Result: value (s32, strong) - 1 of 2 callers read the result (caller_a): a int result

## caller_a (open)
- call: reads the result
  > /* 0 80000010 0C000000 */  jal        func_8001

## caller_b (matched) matched C: statement
- call: drops the result
"""


class Gateway:
    def __init__(self, text):
        self.text, self.calls = text, []

    def call(self, tool, arguments):
        self.calls.append((tool, arguments))
        return self.text


def test_parse_reads_the_verdict_and_the_callers():
    found = callers.parse(TEXT)
    assert (found["verdict"], found["type"], found["strength"]) == ("value", "s32", "strong")
    assert found["callers"] == ["caller_a", "caller_b"]
    assert callers.parse("Result: unknown - no direct callers")["verdict"] == "unknown"


def test_run_prints_the_apps_answer_and_asks_for_a_bounded_number_of_callers():
    gateway, lines = Gateway(TEXT), []
    assert callers.run(gateway, "func_8001", 99, lines.append) == 0
    assert lines[0].startswith("func_8001 is called from 2 places.")
    assert gateway.calls == [("get_callers", {"function": "func_8001", "limit": callers.MAX_LIMIT})]
    assert callers.run(Gateway("something else"), "f", 3, lambda _: None) == 1
