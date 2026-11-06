"""Bounded private signed/unsigned mask declarations, justified as hypotheses by bit operations."""
import re

from ..forks.csource import _blank


def variants(source, header):
    clean=_blank(source)
    if not re.search(r'(?<![&])&(?![&])|\^|<<|>>',clean):return []
    result=[]
    for text,is_header in ((header,True),(source,False)):
        plain=_blank(text)
        # Keep width fixed. Do not alter pointers, volatile declarations, arrays or typedefs.
        for match in re.finditer(r'(?<!\w)(unsigned\s+(?:int|long)|signed\s+(?:int|long)|int|long)\s+([A-Za-z_]\w*)\s*(?=[;=])',plain):
            if not re.search(r'flags|mask',match[2],re.I):continue
            start=plain[max(plain.rfind(';',0,match.start()),plain.rfind('{',0,match.start()),plain.rfind('}',0,match.start()))+1:match.start()]
            if re.search(r'\b(?:volatile|typedef|const)\b',start) or not re.search(r'\b'+re.escape(match[2])+r'\b',clean):continue
            typ=match[1];new=typ.removeprefix('unsigned ') if typ.startswith('unsigned ') else 'unsigned '+typ.removeprefix('signed ')
            changed=text[:match.start(1)]+new+text[match.end(1):]
            result.append((source,changed) if is_header else (changed,header))
            if len(result)>=2:return result
    return result
