"""Lossless C tokens for bounded edit experiments; strings and comments remain indivisible."""
import re

TOKEN = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|0[xX][0-9a-fA-F]+[uUlL]*|\d+[uUlL]*|>>=|<<=|->|\+\+|--|&&|\|\||==|!=|<=|>=|<<|>>|\s+|.', re.S)


def tokens(source):
    return TOKEN.findall(source)
