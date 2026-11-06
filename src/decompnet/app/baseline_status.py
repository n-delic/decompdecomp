"""Explain when a selected draft's historical score no longer describes today's inputs."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from .. import scoring
from ..project import compiler_for


@lru_cache(maxsize=1)
def revision():
    # Fingerprint the implementation so a code update cannot silently reuse an old score.
    try:
        return hashlib.sha256(Path(scoring.__file__).read_bytes()).hexdigest()
    except OSError:
        return 'unavailable'


def status(net, proj, fn):
    if fn['status']=='matched' or not fn['best_source']:
        return {'state':'not needed','reasons':[]}
    attempt=net.store.one('SELECT s.settings FROM attempts a LEFT JOIN repair_attempt_settings s '
                          'ON s.attempt_id=a.id WHERE a.function_id=? AND a.source=? AND a.score=? '
                          'ORDER BY a.id DESC LIMIT 1',fn['id'],fn['best_source'],fn['best_score'])
    reasons=[]
    settings=json.loads(attempt['settings']) if attempt and attempt['settings'] else {}
    if not settings:
        reasons.append('The original build settings were not recorded.')
    elif net.is_rom(proj):
        current=compiler_for(net.cfg(proj),fn['vram'], fn)
        old=settings.get('compiler') or {}
        if any(old.get(k)!=current.get(k) for k in ('toolchain','flags')):
            reasons.append('Compiler settings changed.')
        ctx=net._context(proj['id'],fn['unit'])
        if settings.get('context_sha256')!=hashlib.sha256(ctx['header'].encode()).hexdigest():
            reasons.append('The shared header changed.')
    if settings.get('scoring_revision')!=revision():
        reasons.append('The scoring version has not been rechecked.')
    return {'state':'historical' if reasons else 'current','reasons':reasons,
            'message':'Historical score. Open Diff to recompile the saved C with the current settings.' if reasons else ''}
