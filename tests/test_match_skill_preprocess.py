"""preprocess.py (the match-function skill): comment-free source for the pinned classic cpp, which aborts on comments."""
import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("skill_preprocess", SCRIPTS / "preprocess.py")
preprocess = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preprocess)

SOURCE = """\
/* m2c's note, with a "quote
   spanning lines */
int f(int x) {
    // don't touch \\
       the continued line, it's still a comment
    char *s = "// not a comment, /* nor this */";
    char c = '"'; /* a quote char above, an apostrophe's here */
    return x; // it's done
}
"""


def test_every_comment_is_blanked_and_strings_newlines_and_columns_stay():
    out = preprocess.strip_comments(SOURCE)
    assert out.count("\n") == SOURCE.count("\n") and len(out) == len(SOURCE)
    assert "don't" not in out and "m2c" not in out and "it's done" not in out and "apostrophe" not in out
    assert 'char *s = "// not a comment, /* nor this */";' in out and "char c = '\"';" in out
    assert "int f(int x) {" in out and "return x;" in out
    assert [line.strip() for line in out.splitlines()][:2] == ["", ""]


def test_the_command_line_writes_the_stripped_file(tmp_path, capsys):
    source = tmp_path / "c.c"
    source.write_text(SOURCE)
    out = tmp_path / "c.nc.c"
    preprocess.main([str(source), "-o", str(out)])
    assert out.read_text() == preprocess.strip_comments(SOURCE)
    preprocess.main([str(source)])
    assert capsys.readouterr().out == preprocess.strip_comments(SOURCE)


# --- preprocessing as the app does (compiler.preprocess_candidate, the judge worker's "preprocess" op) ---

STUB_DRIVER = """#!/bin/sh
# A stand-in for the old gcc driver: -E copies the source to -o, -S -v reports a cc1 line the way the real one does.
mode=; out=; src=; verbose=
while [ $# -gt 0 ]; do
  case "$1" in -E|-S) mode=$1;; -v) verbose=1;; -o) out=$2; shift;; -*) ;; *) src=$1;; esac
  shift
done
case "$mode" in
  -E) printf '# 1 "%s"\\n' "$src" > "$out"; cat "$src" >> "$out";;
  -S) [ -n "$verbose" ] && echo " $TC/cc1 /tmp/ccAbCdEf.i -quiet -G0 -O2 -o /tmp/ccGhIjKl.s" >&2; : > "$out";;
esac
"""


def _config(tmp_path):
    from decompnet.compiler import CompilerConfig
    tc = tmp_path / "tool dir"  # (the toolchains folder holds a space on macOS)
    tc.mkdir()
    driver = tc / "gcc"
    driver.write_text(STUB_DRIVER.replace("$TC", str(tc)))
    driver.chmod(0o755)
    return CompilerConfig(cmd=[str(driver), f"-B{tc}/", "-c", "-G0", "-O2", "-I{include}", "{src}", "-o", "{obj}"],
                          sandbox="none"), tc


def test_the_candidate_goes_through_the_drivers_preprocess_step_and_names_the_cc1_command(tmp_path):
    from decompnet import compiler
    cfg, tc = _config(tmp_path)
    reply = compiler.preprocess_candidate(cfg, "typedef int s32;\n", "s32 f(void) { return 1; }")
    assert "error" not in reply
    assert "typedef int s32;" not in reply["text"] and '#include "context.h"' in reply["text"]  # the stub doesn't expand
    assert "s32 f(void) { return 1; }" in reply["text"] and '#line 1 "func.c"' in reply["text"]
    assert reply["cc1"] == [f"{tc}/cc1", "IN.i", "-quiet", "-G0", "-O2", "-o", "OUT.s"]


def test_a_command_that_is_not_a_gcc_style_driver_says_so(tmp_path):
    from decompnet import compiler
    from decompnet.compiler import CompilerConfig
    reply = compiler.preprocess_candidate(CompilerConfig(cmd=["mwcc", "{src}"], sandbox="none"), "", "int f(void);")
    assert "error" in reply and "-c" in reply["error"]


def test_the_cc1_line_fills_in_names_and_quotes_paths_with_spaces():
    line = preprocess.cc1_line(["/tool dir/cc1", "IN.i", "-O2", "-o", "OUT.s"], "a.i", "a.s")
    assert line.endswith("'/tool dir/cc1' a.i -O2 -o a.s")
    assert "passes.py" in preprocess.cc1_line(None, "a.i", "a.s")


def test_the_worker_op_runs_the_app_preprocess(tmp_path):
    from types import SimpleNamespace

    from decompnet import compiler, hosted_judge, project, toolchains
    cfg, _ = _config(tmp_path)
    seen = {}
    net = SimpleNamespace(cfg=lambda proj: {}, _context=lambda pid, unit: {"header": "typedef int s32;\n"})
    originals = (hosted_judge.lookup, project.compiler_for, project.compiler_config, toolchains.installed,
                 compiler.preprocess_candidate)
    try:
        hosted_judge.lookup = lambda net, request: ({"id": 1, "root": str(tmp_path)}, {"vram": 0, "unit": "u"})
        project.compiler_for = lambda *_: {"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"]}
        project.compiler_config = lambda root, c: seen.setdefault("config", c) and cfg
        toolchains.installed = lambda tid: tmp_path
        compiler.preprocess_candidate = lambda c, header, source: {"text": header + source, "cc1": None, "log": ""}
        reply = hosted_judge.preprocess_report(net, {"slug": "s", "name": "f", "source": "int f;"})
        assert reply["text"] == "typedef int s32;\nint f;"
        assert seen["config"]["compiler"] == {"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"]}
        assert "error" in hosted_judge.preprocess_report(net, {"slug": "s", "name": "f", "source": " "})
    finally:
        (hosted_judge.lookup, project.compiler_for, project.compiler_config, toolchains.installed,
         compiler.preprocess_candidate) = originals
