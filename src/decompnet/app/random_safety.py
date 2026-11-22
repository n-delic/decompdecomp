"""Keep a pre-random-search checkpoint and reject known untrustworthy mutation shapes.

This is not a C equivalence checker. Near misses remain exploratory and only the normal
exact judge can accept a match. A checkpoint's functionality is explicitly unverified.
"""
import ctypes
import hashlib
import json
import os
import re
import time
from functools import lru_cache

from decomprepair.repair_preflight import invalid_call

from ..compile_sessions import current
from ..forks.csource import _COMMENT_OR_STRING, _blank
from ..instruction_alignment import native_symbol

PREFIX = 'permuter-checkpoint:'


def nested_updates(source):
    # Chaining formerly independent += updates nests side effects and changes the values
    # added to outer destinations. Count these expressions, not ordinary a=b=c=0 copies.
    return sum(len(re.findall(r'(?:<<|>>|[+*/%&|^-])=(?!=)', statement))>1
               for statement in _blank(source).split(';'))


def empty_do_loops(source):
    # A new literal-zero empty do loop only adds source scaffolding. Keep genuine
    # nonempty wrappers and dynamic-condition waits; do not ban their generator.
    pattern = r'\bdo\s*(?:\{\s*(?:;\s*)*\}|;)\s*while\s*\(\s*(?:0+|0[xX]0+)[uUlL]*\s*\)\s*;'
    # Match literals/comments together so raw-string text cannot expose fake C
    # after an embedded quote, nor hide actual code outside the literal.
    literals = re.compile(r'(?:u8|u|U|L)?R"(?P<delimiter>[^\s()\\]{0,16})\(.*?\)(?P=delimiter)"|'
                          + _COMMENT_OR_STRING.pattern, re.S)
    clean = literals.sub(lambda match: re.sub(r'[^\n]', ' ', match[0]), source)
    return len(re.findall(pattern, clean))


class _Counts(ctypes.Structure):
    _fields_ = [('nested', ctypes.c_size_t), ('empty_do', ctypes.c_size_t)]


@lru_cache(maxsize=1)
def _kernel():
    if os.environ.get('DECOMP_NATIVE_CANDIDATE_CHECKS') == '0':
        return None
    try:
        scan = native_symbol('dd_candidate_checks_v1')
    except (OSError, AttributeError):
        return None
    scan.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(_Counts)]
    scan.restype = ctypes.c_uint8
    return scan


def _native_counts(source):
    if len(source) <= 256 * 1024 and source.isascii() and (scan := _kernel()) is not None:
        result = _Counts()
        if scan(source.encode('ascii'), len(source), ctypes.byref(result)):
            return result.nested, result.empty_do
    return None


def _counts(source):
    if (result := _native_counts(source)) is not None:
        return result
    return nested_updates(source), empty_do_loops(source)


def _remember(cache, key, calculate, *args):
    if key not in cache:
        cache[key] = calculate(*args)
        if len(cache) > 32:
            cache.popitem(last=False)
    else:
        cache.move_to_end(key)
    return cache[key]


def _shapes(source, session):
    if session is not None and len(source) <= 32 * 1024:
        return _remember(session.candidate_shapes, source, _counts, source)
    return _counts(source)


def unsafe_update(parent, candidate):
    session = current()
    baseline = _shapes(parent, session)
    changed = _shapes(candidate, session)
    return changed[0] > baseline[0] or changed[1] > baseline[1]


def invalid_candidate_call(source, name, header):
    """Reuse an arity verdict only with the same function spelling, C and header."""
    session = current()
    if session is None or len(source) > 32 * 1024 or len(header) > 1024 * 1024:
        return invalid_call(source, name, header)
    if header != session.candidate_header:
        session.candidate_header = header
        session.candidate_calls.clear()
    return _remember(session.candidate_calls, (source, name), invalid_call, source, name, header)


def checkpoint(net, fn):
    source=fn['best_source']
    if not source:
        return
    key=PREFIX+str(fn['id'])
    row=net.project_store(fn['project_id']).one('SELECT value FROM settings WHERE key=?',key)
    saved=json.loads(row['value']) if row else []
    digest=hashlib.sha256(source.encode()).hexdigest()
    if any(item['source_hash']==digest for item in saved):
        return
    item={'source_hash':digest,'source':source,'score':fn['best_score'],'saved_at':time.time(),
          'quality':'unverified; saved before random rewriting'}
    # Keep the first checkpoint as well as two recent alternatives, even across restarts.
    saved=(saved[:1]+saved[-1:]+[item]) if len(saved)>=3 else saved+[item]
    with net.project_store(fn['project_id']).tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(saved)))


def view(net, fn):
    row=net.project_store(fn['project_id']).one('SELECT value FROM settings WHERE key=?',PREFIX+str(fn['id']))
    if not row:
        return []
    out=['### Before random search (quality unverified)',
         'These checkpoints are retained separately from the lowest-scoring exploratory draft. '
         'A lower score does not prove functional correctness; rejudge any restored source.']
    for item in json.loads(row['value']):
        out += [f"Checkpoint {item['source_hash'][:20]}, historical score {item['score']}:",
                '```c\n'+item['source'][:6000]+'\n```'+('\nPreview truncated; the full checkpoint is retained locally.' if len(item['source'])>6000 else '')]
    return out
