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
