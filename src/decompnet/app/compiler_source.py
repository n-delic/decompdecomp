"""compiler_source: a helper reads one function of a GCC pass from the compiler's installed source.

Hand sessions finish the last rows by reading the pass that decides them (sched.c `birthing_insn_p`, local-alloc.c
`qty_compare`, global.c `find_reg`, loop.c `move_movables`) and writing the C that pass keys on. Helpers were told
where the source is (compiler_passes' last line) but had no tool that reads files outside a project. This returns one
function's definition (or a macro's), with the comment above it, from `toolchains.source_for(compiler)`, the read-only
copy installed beside the compiler; bounded in size. It reads no project, so it works for every console and mode
whose compiler has a public source (GCC and EGCS builds); proprietary compilers have none."""
import re

from ..network import NetworkError

MAX_CHARS = 12000
COMMENT_LINES = 30
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,80}$")


def _files(root):
    for pattern in ("*.c", "gcc/*.c", "*.h", "gcc/*.h", "config/mips/*.c", "gcc/config/mips/*.c", "config/mips/*.h",
                    "gcc/config/mips/*.h"):
        yield from sorted(root.glob(pattern))


def _excerpt(lines, at):
    """The definition starting at line `at` (its name at column 0, GNU style), with its return type and the comment
    above it, through the first `}` at column 0."""
    start = at
    if start > 0 and lines[start - 1].strip() and not lines[start - 1].rstrip().endswith((";", "}", "*/")):
        start -= 1  # the return type on the line above
    above = start - 1
    if above > 0 and not lines[above].strip():
        above -= 1  # GNU style leaves a blank line between the comment and the definition
    if above >= 0 and lines[above].rstrip().endswith("*/"):
        top = above
        while top > 0 and "/*" not in lines[top] and start - top < COMMENT_LINES:
            top -= 1
        start = top
    end = at
    while end < len(lines) and not lines[end].startswith("}"):
        end += 1
    return lines[start:end + 1], start


def _macro(lines, name):
    for i, line in enumerate(lines):
        if re.match(rf"#\s*define\s+{re.escape(name)}\b", line):
            end = i
            while end < len(lines) - 1 and lines[end].rstrip().endswith("\\"):
                end += 1
            return lines[i:end + 1], i
    return None


def find(root, name):
    """(relative file, first line number, text lines) of `name`'s definition in the source tree, or None."""
    definition = re.compile(rf"^{re.escape(name)}\s*\(")
    macro = None
    for path in _files(root):
        try:
            lines = path.read_text(encoding="latin-1").split("\n")  # not splitlines: GCC sources hold form feeds
        except OSError:
            continue
        for i, line in enumerate(lines):
            if definition.match(line):
                text, start = _excerpt(lines, i)
                return str(path.relative_to(root)), start + 1, text
        if macro is None and (found := _macro(lines, name)):
            macro = (str(path.relative_to(root)), found[1] + 1, found[0])
    return macro


def read(compiler, function_name, root=None):
    compiler = str(compiler or "").strip()
    name = str(function_name or "").strip()
    if not compiler or not NAME.match(name):
        raise NetworkError('Name an installed compiler and one C function or macro of its passes, e.g. '
                           'compiler_source("gcc-2.7.2-kmc", "birthing_insn_p").')
    if root is None:
        from .. import toolchains
        try:
            root = toolchains.source_for(compiler)
        except KeyError:
            root = None
        if root is None:
            raise NetworkError(f"No source is installed for {compiler}: proprietary compilers (IDO, Metrowerks, SN's "
                               "own, Visual C++) have none, and a free one's downloads with it. compiler_passes' last "
                               "line says which.")
    found = find(root, name)
    if found is None:
        raise NetworkError(f"{name} isn't defined in {compiler}'s source (searched its .c and .h files). Pass names: "
                           "sched.c adjust_priority/birthing_insn_p/rank_for_schedule, local-alloc.c qty_compare/"
                           "update_equiv_regs, global.c find_reg/set_preference, loop.c move_movables/combine_movables.")
    where, line, text = found
    body = "\n".join(text)
    cut = len(body) > MAX_CHARS
    if cut:
        body = body[:MAX_CHARS].rsplit("\n", 1)[0]
    return (f"{compiler} source, {where}:{line} ({len(text)} lines{', cut' if cut else ''}; the nearest public "
            f"release or a community reconstruction, read-only):\n```c\n{body}\n```"
            + ("\n(cut at the size limit: ask for a callee by name for the rest)" if cut else ""))


def run(net, agent, slug, compiler, function_name):
    if compiler in ("", "current"):
        raise NetworkError('Name the compiler ID (the brief\'s "compiler ID" line), e.g. "gcc-2.7.2-kmc".')
    return read(compiler, function_name)
