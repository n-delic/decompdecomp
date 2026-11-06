"""Actionable compiler diagnostics without modifying declarations or guessing types."""
import re


def hints(text, source):
    if not any(marker in text for marker in ('COMPILE ERROR','BUILD ERROR')):
        return ''
    advice = []
    lines = source.splitlines()
    decl = re.compile(r'^\s*(?:(?:unsigned|signed|const)\s+)*(?:[su](?:8|16|32|64)|f32|f64|int|float|double|char|short|long|size_t)\s+\**\s*[A-Za-z_]\w*')
    if 'parse error' in text or 'declaration' in text.lower():
        for i,line in enumerate(lines):
            prior = lines[i-1].strip() if i else ''
            if decl.match(line) and prior.endswith(';') and not decl.match(prior) and '=' in prior:
                advice.append(f'Possible C89 declaration after a statement at candidate line {i+1}: {line.strip()[:140]}. Declare locals at the start of this block; keep assignments at their original execution point.')
                break
    if re.search(r'for\s*\(\s*(?:int|unsigned|s32|u32|size_t)\s+\w+', source):
        advice.append('This candidate declares a loop variable inside for(...). For a C89 compiler, declare it at the start of its block, then assign it in for(...).')
    if not advice and 'parse error' in text and re.search(r'(?m)^\s*(?:int|s32|u32|float|double|char|unsigned)\s+\w+', source):
        advice.append('Check C89 declaration placement: declarations must precede statements in each block. Also check the first reported line for a missing delimiter; later errors may be cascades.')
    names = list(dict.fromkeys(re.findall(r"[`‘']([A-Za-z_]\w*)['’] undeclared", text) + re.findall(r"(?:undeclared identifier|unknown type name) ['‘]([A-Za-z_]\w*)['’]", text)))[:3]
    local_names = [n for n in names if re.fullmatch(r'(?:__repair_\w*|new_var\d*|temp_[A-Za-z0-9_]+|var_[A-Za-z0-9_]+)', n) or re.search(r'\b(?:[su](?:8|16|32|64)|int|float|double|char|short|long)\s+\**\s*'+re.escape(n)+r'\b',source)]
    if local_names:
        advice.append('Candidate-local identifier issue: '+', '.join(local_names)+'. Check its declaration and scope in this candidate before fetching shared headers.')
    names = [n for n in names if n not in local_names]
    if names:
        advice.append('Read get_context(function=...) and get_function(section="declarations") for '+', '.join(names)+'. Reuse proven declarations; do not invent symbol addresses or types.')
    if 'conflicting types' in text or 'redefinition' in text:
        advice.append('Compare the candidate declarations with get_context(function=...). Remove or correct the candidate-local duplicate; do not change the shared header just to silence this error.')
    if not advice:
        advice.append('Fix the first compiler error before tuning assembly. Get the function-specific context if its declarations are missing; follow-up errors can be cascades.')
    advice.append('To abandon this failing edit without losing recorded attempts, use get_candidate(base="last_compiling"); base="saved_best" restores the saved baseline instead.')
    return '\nCompiler error guidance (advisory):\n'+'\n'.join('- '+line for line in (advice[:3]+advice[-1:] if len(advice)>3 else advice))
