"""Source-only buffer and argument helpers shared by deterministic repairs."""
import re


def _split_top_level(text: str) -> list[str]:
    """`text` cut at the commas that aren't inside brackets."""
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        depth += (ch in "([{") - (ch in ")]}")
        cur += ch
    return parts + [cur]

def stack_array(draft: str, name: str, canonical=False) -> str | None:
    """Infer a byte buffer only from m2c's explicit address-of-index loop and literal bound.

    Never infer length from an arbitrary stack offset or grow an escaped scalar without a bounded loop.
    The caller keeps this as an alternative and still compiles/links it with the normal judge.
    """
    from ..forks.csource import _blank, find

    span = find(draft,name)
    if not span:
        return None
    clean = _blank(draft)
    opening = clean.find('{',*span)
    body = clean[opening:span[1]]
    if '#' in body or re.search(r'\bvolatile\b',body):
        return None
    for m in re.finditer(r'\b(u8|s8|char|unsigned\s+char)\s+(\w+)\s*;',body):
        typ,var=m[1],m[2]
        access=re.search(r'&\s*\(\s*&\s*'+re.escape(var)+r'\s*\)\s*\[\s*(\w+)\s*\]',body)
        if not access:
            continue
        index=access[1]
        bound=re.search(r'\bwhile\s*\(\s*'+re.escape(index)+r'\s*<\s*(0x[0-9a-fA-F]+|\d+)\s*\)',body[access.end():])
        if not bound or not re.search(r'\b'+re.escape(index)+r'\s*(?:\+=\s*1|\+\+)\s*;',body[:access.start()]):
            continue
        length=int(bound[1],0) if bound[1].startswith('0x') else int(bound[1])
        if not 2<=length<=4096 or not re.search(r'\b'+re.escape(index)+r'\s*=\s*0\s*;',body[:access.start()]):
            continue
        # All non-declaration uses must take the scalar's address, not read its byte value.
        uses=list(re.finditer(r'\b'+re.escape(var)+r'\b',body))
        if any(hit.start()!=m.start(2) and not re.search(r'&\s*$',body[:hit.start()]) for hit in uses):
            continue
        changed=draft[opening:span[1]]
        changed=changed[:m.start()]+f'{typ} {var}[{length}];'+changed[m.end():]
        changed=re.sub(r'&\s*\(\s*&\s*'+re.escape(var)+r'\s*\)\s*\[\s*'+re.escape(index)+r'\s*\]',f'&{var}[{index}]',changed)
        changed=re.sub(r'&\s*\b'+re.escape(var)+r'\b(?!\s*\[)',var,changed)
        if canonical:
            fill=re.search(r'\bdo\s*{\s*'+re.escape(index)+r'\s*(?:\+=\s*1|\+\+)\s*;\s*'
                           r'\*\s*(\w+)\s*=\s*([^;{}]+);\s*\1\s*=\s*&'+re.escape(var)+r'\s*\[\s*'+re.escape(index)+r'\s*\]\s*;\s*}',changed)
            if not fill:
                return None
            ptr=fill[1]
            if re.search(r'\b(?:'+re.escape(index)+'|'+re.escape(ptr)+r')\b',fill[2]):
                return None
            if len(re.findall(r'\b'+re.escape(ptr)+r'\b',_blank(changed)))!=4:
                return None
            changed=changed[:fill.start()]+f'do {{ {var}[{index}] = {fill[2]}; {index}++; }}'+changed[fill.end():]
            changed=re.sub(r'\b(?:u8|s8|char|unsigned\s+char)\s*\*\s*'+re.escape(ptr)+r'\s*;','',changed)
            changed=re.sub(r'\b'+re.escape(ptr)+r'\s*=\s*'+re.escape(var)+r'\s*;','',changed)
        return draft[:opening]+changed+draft[span[1]:]
    return None
