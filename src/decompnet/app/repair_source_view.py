"""Local views for narrow repairs that can leave unconditional macro definitions opaque."""
import re

from ..forks.csource import _blank, find
from .integer_repair_support import View, view
from .pattern_repairs import close


def read(source,name):
    ordinary=view(source,name)
    if ordinary:return ordinary
    span=find(source,name)
    if not span:return None
    clean=_blank(source)
    definition=re.search(r'\b'+re.escape(name)+r'\s*\([^{};]*\)\s*{',clean[span[0]:span[1]])
    if not definition:return None
    opening=span[0]+definition.end()-1
    # Conditional directives, line controls and C++ remain unsupported. Definitions are
    # opaque: callers must separately reject referenced macros in every edited interval.
    if re.search(r'::|\b(?:class|template|namespace)\b',clean):return None
    directives=list(re.finditer(r'(?m)^[ \t]*#(?:[^\n\\]|\\[^\n]|\\\n)*',clean[opening:span[1]]))
    if not directives or any(not re.match(r'\s*#\s*(?:define|undef)\b',m[0]) for m in directives):return None
    for m in reversed(directives):
        a,b=opening+m.start(),opening+m.end();clean=clean[:a]+re.sub(r'[^\n]',' ',clean[a:b])+clean[b:]
    end=close(clean,opening,'{','}')
    return View(source,clean,opening,end) if end else None
