"""Pointer and `void` spellings in m2c's valid-syntax C that its own compiler refuses.

m2c prints what it can tell and leaves the types it can't as `void`, a bare number or a pointer sum. A C compiler
rejects each of those even though the instruction behind it is plain, so the draft never compiled and the function
stayed out of the Deep Permuter. On one N64 game these were 60 of the 406 functions without scored C:

- `*(void *)0xA4600010 & 3` (a hardware register polled in a loop), `*M2C_FIELD(p, void **, 4) = NULL`: a `void`
  dereferenced. A word read or written through a number or a `void` pointer is a word: `s32`.
- `*(0x43A7C8 + (i * 0x10))`, `*((i * 4) + M2C_FIELD(p, s32 *, 0x2C))`: `*` of an integer sum.
- `*(&table + i)(args);`: a call through a table entry m2c typed as a number.
- `(u32) (&D_200500 + &D_A9F14)`: two addresses added, which C only allows as numbers.
- `&sp10[0]`: a stack buffer m2c indexes but never declared.

Only the spelling changes: the same instructions are meant, and what the search scores decides the rest. The module
reads C text only; it never compiles or judges.
"""
import re

_INTEGER_START = re.compile(r"0x[0-9A-Fa-f]+\b|\(\w+ \* (?:0x[0-9A-Fa-f]+|\d+)\)")
# What may stand before a unary `*`: an operator, an opening bracket, `return`, or a cast (`(M2C_UNK *) *(...)`).
_UNARY_BEFORE = re.compile(r"(?:^|[=(,;{?:&|!<>+\-*/%]|\breturn|\(\s*[\w ]+\*+\s*\))\s*$")


def _balanced(text: str, start: int) -> int | None:
    """Index just past the parenthesis group that opens at `text[start]`."""
    depth = 0
    for index in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[index], 0)
        if depth == 0:
            return index + 1
    return None


def _word_type(source: str, address: str) -> str:
    """The type the same literal address is already read with elsewhere in the draft, else a word."""
    found = re.search(rf"\*\(((?:u|s)(?:8|16|32)) \*\){re.escape(address)}\b", source)
    return found[1] if found else "s32"


def _void_dereferences(source: str) -> str:
    source = re.sub(r"\*\(void \*\)(0x[0-9A-Fa-f]+)\b", lambda m: f"*({_word_type(source, m[1])} *){m[1]}", source)
    return re.sub(r", void \*\*, ", ", M2C_UNK **, ", source)


def _integer_dereferences(source: str) -> str:
    """`*(0x43A7C8 + ...)` and `*((i * 4) + ...)`: the sum is an address held in a number."""
    out, index = [], 0
    for match in re.finditer(r"\*\(", source):
        start = match.start()
        if start < index or not _UNARY_BEFORE.search(source[max(0, start - 24):start]):
            continue
        # `(T *) *(...)` is a cast of the dereference, not of the sum: the `*` is still unary.
        end = _balanced(source, start + 1)
        inner = source[start + 2:end - 1] if end else ""
        if not end or not _INTEGER_START.match(inner) or not re.search(r"[+\-]", inner):
            continue
        out.append(source[index:start] + "*(s32 *)(" + inner + ")")
        index = end
    return "".join(out) + source[index:]


def _scalar_names(source: str) -> dict[str, str]:
    """{name: type} of the locals and parameters declared as plain integers (never `u32 *p`)."""
    return {m[2]: m[1] for m in re.finditer(r"\b(s8|u8|s16|u16|s32|u32|s64|u64|M2C_UNK\d*)\s+(\w+)\s*[;,)]", source)}


def _scalar_dereferences(source: str) -> str:
    """`temp = *var_a0;` and `*(arg1 + i) == x` where the name m2c dereferences is a number: the number holds an
    address, so it is read through a pointer to the type the result is assigned to (a word otherwise)."""
    scalars = _scalar_names(source)
    if not scalars:
        return source
    out, index = [], 0
    for match in re.finditer(r"\*", source):
        start = match.start()
        if start < index or not _UNARY_BEFORE.search(source[max(0, start - 24):start]):
            continue
        rest = source[start + 1:]
        if rest.startswith("("):
            end = _balanced(source, start + 1)
            inner = source[start + 2:end - 1] if end else ""
            first = re.match(r"[(\s]*(\w+)", inner)
            if not end or not first or first[1] not in scalars or not re.search(r"\s[+\-]\s", inner):
                continue
            operand = "(" + inner + ")"
        else:
            name = re.match(r"(\w+)\b(?![\[.(]|->)", rest)
            if not name or name[1] not in scalars:
                continue
            operand, end = name[1], start + 1 + len(name[1])
        line = source[source.rfind("\n", 0, start) + 1:start]
        assigned = re.match(r"\s*(\w+)\s*=\s*$", line)
        kind = scalars.get(assigned[1]) if assigned else None
        kind = kind if kind and not kind.startswith("M2C") else "s32"
        out.append(f"{source[index:start]}*({kind} *){operand}")
        index = end
    return "".join(out) + source[index:]


def _table_calls(source: str) -> str:
    """`*(&table + i)(a, b);` calls an address read from a table: the entry is a function pointer."""
    lines = []
    for line in source.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("*(") and (end := _balanced(stripped, 1)) and stripped[end:end + 1] == "(" \
                and stripped.rstrip().endswith(");"):
            indent = line[:len(line) - len(stripped)]
            line = f"{indent}((void (*)()) {stripped[:end]}){stripped[end:]}"
        lines.append(line)
    return "\n".join(lines)


def _address_sums(source: str) -> str:
    return re.sub(r"\(&(\w+) \+ &(\w+)\)", r"((u32) &\1 + (u32) &\2)", source)


def repair(source: str) -> str:
    """`source` with the spellings above made compilable; unchanged when it has none."""
    if not source:
        return source
    for step in (_void_dereferences, _integer_dereferences, _scalar_dereferences, _table_calls, _address_sums):
        source = step(source)
    return source
