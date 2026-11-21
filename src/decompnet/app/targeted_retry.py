"""Requeue completed deep passes only when new focused rules fit their saved evidence."""
import hashlib
import json

from decomprepair import last_byte_mask_repairs

from .. import claim_stalls
from . import region_repairs

RULES = {'last_byte_mask_repairs.py': last_byte_mask_repairs}


def general_rules(rules=None):
    rules = claim_stalls.repair_rules() if rules is None else rules
    return [(name, digest) for name, digest in rules if name not in RULES]


def source_key(source):
    return hashlib.sha256(source.encode()).hexdigest()


def snapshot(source, rows):
    rows = region_repairs.clean(rows)
    changed = [i for i, (mark, left, right) in enumerate(rows) if mark != ' ' or left != right]
    window = []
    if len(changed) == 1:
        i = changed[0]
        window = rows[max(0, i-3):i+4]
    return {'source': source_key(source), 'rows': window}


def stamp(proj, fn, saved, rules):
    if proj['arch'] != 'mips' or fn['status'] != 'open' or not fn['best_source']:
        return ()
    if isinstance(saved, str):
        saved = json.loads(saved)
    source = fn['best_source']
    measured = saved is not None and saved.get('source') == source_key(source)
    # A legacy pass lacks measured rows. Require the score of a lone operand
    # difference before checking the saved C guard against the target window.
    if not measured and fn['best_score'] != 10:
        return ()
    return tuple((name, digest) for name, digest in rules if name in RULES and (
        RULES[name].variants(source, fn['name'], saved['rows']) if measured else
        RULES[name].legacy_candidate(source, fn['name'], fn['asm'] or '')))
