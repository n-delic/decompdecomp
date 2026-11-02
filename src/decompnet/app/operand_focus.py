"""Advisory literal-offset overlap for operand repairs; no invented source/assembly mapping."""
import re


def offsets(rows):
    result = set()
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) != 3 or not all(isinstance(x, str) for x in row):
            return set()
        if row[0] == ' ':
            continue
        for text in row[1:]:
            for hit in re.finditer(r'(?<![\w])(-?(?:0x[\da-fA-F]+|\d+))\(', text):
                result.add(int(hit[1], 16 if '0x' in hit[1] else 10))
    return result


def weight(text, evidence):
    # Explicit pointer offsets only. Array stack positions and compiler registers are not
    # inferable from C names, so unknown correspondence keeps the ordinary source-order tie.
    numbers = {int(h[1], 16 if '0x' in h[1] else 10)
               for h in re.finditer(r'\+\s*\(?\s*(0x[\da-fA-F]+|\d+)\b', text)}
    return len(numbers & evidence)
