"""One mismatched external callee may be a copied overlay name; judge every hypothesis."""
import re

from .csource import _blank
from .integer_repair_support import IDENT, edit, view

FAMILY = 'target_callee_name'


def variants(source, name, asm, header=''):
    part=view(source,name)
    if not part or re.search(r'\bjalr\b',asm):return []
    targets=re.findall(r'\bjal\s+('+IDENT+r')\s*(?:$|[#/])',asm,re.M)
    calls=re.findall(r'(?<![\w.>])\b('+IDENT+r')\s*\(',part.body)
    calls=[c for c in calls if c not in {'if','for','while','switch','sizeof'}]
    missing=set(targets)-set(calls)-{name}
    extra=set(calls)-set(targets)-{name}
    if len(missing)!=1 or len(extra)!=1:return []
    old=extra.pop();new=missing.pop()
    if not re.fullmatch(r'func_[0-9a-fA-F]+(?:_\w+)?',old):return []
    if calls.count(old)!=targets.count(new):return []
    # A local definition/helper or function-pointer use cannot establish a direct callee.
    prefix=part.clean[:part.opening]
    declaration=re.compile(r'(?m)(?:^|[;}])\s*((?:extern\s+)?(?:'+IDENT+r'\s+)+\**\s*)'+re.escape(old)+r'\s*\(([^;{}]*)\)\s*;')
    matches=list(declaration.finditer(prefix))
    if len(re.findall(r'\b'+re.escape(old)+r'\b',prefix))!=1:return []
    if len(matches)!=1 or re.search(r'\b(?:static|inline|typedef)\b',matches[0][1]):return []
    if re.search(r'\b'+re.escape(old)+r'\s*\([^;{}]*\)\s*{',prefix):return []
    refs=list(re.finditer(r'\b'+re.escape(old)+r'\b',part.body))
    if len(refs)!=calls.count(old):return []
    # Do not rename tokens captured by a macro, or a local binding named like either callee.
    combined=_blank(header)+'\n'+part.clean
    if any(re.search(r'\b(?:'+re.escape(old)+'|'+re.escape(new)+r')\b',line)
           for line in combined.splitlines() if re.match(r'\s*#',line)):return []
    if re.search(r'\\\n',combined):return []
    if re.search(r'\b'+IDENT+r'\s+\**\s*(?:'+re.escape(old)+'|'+re.escape(new)+r')\b',part.body):return []
    # A preexisting target declaration could have another ABI; leave that case for review.
    if re.search(r'\b'+re.escape(new)+r'\b',combined):return []
    return [(FAMILY,edit(source,[(m.start(),m.end(),new) for m in re.finditer(r'\b'+re.escape(old)+r'\b',part.clean)]))]
