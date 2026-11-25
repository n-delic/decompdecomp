"""Did the big-endian bitfield rewrite (build.big_endian_bitfields) keep every struct the size it was?

The rewrite re-declares bitfields so clang puts them where the console's compiler did. When it gets a run wrong the
struct changes size (a `u16` then five bytes as `u32 x : 8` went from 28 bytes to 32), and an array of them has the
wrong stride: one record's fields overwrite the next one's, far from the code that did it. A game's intro cutscene
faulted on a pointer of -1 and its title screen was drawn in two-pixel rows, and neither pointed at a struct.

So every source the rewrite changed is compiled twice for wasm32, as it was and as it is, and the size of every
record is compared (clang's own record layouts). The sizes were the console compiler's too in every case found so far:
the rewrite moves bits inside a unit, never the unit. A difference is a warning in the build's log naming the record,
the file and both sizes. It is not an error: where the original's size was clang's guess and the rewrite is right, a
warning is all there is to say, and a game that builds must go on building."""
from __future__ import annotations

import concurrent.futures
import re
import tempfile
from pathlib import Path

_TYPE = re.compile(r"^Type:\s+(.+?)\s*$")
_SIZE = re.compile(r"^\s+Size:(\d+)\s*$")


def parse_layouts(dump: str) -> dict[str, int]:
    """{record name: size in bytes} from clang's `-fdump-record-layouts-simple` (sizes there are in bits)."""
    sizes: dict[str, int] = {}
    name = None
    for line in dump.splitlines():
        if m := _TYPE.match(line):
            name = m.group(1)
        elif name and (m := _SIZE.match(line)):
            sizes[name] = int(m.group(1)) // 8
            name = None
    return sizes


def top_level_records(text: str) -> list[str]:
    """The types a file declares at its top level, as C spells them: `Name` for `typedef struct {...} Name;`,
    `struct Tag` for `struct Tag {...};`. Clang lays a record out only when something needs its size."""
    names, depth, typedef = [], 0, False
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                head = text[max(text.rfind(";", 0, i), text.rfind("}", 0, i)) + 1:i]
                typedef = bool(re.search(r"\btypedef\b", head))
                if not typedef and (m := re.search(r"\b(struct|union)\s+(\w+)\s*$", head)):
                    names.append(f"{m.group(1)} {m.group(2)}")
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and typedef and (m := re.match(r"\s*(\w+)\s*;", text[i + 1:i + 80])):
                names.append(m.group(1))
    return names


def sizes_of(run, zig, flags, text: str, scratch: Path, include: Path, env=None, probe=()) -> dict[str, int] | None:
    """The sizes of the records `text` declares (and the headers it includes), or None when clang printed nothing
    (the file doesn't parse). `zig cc -fsyntax-only` exits with an error even when it parsed, so only the dump counts."""
    src = scratch / "probe.c"
    src.write_text(text + "\n" + "".join(f"unsigned long _size_{n} = sizeof({t});\n"
                                         for n, t in enumerate(sorted({*top_level_records(text), *probe}))),
                   encoding="utf-8")
    done = run([zig, "cc", *flags, "-I", include, "-fsyntax-only", "-Xclang", "-fdump-record-layouts-simple", src],
               env=env)
    return parse_layouts(done.stdout + done.stderr) or None


def changed_records(before: dict[str, int] | None, after: dict[str, int] | None) -> list[tuple[str, int, int]]:
    """[(record, size before, size after)] for every record both compiles have whose size differs."""
    if before is None or after is None:
        return []
    return [(name, before[name], after[name]) for name in sorted(before.keys() & after.keys())
            if before[name] != after[name]]


def check(run, zig, flags, out: Path, originals: dict[str, str], headers: dict[str, str], log, env=None) -> list[str]:
    """Compile each file in `originals` ({name in out/src: its text before the rewrite}) beside the one the rewrite
    wrote, with `headers` ({name in out/include: text before the rewrite}) for the first, and warn about each record
    whose size differs. Returns the warnings."""
    if not originals:
        return []
    warnings: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="bitfield-layout-") as scratch:
        before_include = Path(scratch) / "include"
        before_include.mkdir()
        for folder in (out / "include",):
            for header in folder.glob("*"):
                if header.is_file():
                    (before_include / header.name).write_text(headers.get(header.name, header.read_text(
                        encoding="utf-8", errors="replace")), encoding="utf-8")
        for name, text in headers.items():
            (before_include / name).write_text(text, encoding="utf-8")

        def work(name: str):
            folder = Path(tempfile.mkdtemp(dir=scratch))
            after_text = (out / "src" / name).read_text(encoding="utf-8", errors="replace")
            names = top_level_records(originals[name])
            return name, changed_records(sizes_of(run, zig, flags, originals[name], folder, before_include, env, names),
                                         sizes_of(run, zig, flags, after_text, folder, out / "include", env, names))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for name, differences in pool.map(work, sorted(originals)):
                for record, was, now in differences:
                    warnings.setdefault(record, f"{record} is {now} bytes after the bitfield rewrite, {was} before "
                                                f"(in {name}): arrays of it would have the wrong stride")
    for line in warnings.values():
        log("layout check: " + line)
    return list(warnings.values())
