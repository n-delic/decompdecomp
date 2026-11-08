"""Translate supplied raw field macros only through an unambiguous existing record layout."""
import re

from ..forks.csource import _blank
from .compiler_shape_repairs import SCALARS, fields

ID=r'[A-Za-z_]\w*'


def variants(source, name, header):
    """No guessed fields: simple scalar records, typed parameters, exact raw macro definitions."""
    clean=_blank(source);signature=re.search(r'\b'+re.escape(name)+r'\s*\(([^()]*)\)\s*{',clean)
    if not signature or re.search(r'\bvolatile\b',_blank(header+source)):return []
    params=dict((m[2],m[1]) for m in re.finditer(r'\b('+ID+r')\s*\*\s*('+ID+r')\b',signature[1]))
    # A redeclaration inside the body invalidates the parameter-to-record association.
    body=clean[signature.end():]
    for param in list(params):
        if re.search(r'\b'+ID+r'\s+\**\s*'+re.escape(param)+r'\s*[;=,)]',body):params.pop(param)
    layout={}
    for record,typ,member,offset in fields(header):layout.setdefault((record,offset),[]).append((typ,member))
    macros={}
    # Check actual macro bodies, not familiar-looking names such as W/H/F.
    pattern=r'(?m)^\s*#define\s+('+ID+r')\(\s*('+ID+r')\s*,\s*('+ID+r')\s*\)\s+\(\*\(('+ID+r')\s*\*\)\s*\(\(u8\s*\*\)\s*\2\s*\+\s*\3\)\)\s*$'
    for m in re.finditer(pattern,header):
        if m[4] in SCALARS:macros[m[1]]=m[4]
    edits=[]
    for m in re.finditer(r'\b('+ID+r')\(\s*('+ID+r')\s*,\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*\)',clean):
        if m[1] not in macros or m[2] not in params:continue
        choices=layout.get((params[m[2]],int(m[3],0)),[])
        if len(choices)!=1 or choices[0][0]!=macros[m[1]]:continue
        edits.append((m.start(),m.end(),m[2]+'->'+choices[0][1]))
    if not edits:return []
    for a,b,text in reversed(edits):source=source[:a]+text+source[b:]
    return [('cpu-retail-typed-fields',source,header)]
