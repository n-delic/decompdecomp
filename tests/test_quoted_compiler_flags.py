"""Quoted compiler arguments survive probes, suggestions and bounded flag search; no game data needed."""

import ast
import shlex
from types import SimpleNamespace

import pytest

from decompnet import consoles, toolchains
from decompnet.app.engine.compilers import MAX_FLAG_VARIANTS, CompilerMixin, parse_flags
from decompnet.compiler import SourceRejected, check_source, compile_source
from decompnet.elfobj import extract_function
from decompnet.network import Agent, NetworkError
from decompnet.project import compiler_config
from decompnet.scoring import DiffResult

FLAGS = ["-O3,p", "-sdatathreshold", "8", "-pragma", "divbyzerocheck on"]


class Probe(CompilerMixin):
    def __init__(self):
        self.settings = {"platform": "ps2", "kind": "rom", "auto_compiler": False,
                         "compiler": {"toolchain": "mwcps2-2.3.3", "flags": FLAGS[:3]}}
        self.calls = []
        self.filecompilers = SimpleNamespace(applies=lambda slug: False)
        self.rangechecks = SimpleNamespace(tried=lambda *args: None, propose=lambda *args: "range check queued")

    def fork_slug(self, slug):
        return None

    def _project(self, slug):
        return {"id": 1, "slug": slug}

    def cfg(self, proj):
        return self.settings

    def _function(self, name, slug):
        return {"name": name, "vram": 0x100000}

    def _toolchain_named(self, cfg, name):
        return name

    def _judge_with(self, proj, fn, tc, flags, source):
        self.calls.append(list(flags))
        matched = flags == FLAGS
        return matched, DiffResult(0 if matched else 100, 1000, [], matched), ""

    def _update_cfg(self, slug, change):
        change(self.settings)

    def _check_compiler(self, proj, tc, flags, function, source):
        self.calls.append(list(flags))
        return "checked: matches the game"


def test_quoted_flags_round_trip_without_shell_expansion():
    for flags in [FLAGS, ['-DNAME="a b"', '-I', r'C:\SDK files\include'], ['-DVALUE=$(echo nope)', '-DOTHER=`id`']]:
        assert parse_flags(shlex.join(flags)) == flags
    assert parse_flags('-O3,p -pragma "divbyzerocheck on"') == [FLAGS[0], *FLAGS[3:]]
    assert parse_flags("") == []
    with pytest.raises(NetworkError, match="Quote arguments"):
        parse_flags('-pragma "unterminated')


def test_probe_and_suggestion_preserve_the_same_argument_list():
    net = Probe()
    agent = Agent(1, "test", "owner")
    said = net.try_compiler(agent, "game", "f", "mwcps2-2.3.3", shlex.join(FLAGS), source="int f(){return 1;}")
    assert "MATCHES" in said and net.calls == [FLAGS]
    # The tool's copyable suggestion uses a valid string literal, including embedded quotes.
    flags_literal = said.split("flags=", 1)[1].split(", function=", 1)[0]
    quoted = ast.literal_eval(flags_literal)
    assert parse_flags(quoted) == FLAGS
    net.suggest(agent, "game", "checked integer division", compiler="mwcps2-2.3.3", flags=quoted,
                function="f", ranges="0x100000-0x100020", source="int f(){return 1;}")
    assert net.calls[-1] == FLAGS
    assert net.settings["suggestions"][0]["flags"] == FLAGS
    assert net.settings["compiler"]["flags"] == FLAGS[:3]  # a probe or suggestion is not a global switch


def test_ps2_search_tests_division_checks_before_the_budget_runs_out():
    net = Probe()
    said = net.search_flags(Agent(1, "test", "owner"), "game", "f", source="int f(){return 1;}")
    assert net.calls[0] == FLAGS[:3] and net.calls[1] == FLAGS
    assert net.calls[2][-2:] == ["-pragma", "divbyzerocheck off"]
    assert len(net.calls) <= MAX_FLAG_VARIANTS and "MATCHES with" in said
    assert parse_flags(ast.literal_eval(said.split("flags=", 1)[1].split(", function=", 1)[0])) == FLAGS
    assert net.settings["compiler"]["flags"] == FLAGS[:3]


def test_division_check_variants_do_not_leak_to_other_compilers_or_duplicate_the_setting():
    console = consoles.get("ps2")
    assert console.extra_flag_variants("ee-gcc-2.96", ["-O2"]) == []
    assert consoles.get("psp").extra_flag_variants("mwccpsp-2.0", ["-O3"]) == []
    for enabled in ("on", "off"):
        base = [*FLAGS[:3], "-pragma", "unrelated on", "-pragma", f"divbyzerocheck {enabled}"]
        (other,) = console.extra_flag_variants("mwcps2-2.3.3", base)
        assert other[:5] == base[:5] and other.count("-pragma") == 2
        assert other[-1] != base[-1] and base[-1] == f"divbyzerocheck {enabled}"


def test_a_bounded_search_does_not_claim_to_exhaust_all_flags(monkeypatch):
    net = Probe()
    monkeypatch.setattr(net, "_judge_with", lambda *args: (False, DiffResult(100, 1000, [], False), ""))
    said = net.search_flags(Agent(1, "test", "owner"), "game", "f", source="int f(){return 1;}")
    assert "untested options may still matter" in said


def test_source_pragmas_are_still_rejected():
    with pytest.raises(SourceRejected, match="pragmas"):
        check_source('#pragma divbyzerocheck on\nint f(){return 1;}')


@pytest.mark.skipif(toolchains.installed("mwcps2-2.3.3") is None, reason="needs installed CodeWarrior PS2 2.3.3")
def test_installed_compiler_emits_the_divide_guard_only_when_requested(tmp_path):
    source = "int divide(short *p, int n) { return *p / (short)n; }"
    outputs = []
    for setting in ("off", "on"):
        flags = parse_flags(f'-O3,p -sdatathreshold 8 -pragma "divbyzerocheck {setting}"')
        config = compiler_config(tmp_path, {"compiler": {"toolchain": "mwcps2-2.3.3", "flags": flags}})
        result = compile_source(config, "", source)
        assert result.ok, result.log
        function = extract_function(result.obj, "divide")
        assert function is not None
        outputs.append(function.code)
    trap = (0x000001CD).to_bytes(4, "little")  # break 0,7
    assert trap not in outputs[0] and trap in outputs[1]
    assert outputs[0] != outputs[1]
