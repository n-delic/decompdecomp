"""Deduplicate per-message usage snapshots and reconcile the final cumulative total."""
import json

from . import number

FIELDS = {'input':'input_tokens','output':'output_tokens','cache_read':'cache_read_input_tokens','cache_write':'cache_creation_input_tokens'}


def update(event, state):
    messages = state.setdefault('usage_messages', {})
    totals = state.setdefault('usage_totals', dict.fromkeys(FIELDS, 0))
    usage = None
    key = None
    final = event.get('type') == 'result'
    if final:
        usage = event.get('usage')
    elif event.get('type') == 'assistant':
        message = event.get('message')
        if not isinstance(message,dict):
            return None
        key, usage = message.get('id'), message.get('usage')
    elif event.get('type') == 'stream_event':
        stream = event.get('event') or {}
        if not isinstance(stream,dict):
            return None
        if stream.get('type') == 'message_start':
            message = stream.get('message') or {}
            if not isinstance(message,dict):
                return None
            key, usage = message.get('id'), message.get('usage')
            state['usage_message_id'] = key
        elif stream.get('type') == 'message_delta':
            key, usage = state.get('usage_message_id'), stream.get('usage')
    if not isinstance(usage, dict) or not usage or (not final and not key):
        return None
    if not final and key not in messages and len(messages)>=2048:
        state['usage_snapshot_limit'] = True
        return None
    delta = {}
    inconsistent = False
    previous = totals if final else messages.setdefault(key, dict.fromkeys(FIELDS, 0))
    for field, wire in FIELDS.items():
        if wire not in usage:
            continue
        raw = usage[wire]
        if type(raw) is not int or raw < 0:
            inconsistent = True
            continue
        value = number(usage,wire)
        if final and value < previous[field]:
            inconsistent = True
        delta[field] = max(0, value-previous[field])
        if final:
            totals[field] += delta[field]
        else:
            previous[field] += delta[field]
            totals[field] += delta[field]
    if not final and not any(delta.values()):
        return None
    return ('usage', json.dumps({**delta, 'coverage':'complete' if final and not inconsistent and not state.get('usage_snapshot_limit') else 'partial'}))
