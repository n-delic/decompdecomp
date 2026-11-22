"""Order a bounded batch of already generated candidates before compiler admission."""
import ctypes
import json
import os
import re
import time
from functools import lru_cache

from decomprepair.repair_tokens import tokens

from .. import arch, compile_timing
from ..instruction_alignment import native_symbol

_LITERAL = re.compile(r'(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)[uUlL]*')


def _python_literals(source):
    values = set()
    for token in tokens(source):
        match = _LITERAL.fullmatch(token)
        if match is None or len(match[1]) > 12:
            continue
        digits = match[1]
        base = 16 if digits.lower().startswith('0x') else 8 if digits.startswith('0') else 10
        value = int(digits, base)
        if 0x10000 <= value <= 0xffffffff:
            values.add(value)
    return values


@lru_cache(maxsize=1)
def _kernel():
    if os.environ.get('DECOMP_NATIVE_RANK_LITERALS') == '0':
        return None
    try:
        function = native_symbol('dd_rank_literals_v1')
    except (AttributeError, OSError):
        return None
    size = ctypes.c_size_t
    function.argtypes = [ctypes.c_char_p, size, ctypes.POINTER(ctypes.c_uint32), size]
    function.restype = size
    return function


def _native_literals(source, kernel):
    raw = source.encode('ascii')
    # The shortest eligible spelling is a five-digit decimal number.
    result = (ctypes.c_uint32 * (len(raw) // 5))()
    count = kernel(raw, len(raw), result, len(result))
    return set(result[:count]) if count <= len(result) else None


def _literals(source):
    if len(source) < 5:
        return set()
    if len(source) <= 256 * 1024 and source.isascii() and (kernel := _kernel()) is not None:
        if (result := _native_literals(source, kernel)) is not None:
            return result
    return _python_literals(source)


def for_function(project, function, log):
    reader = arch.get(project['arch']).split_immediates
    if reader is None or not function['target']:
        return None
    relocated = {reloc[0] & ~3 for reloc in json.loads(function['relocs'] or '[]')}
    target = reader(function['target'], bool(project['big_endian']), relocated)
    if not target:
        return None
    parent_literals = lru_cache(maxsize=1)(_literals)
    reported = False

    def order(parent, candidates):
        nonlocal reported
        if parent is None or len(candidates) < 2:
            return candidates
        started = time.thread_time_ns()
        base = parent_literals(parent)
        literals = [_literals(candidate) for candidate in candidates]
        compile_timing.add('rank_literals_cpu_ns', time.thread_time_ns() - started)
        compile_timing.add('rank_batches', 1)
        compile_timing.add('rank_candidates', len(candidates))

        def distance(values):
            return min(((value ^ expected).bit_count()
                        for value in values - base for expected in target), default=33)

        ranked = [candidate for candidate, _ in sorted(zip(candidates, literals), key=lambda item: distance(item[1]))]
        if not reported and ranked != candidates:
            log('CPU constant ranking reordered a random candidate batch.')
            reported = True
        return ranked

    return order
