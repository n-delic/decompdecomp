"""Network._context returns a database row (sqlite3.Row), which has no .get: reading it that way raised for every
deep search that reached random rewrites without a header in hand (122 times in one app's log)."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'src' / 'decompnet'
CALL = r"(?:self\.)?net\._context\((?:[^()]|\([^()]*\))*\)"
ROW_GET = re.compile(rf"(?<!dict)\({CALL}(?: or \{{\}})?\)\.get\(|(?<![(\w]){CALL}\.get\(")


def test_no_code_calls_get_on_a_context_row():
    offenders = [f'{path.relative_to(SRC)}:{number}' for path in SRC.rglob('*.py')
                 for number, line in enumerate(path.read_text().splitlines(), 1)
                 if ROW_GET.search(line)]
    assert not offenders, offenders


def test_the_pattern_catches_the_old_line():
    old = "simple_header = header or ('' if fork else (self.net._context(proj['id'], fn['unit']) or {}).get('header', ''))"
    fixed = "simple_header = header or ('' if fork else dict(self.net._context(proj['id'], fn['unit']) or {}).get('header', ''))"
    assert ROW_GET.search(old) and not ROW_GET.search(fixed)
    assert ROW_GET.search("x = net._context(pid, unit).get('header')")
