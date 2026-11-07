"""Attach shared identity labels to bounded project-owned result sets."""
import json


def names(net, ids):
    ids = sorted({aid for aid in ids if aid is not None})
    if not ids:return {}
    return {row['id']:row['name'] for row in net.store.all(
        'SELECT id,name FROM agents WHERE id IN (SELECT value FROM json_each(?))', json.dumps(ids))}


def label(net, rows, columns, *, require=()):
    """columns maps output label names to identity-ID fields in project rows."""
    rows = [dict(row) for row in rows]
    labels = names(net, (row[field] for row in rows for field in columns.values()))
    result = []
    for row in rows:
        if any(row[columns[key]] not in labels for key in require):continue
        for key, field in columns.items():row[key] = labels.get(row[field])
        for field in set(columns.values()) - columns.keys():row.pop(field)
        result.append(row)
    return result
