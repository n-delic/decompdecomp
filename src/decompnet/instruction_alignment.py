"""Mnemonic-only alignment shared by score and diff, with a Python fallback."""

from __future__ import annotations

import ctypes
import os
import sys
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from . import compile_sessions

_MAX_NATIVE_LENGTH = 8192
_MAX_CACHED_LENGTH = 2048  # total instructions across both sequences
_CACHE_ENTRIES = 128


def library_name() -> str:
    suffix = {'darwin': 'dylib', 'win32': 'dll'}.get(sys.platform, 'so')
    return f'decomp-alignment.{suffix}'


def native_symbol(name):
    path = Path(__file__).resolve().with_name('bin') / library_name()
    return getattr(ctypes.CDLL(str(path)), name)


class _Block(ctypes.Structure):
    _fields_ = [('a', ctypes.c_size_t), ('b', ctypes.c_size_t), ('size', ctypes.c_size_t)]


@lru_cache(maxsize=1)
def _kernel():
    if os.environ.get('DECOMP_NATIVE_ALIGNMENT') == '0':
        return None
    try:
        function = native_symbol('dd_matching_blocks_v1')
    except (OSError, AttributeError):
        return None
    tokens = ctypes.POINTER(ctypes.c_uint32)
    length = ctypes.c_size_t
    function.argtypes = [tokens, length, tokens, length, length, ctypes.POINTER(_Block), length]
    function.restype = length
    return function


def _native_blocks(left, right, kernel):
    ids = {}

    def encode(sequence):
        return (ctypes.c_uint32 * len(sequence))(*(ids.setdefault(token, len(ids)) for token in sequence))

    a, b = encode(left), encode(right)
    out = (_Block * (min(len(a), len(b)) + 1))()
    count = kernel(a, len(a), b, len(b), len(ids), out, len(out))
    if 0 < count <= len(out):
        return [(block.a, block.b, block.size) for block in out[:count]]
    return None


def _from_blocks(blocks):
    # The terminal empty block accounts for any trailing insert or delete.
    i = j = 0
    for a, b, size in blocks:
        if i < a or j < b:
            tag = 'insert' if i == a else 'delete' if j == b else 'replace'
            yield tag, i, a, j, b
        i, j = a + size, b + size
        if size:
            yield 'equal', a, i, b, j


def _calculate(left, right):
    if max(len(left), len(right)) <= _MAX_NATIVE_LENGTH and (kernel := _kernel()) is not None:
        blocks = _native_blocks(left, right, kernel)
        if blocks is not None:
            return tuple(_from_blocks(blocks))
    return tuple(SequenceMatcher(a=left, b=right, autojunk=False).get_opcodes())


def opcodes(left, right):
    """Cache alignment shape only. Operands, score and the byte verdict are per candidate."""
    session = compile_sessions.current()
    if session is None or len(left) + len(right) > _MAX_CACHED_LENGTH:
        return _calculate(left, right)
    cache = session.alignments
    key = tuple(left), tuple(right)
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    result = cache[key] = _calculate(*key)
    if len(cache) > _CACHE_ENTRIES:
        cache.popitem(last=False)
    return result
