"""Actual referenced object declarations, never copied initializers or guessed types."""
import re

from ..forks.csource import _blank

IDENT = r'[A-Za-z_]\w*'
MAX_DECLARATIONS = 64


def statements(text):
    """Top-level declarations only; bodies and preprocessor lines cannot become externs."""
    clean = _blank(text)
    clean = re.sub(r'(?m)^[ \t]*#[^\n]*(?:\\\n[^\n]*)*', lambda m:' '*len(m[0]), clean)
    start = depth = 0
    function = False
    for index, char in enumerate(clean):
        if char == '{':
            if depth == 0:function = clean[start:index].rstrip().endswith(')') and '=' not in clean[start:index]
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0 and function:start = index + 1;function = False
        elif char == ';' and depth == 0:
            yield clean[start:index+1]
            start = index + 1


def objects(text):
    result = {}
    for statement in statements(text):
        head = statement.split('=', 1)[0].strip().removesuffix(';').strip()
        if any(c in head for c in '(){},') or re.match(r'(?:typedef|enum)\b', head):continue
        match = re.fullmatch(r'(.+?[\s*])('+IDENT+r')\s*((?:\[[^\]]*\]\s*)*)', head, re.S)
        if not match:continue
        prefix = re.sub(r'\b(?:static|extern)\s+', '', match[1]).strip()
        if not prefix or prefix in {'struct', 'union'}:continue
        declaration = 'extern '+prefix+' '+match[2]+match[3].strip()+';'
        if match[2] in result and result[match[2]] != declaration:result[match[2]] = None
        else:result[match[2]] = declaration
    return {name:value for name,value in result.items() if value}


def referenced(reference, source, header):
    used = set(re.findall(IDENT, _blank(source)))
    present = set(objects(header)) | set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')\b',_blank(header)))
    selected = [declaration for name,declaration in objects(reference).items()
                if name in used and name not in present]
    return '\n'.join(selected[:MAX_DECLARATIONS])
