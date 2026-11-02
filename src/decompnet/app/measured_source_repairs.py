"""Small source/header hypotheses gated by compiler differences, never by score alone."""
import re
from collections import Counter

from ..forks.csource import _blank, find

ID=r'[A-Za-z_]\w*'


def scheduling(rows):
    """A few instruction positions moved, with the same actual instruction multiset."""
    different=[(a,b) for mark,a,b in rows if mark!=' ']
    normalize=lambda s:re.sub(r'\s+','',s)
    return (0<len(different)<=8 and Counter(normalize(a) for a,_ in different if a)
            ==Counter(normalize(b) for _,b in different if b))


def statement_lines(source, name, header, rows):
    """Join/split adjacent simple assignments, preserving tokens, comments and execution order."""
    if not scheduling(rows):return []
    result=[];splits=[]
    for text,is_header in ((source,False),(header,True)):
        clean=_blank(text)
        spans=[]
        if not is_header:
            span=find(text,name)
            if span:spans.append(span)
        from .source_match_context import dependencies
        used=dependencies(source,header)
        for match in re.finditer(r'\bstatic\s+(?:(?:__inline(?:__)?|inline)\s+)?(?:'+ID+r'[\s*]+)+('+ID+r')\s*\(',clean):
            if match[1] not in used:continue
            span=find(text,match[1])
            if span and span not in spans and span[1]-span[0]<=16000:spans.append(span)
            if len(spans)>=5:break
        for a,b in spans:
            # No preprocessor directives/line macros are crossed or reflowed.
            segment=clean[a:b]
            if '__LINE__' in segment:continue
            assignment=r'[^;{}\n#]+=[^;{}\n#]+;'
            pattern=r'('+assignment+r')([ \t]*\n[ \t]*)(?=[^;{}\n#]+=[^;{}\n#]+;)'
            for match in list(re.finditer(pattern,segment))[:4]:
                start,end=a+match.start(2),a+match.end(2)
                if text[start:end].strip():continue  # Never erase a comment hidden by _blank.
                previous=segment[segment.rfind('\n',0,match.start())+1:match.start()]+match[1]
                following=segment[match.end():segment.find(';',match.end())+1]
                if re.search(r'\b(?:if|for|while|return|case|goto|else)\b|:',previous+following):continue
                changed=text[:start]+' '+text[end:]
                result.append(('cpu-statement-lines',source,changed) if is_header else ('cpu-statement-lines',changed,header))
                if len(result)>=4:return result
            for match in list(re.finditer(r'('+assignment+r')([ \t]+)(?=[^;{}\n#]+=[^;{}\n#]+;)',segment))[:2]:
                start,end=a+match.start(2),a+match.end(2)
                if text[start:end].strip():continue
                previous=segment[segment.rfind('\n',0,match.start())+1:match.start()]+match[1]
                following=segment[match.end():segment.find(';',match.end())+1]
                if re.search(r'\b(?:if|for|while|return|case|goto|else)\b|:',previous+following):continue
                changed=text[:start]+'\n'+text[end:]
                splits.append(('cpu-statement-lines',source,changed) if is_header else ('cpu-statement-lines',changed,header))
    return list(dict.fromkeys(result+splits))[:4]


def narrowing_casts(source, header, evidence):
    """Try dropping actual narrow casts only when the candidate emitted extra normalization."""
    unsigned={e['width'] for e in evidence if e.get('unsigned')}
    signed={e['width'] for e in evidence if not e.get('unsigned')}
    types={'u8':(8,True),'unsigned char':(8,True),'u16':(16,True),'unsigned short':(16,True),
           's8':(8,False),'signed char':(8,False),'s16':(16,False),'short':(16,False),'signed short':(16,False)}
    result=[];edits=[];clean=_blank(source)
    for match in re.finditer(r'\(\s*('+'|'.join(types)+r')\s*\)\s*(?='+ID+r'\b)',clean):
        width,is_unsigned=types[match[1]]
        if width not in (unsigned if is_unsigned else signed):continue
        # Only a plain identifier operand; no call, pointer load or compound expression.
        operand=re.match(ID,clean[match.end():])
        if not operand or re.match(r'\s*(?:\(|\[|->|\.)',clean[match.end()+operand.end():]):continue
        if '/*' in source[match.start():match.end()] or '//' in source[match.start():match.end()]:continue
        replacement=re.sub(r'[^\n]',' ',source[match.start():match.end()])
        edits.append((match.start(),match.end(),replacement))
        if len(edits)>=2:break
    for a,b,replacement in edits:
        result.append(('cpu-remove-extra-narrowing',source[:a]+replacement+source[b:],header))
    if len(edits)>1:
        combined=source
        for a,b,replacement in reversed(edits):combined=combined[:a]+replacement+combined[b:]
        result.insert(0,('cpu-remove-extra-narrowing-bundle',combined,header))
    return result


def externs(source, header, reference):
    """Replace tentative private-header objects only with actual compatible extern declarations."""
    from .source_context import typedefs
    aliases={**typedefs(header),**typedefs(reference)}
    def canonical(typ):
        for _ in range(8):
            replaced=False
            for name,decl in aliases.items():
                simple=re.fullmatch(r'typedef\s+([^;{}()]+)\s+'+re.escape(name)+r'\s*;',decl)
                if simple and re.search(r'\b'+re.escape(name)+r'\b',typ):
                    typ=re.sub(r'\b'+re.escape(name)+r'\b',simple[1],typ);replaced=True
            if not replaced:break
        return re.sub(r'\bsigned\s+(?=int\b)','', ' '.join(typ.split()))
    pattern=r'(?m)^[ \t]*extern\s+((?:'+ID+r'[ \t*]+)+)('+ID+r')\s*;'
    actual={}
    for m in re.finditer(pattern,_blank(reference)):
        actual.setdefault(m[2],set()).add(m[1].strip())
    clean=_blank(header);edits=[]
    tentative=r'(?m)^[ \t]*((?:'+ID+r'[ \t*]+)+)('+ID+r')[ \t]*;'
    for match in re.finditer(tentative,clean):
        typ,name=match[1].strip(),match[2]
        if re.search(r'\b(?:extern|static|typedef|const|volatile)\b',typ) or len(actual.get(name,()))!=1:continue
        # Only file-scope simple objects; no local declarations, fields, arrays or initializers.
        prefix=clean[:match.start()]
        if prefix.count('{')!=prefix.count('}') or prefix.count('(')!=prefix.count(')'):continue
        original=next(iter(actual[name]))
        if canonical(typ)!=canonical(original):continue
        declaration='extern '+original+' '+name+';'
        edits.append((match.start(),match.end(),declaration+'\n'*header[match.start():match.end()].count('\n')))
        if len(edits)>=8:break
    if not edits:return []
    private=header
    for a,b,text in reversed(edits):private=private[:a]+text+private[b:]
    return [('cpu-reference-externs',source,private)]
