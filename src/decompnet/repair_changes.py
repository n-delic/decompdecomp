"""Describe source differences without mistaking changed comments or layout for a learned C repair."""
import re

from .forks.csource import _COMMENT_OR_STRING

TOKEN=re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|\d+(?:\.\d+)?|>>=|<<=|\.\.\.|\+\+|--|->|&&|\|\||==|!=|<=|>=|<<|>>|[+*/%&|^!-]=|\S')
LABELS={'missing_before':'No saved failed source', 'unchanged':'Source unchanged',
        'comments_or_spacing':'Comments/spacing changed; source line boundaries preserved',
        'layout_only':'Source layout changed; C tokens unchanged (may affect debug scheduling)',
        'source_change':'C tokens changed'}


def without_comments(source):
    return _COMMENT_OR_STRING.sub(lambda m: re.sub(r'[^\n]',' ',m[0]) if m[0].startswith(('/*','//')) else m[0],source)


def kind(before,after):
    if before is None:
        return 'missing_before'
    if before==after:
        return 'unchanged'
    a,b=without_comments(before),without_comments(after)
    lines=lambda s:[TOKEN.findall(line) for line in s.splitlines()]
    if lines(a)==lines(b):
        return 'comments_or_spacing'
    if TOKEN.findall(a)==TOKEN.findall(b):
        return 'layout_only'
    return 'source_change'
