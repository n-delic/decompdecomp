"""Reject generated aggregate dispatch helpers unless the compiler fully removes them.

This is an additional candidate check, never a match verdict. Ordinary linking/data checks
still apply. Unknown object formats fail closed only for this opt-in generated helper family.
"""
PREFIX = '__repair_aggregate_call_'


def check(source, obj):
    if PREFIX not in source:
        return ''
    from .object_emission import inspect, text
    inventory = inspect(obj)
    if not inventory.get('available'):
        return inventory['reason']
    if inventory['remaining_generated_helpers']:
        names = ', '.join(inventory['remaining_generated_helpers'][:3])
        return 'Generated aggregate helper did not fully inline ('+names+'); reject this source shape. '+text(inventory)
    return ''
