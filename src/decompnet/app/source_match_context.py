"""Read header macro definitions for local source proposal guards."""
import re

ID = r"[A-Za-z_]\w*"

def macros(text):
    out = {}
    lines = text.splitlines(True)
    i = 0
    while i < len(lines):
        start = i
        hit = re.match(r'^\s*#\s*define\s+('+ID+r')\b', lines[i])
        i += 1
        if not hit:
            continue
        while lines[i-1].rstrip('\r\n').endswith('\\') and i < len(lines):
            i += 1
        out[hit[1]] = (''.join(lines[start:i]), start+1)
    return out
