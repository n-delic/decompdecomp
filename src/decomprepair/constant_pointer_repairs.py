"""Separate an invariant pointer field argument from the pointer's later lifetime."""
import re

from .csource import _blank, find
from .integer_repair_support import IDENT, edit

FAMILIES = ('constant_pointer_capture_timing',)


def _conditional_ranges(text):
    """Locate conditional directives and their bodies without interpreting build macros."""
    result=[];depth=0;offset=0
    for line in text.splitlines(keepends=True):
        directive=re.match(r'\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b',line)
        if depth or directive:result.append((offset,offset+len(line)))
        if directive:
            if directive[1] in ('if','ifdef','ifndef'):depth+=1
            elif directive[1]=='endif':depth=max(0,depth-1)
        offset+=len(line)
    return result


def _unique_binding(function, name):
    # Count declarations throughout the function, including scalar shadows. Comma
    # declarators are ambiguous here, so conservatively exclude them as well.
    declarations=re.findall(r'\b'+IDENT+r'\s+\**\s*'+re.escape(name)+r'\s*(?=[,;)=\[])',function)
    return len(declarations)==1 and not re.search(r',\s*\**\s*'+re.escape(name)+r'\s*(?=[;=\[])',function)


def variants(source, name, header='', arch=None):
    span=find(source,name)
    if not span:return []
    clean=_blank(source);context=_blank(header+'\n'+source);result=[]
    clean=re.sub(r'^[ \t]*#.*$',lambda m:' '*len(m[0]),clean,flags=re.M)
    function=clean[span[0]:span[1]]
    protected=_conditional_ranges(context)
    source_offset=len(_blank(header+'\n'))
    def guarded(start,end):
        return any(start<b and end>a for a,b in protected)
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b',context,re.M))
    if macros & set(re.findall(IDENT,function)):return []
    volatile_types={m[1] for m in re.finditer(r'\btypedef\s+volatile\b[^{}]*\{[^{}]*\}\s*('+IDENT+r')\s*;',context)}
    aliases=list(re.finditer(r'\btypedef\s+([^;]+?)\b('+IDENT+r')\s*;',context))
    for _ in aliases:
        for alias in aliases:
            if re.search(r'\bvolatile\b',alias[1]) or set(re.findall(IDENT,alias[1])) & volatile_types:
                volatile_types.add(alias[2])
    # A deliberately small proof language: a private helper returns one external array,
    # possibly via the old compiler's conditional local-plus-zero identity form.
    definition=r'\bstatic\s+(?:(?:static|inline|__inline__)\s+)*(?:struct\s+)?('+IDENT+r')\s*\*\s*('+IDENT+r')\s*\(\s*void\s*\*\s*('+IDENT+r')\s*\)\s*\{([^{}]*)\}'
    for helper in re.finditer(definition,clean):
        pointer_type,helper_name,param,body=helper.groups()
        if guarded(source_offset+helper.start(),source_offset+helper.end()):continue
        conditional_text=''.join(context[a:b] for a,b in protected)
        if re.search(r'\btypedef\b[^;{}]*(?:\{[^{}]*\}\s*)?\b'+re.escape(pointer_type)+r'\s*;',conditional_text):continue
        direct=re.fullmatch(r'\s*return\s+('+IDENT+r');\s*',body)
        base=None
        if direct:base=direct[1]
        else:
            identity=re.fullmatch(
                r'\s*(?:struct\s+)?'+re.escape(pointer_type)+r'\s*\*\s*('+IDENT+r');\s*'
                r'int\s+('+IDENT+r')\s*=\s*0;\s*if\s*\(\s*'+re.escape(param)+r'\s*\)\s*'
                r'\1\s*=\s*('+IDENT+r');\s*else\s*\1\s*=\s*\3\s*\+\s*\2;\s*return\s+\1;\s*',body)
            if identity and len(set(identity.group(1,2,3)+(param,)))==4:base=identity[3]
        if not base or base==param:continue
        array=re.search(r'\bextern\s+(?:struct\s+)?('+IDENT+r')\s+'+re.escape(base)+r'\s*\[\s*\]\s*;',context)
        if not array or array[1] in volatile_types or array[1]!=pointer_type or guarded(array.start(),array.end()):continue
        if re.search(r'\b'+IDENT+r'\s+\**\s*'+re.escape(base)+r'\s*(?:[;=,\[])',function):continue
        pattern=r'\(\s*('+IDENT+r')\s*=\s*'+re.escape(helper_name)+r'\s*\(\s*('+IDENT+r')\s*\)\s*\)\s*->\s*('+IDENT+r')'
        for capture in re.finditer(pattern,clean[span[0]:span[1]]):
            start=span[0]+capture.start();end=span[0]+capture.end();var,arg,field=capture.groups()
            if not _unique_binding(function,var) or not _unique_binding(function,arg):continue
            if guarded(source_offset+start,source_offset+end):continue
            local=re.search(r'\b(?:struct\s+)?('+IDENT+r')\s*\*\s*'+re.escape(var)+r'\s*;',function[:capture.start()])
            if not local or local[1] in volatile_types or local[1]!=pointer_type:continue
            if guarded(source_offset+span[0]+local.start(),source_offset+span[0]+local.end()):continue
            argument=re.search(r'\b(?:struct\s+)?('+IDENT+r')\s*\*\s*'+re.escape(arg)+r'\s*(?=[,);=])',function[:capture.start()])
            if not argument or argument[1] in volatile_types:continue
            if guarded(source_offset+span[0]+argument.start(),source_offset+span[0]+argument.end()):continue
            if re.search(r'&\s*\(*\s*'+re.escape(arg)+r'\b(?!\s*(?:->|\[))',function):continue
            if re.search(r'&\s*\(*\s*'+re.escape(var)+r'\b',function):continue
            if any(re.search(r'^\s*#\s*define\s+'+re.escape(token)+r'\b',context,re.M) for token in set(re.findall(IDENT,helper[0])) | {base,var,arg,field}):continue
            # Only a whole call statement in an existing braced block. Nested calls,
            # conditions and assignment expressions elsewhere need a fuller flow proof.
            before=clean.rfind(';',span[0],start)+1
            before=max(before,clean.rfind('{',span[0],start)+1,clean.rfind('}',span[0],start)+1)
            after=clean.find(';',end,span[1])
            if after<0:continue
            statement=clean[before:after]
            replaced=statement[:start-before]+base+'[0].'+field+statement[end-before:]
            call=re.fullmatch(r'\s*('+IDENT+r')\s*\((.*)\)\s*',replaced,re.S)
            if not call or re.search(r'\b'+IDENT+r'\s*\(',call[2]) or re.search(r'\+\+|--|(?<![=!<>])=(?!=)',call[2]):continue
            if any(re.search(r'^\s*#\s*define\s+'+re.escape(token)+r'\b',context,re.M) for token in re.findall(IDENT,replaced)):continue
            # The captured local cannot be used by another argument, passed by address,
            # or changed through a volatile-qualified declaration.
            if re.search(r'\b'+re.escape(var)+r'\b',replaced) or re.search(r'\bvolatile\b[^;{}]*\b(?:'+re.escape(var)+'|'+re.escape(arg)+r')\b',context):continue
            if not re.search(r'\b'+re.escape(var)+r'\s*=\s*'+re.escape(helper_name)+r'\s*\(\s*'+re.escape(arg)+r'\s*\)\s*;',clean[span[0]:before]):continue
            indent=re.search(r'\n([ \t]*)[^\n]*$',source[:before+len(statement)-len(statement.lstrip())])
            spacing=indent[1] if indent else '    '
            updated=edit(source,[(start,end,base+'[0].'+field),(after+1,after+1,'\n'+spacing+var+' = '+helper_name+'('+arg+');')])
            result.append((FAMILIES[0],updated))
            if len(result)>=2:return result
    return result
