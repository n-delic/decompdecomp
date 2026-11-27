"""A call tail that several blocks jump into with their stack arguments half stored, written out in each of them.

GCC merges the common end of calls: each arm of a switch stores the first stack arguments, jumps into the stores of the
rest and shares one `jal`. m2c reads the shared call once, so the stores made before each jump belong to no call and
vanish from the draft: on one N64 function with nine such arms the draft called an 18-argument function with ten
wrong ones, and each case lost its callback. Written out per arm (the delay slot first, then the tail through the
call, then a jump past it) every arm's call has all its arguments, and the compiler merges the tails again. The module
reads assembly text only and changes nothing m2c's input does not already say.
"""
import re

_LABEL = re.compile(r"^\s*(\.?L\w+):\s*$")
_INSN = re.compile(r"^\s*(?:/\*.*?\*/\s*)?([a-z][\w.]*)\b\s*(.*?)\s*$")
_FLOW = re.compile(r"^(?:j|jr|jal|jalr|b|b[a-z]+|bc1[tf]l?)$")
# a store above the four register arguments' home slots: an outgoing argument
_ARGUMENT_STORE = re.compile(r"^(?:sw|sh|sb|swc1|sdc1|sd)\s+\$\w+,\s*(0x[0-9A-Fa-f]+|\d+)\(\$(?:sp|29)\)")
MAX_TAIL = 48


def _instruction(line: str) -> tuple[str, str] | None:
    found = _INSN.match(line)
    return (found[1], found[2]) if found and not line.strip().endswith(':') else None


def _stores_argument(line: str) -> bool:
    found = _instruction(line)
    store = found and _ARGUMENT_STORE.match(f"{found[0]} {found[1]}")
    return bool(store and int(store[1], 0) >= 0x10)


def _tail(lines: list[str], start: int) -> tuple[list[str], int] | None:
    """The straight-line instructions from `start` through a `jal` and its delay slot, and the line after them."""
    body = []
    for index in range(start, len(lines)):
        found = _instruction(lines[index])
        if not found:
            continue  # a label inside the tail, or a blank line
        if found[0] == 'jal':
            for after in range(index + 1, len(lines)):
                slot = _instruction(lines[after])
                if slot:
                    if _FLOW.match(slot[0]):
                        return None
                    return [*body, lines[index], lines[after]], after + 1
            return None
        if _FLOW.match(found[0]) or len(body) >= MAX_TAIL:
            return None
        body.append(lines[index])
    return None


def _arm_stores_argument(lines: list[str], jump: int) -> bool:
    """Whether the block that ends in the jump at `jump` stores an outgoing argument since its last call or label."""
    for index in range(jump - 1, -1, -1):
        found = _instruction(lines[index])
        if _LABEL.match(lines[index]) or (found and _FLOW.match(found[0])):
            return False
        if _stores_argument(lines[index]):
            return True
    return False


def write_out(asm_text: str) -> str:
    """`asm_text` with each jump into a shared call tail replaced by that tail, when both halves store arguments."""
    lines = asm_text.splitlines()
    labels = {found[1]: index for index, line in enumerate(lines) if (found := _LABEL.match(line))}
    replace, new_labels = {}, {}
    for index, line in enumerate(lines):
        found = _instruction(line)
        if not found or found[0] not in ('j', 'b') or found[1] not in labels:
            continue
        slot = next((i for i in range(index + 1, len(lines)) if _instruction(lines[i])), None)
        tail = _tail(lines, labels[found[1]] + 1)
        if slot is None or tail is None:
            continue
        body, after = tail
        if not any(map(_stores_argument, body)) or not (
                _arm_stores_argument(lines, index) or _stores_argument(lines[slot])):
            continue
        # where the tail goes on: the label already there, or one made for it
        rest = after
        while rest < len(lines) and not lines[rest].strip():
            rest += 1
        if rest >= len(lines):
            continue
        there = _LABEL.match(lines[rest])
        target = there[1] if there else new_labels.setdefault(rest, f".Lcall_tail_{len(new_labels)}")
        replace[index] = (slot, [lines[slot], *body, f"j {target}", "nop"])
    if not replace:
        return asm_text
    out, skip = [], set()
    for index, line in enumerate(lines):
        if index in new_labels:
            out.append(f"{new_labels[index]}:")
        if index in replace:
            slot, written = replace[index]
            out.extend(written)
            skip.add(slot)
        elif index not in skip:
            out.append(line)
    return "\n".join(out) + ("\n" if asm_text.endswith("\n") else "")
