"""Opt-in live responses retain event tails and omit unchanged display sections."""
import hashlib
import json


def compact(out, previous):
    try:
        previous = json.loads(previous or '{}')
    except (ValueError, TypeError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    result, versions = dict(out), {}
    for key in ('overview', 'claims', 'sessions'):
        digest = hashlib.sha256(json.dumps(out[key], separators=(',', ':')).encode()).hexdigest()[:24]
        versions[key] = digest
        if previous.get(key) == digest:
            result.pop(key)
    result['versions'] = versions
    return result
