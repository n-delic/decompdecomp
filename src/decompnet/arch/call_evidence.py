"""Direct call operands in saved target assembly, across the supported processors.

Indirect calls and inlining need a human explanation; nearby addresses and draft C are not call evidence.
"""
import re

CALL = re.compile(r'^\s*(?:[0-9a-fx]+:\s*(?:[0-9a-f]{2,8}\s+)*)?(?:jal|jalx|bal|blx?(?:eq|ne|cs|cc|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)?(?:\.[nw])?|bla|call[lq]?|jmp|j|b)\s+([^;#\n]+)', re.I | re.M)


def direct(assembly, names, address=None):
    clean = re.sub(r'/\*.*?\*/', '', assembly or '', flags=re.S)
    clean = re.sub(r'(?m)#.*$|//.*$', '', clean)
    for hit in CALL.finditer(clean):
        operand = hit[1].strip()
        if operand.startswith(('*','[')):
            continue
        if any(re.search(r'(?<![\w.])'+re.escape(name)+r'(?![\w.])', operand) for name in names if name):
            return True
        if address is not None:
            tokens = re.findall(r'(?<!\w)(?:0x[0-9a-f]+|[0-9a-f]{8})(?!\w)', operand, re.I)
            if any(int(token,16)==address for token in tokens):
                return True
    return False
