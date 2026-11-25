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

# An operand that is a number: a literal, a scaled index, a word read from memory (`*(s32 *)0x430350`, a field read as s32).
_INTEGER_START = re.compile(r"0x[0-9A-Fa-f]+\b|\(\w+ \* (?:0x[0-9A-Fa-f]+|\d+)\)|\*\((?:s|u)32 \*\)"
                            r"|M2C_FIELD\(.*?, (?:s|u)32 \*, (?:0x[0-9A-Fa-f]+|\d+)\)")
# What may stand before a unary `*`: an operator, an opening bracket, `return`, or a cast (`(M2C_UNK *) *(...)`).
_UNARY_BEFORE = re.compile(r"(?:^|[=(,;{?:&|!<>+\-*/%]|\breturn|\(\s*[\w ]+\*+\s*\)|\(\s*(?:[us](?:8|16|32|64)|f32|f64|M2C_\w+)\s*\))\s*$")


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
        if not end or not any(_INTEGER_START.match(inner[i:]) for i in range(min(3, len(inner))) if i == 0 or inner[:i].strip("( ") == "") or not re.search(r"\s[+\-|]\s", inner):
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
        if re.match(r"\s*[\w ]+\(\s*$", source[source.rfind("\n", 0, start) + 1:start + 1]):
            continue  # a declaration (`u16 (*sp26C)[];`), not a dereference
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
    source = re.sub(r"\(&(\w+) \+ &(\w+)\)", r"((u32) &\1 + (u32) &\2)", source)
    # a number minus an address (`var_s0 - &D_80140000`) and a byte distance divided by a size (`(a - b) / 688`)
    source = re.sub(r"(?<=[\w)] )- &(\w+)\b", r"- (s32) &\1", source)
    source = re.sub(r"\((\w+) - (\w+)\) / (\d+)\b", r"((s32) \1 - (s32) \2) / \3", source)
    return re.sub(r"\b[su]24\b", "s32", source)  # (a 24-bit type m2c names for a packed field)


_PROTOTYPE = re.compile(r"^(?P<head>[^\n(){};=]*?\b(?P<name>\w+)\()(?P<params>[^;{}\n]*)(?P<tail>\);[ \t]*/\* extern \*/)$", re.M)


def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in text:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    return [*parts, current] if current.strip() or parts else []


def _argument_counts(source: str, name: str, skip: set[int]) -> set[int]:
    counts = set()
    for call in re.finditer(rf"(?<![\w.]){re.escape(name)}\(", source):
        if call.start() in skip:
            continue
        end = _balanced(source, call.end() - 1)
        if end:
            counts.add(len(_split_top_level(source[call.end():end - 1])))
    return counts


def _call_prototypes(source: str) -> str:
    """m2c prints one prototype per callee, from the call it understood best, and the same function is called
    elsewhere in the draft with fewer arguments (`f(f(x), 1)`, or `f()` for a callee whose argument the code never
    sets): a C compiler refuses the call. 36 of one game's 406 functions without scored C failed with 'too few
    arguments'. The code behind the call passes exactly those registers, as a call to a function declared without
    parameters (`f();`) does, so the prototype becomes that declaration."""
    skip = {m.start("name") for m in _PROTOTYPE.finditer(source)}
    for match in reversed(list(_PROTOTYPE.finditer(source))):
        params = match["params"].strip()
        if "..." in params or not params or params == "void":
            continue
        expected = len(_split_top_level(params))
        counts = _argument_counts(source, match["name"], skip)
        if counts and counts != {expected}:
            source = source[:match.start("params")] + source[match.end("params"):]
    return source


def repair(source: str) -> str:
    """`source` with the spellings above made compilable; unchanged when it has none."""
    if not source:
        return source
    for step in (_void_dereferences, _integer_dereferences, _scalar_dereferences, _table_calls, _address_sums, _call_prototypes):
        source = step(source)
    return source
