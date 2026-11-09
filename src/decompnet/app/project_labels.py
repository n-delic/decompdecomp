"""Attach shared identity labels to bounded project-owned result sets."""
import json

from .. import consoles


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


def projects(net):
    """Catalog discovery with canonical project titles; legacy needs only one read."""
    rows = net.store.all('SELECT id,slug,name,config FROM projects ORDER BY id DESC')
    result = []
    for row in rows:
        store = net.project_store(row['id'])
        selected = row if store is net.store else store.one('SELECT slug,name,config FROM projects WHERE id=?',row['id'])
        if selected is None:
            from ..network import NetworkError
            raise NetworkError('This project is missing from its database. Restore it from backup.')
        cfg = json.loads(selected['config'])
        name = consoles.of(cfg).display_title(selected['name'], cfg.get('rom', {}))
        result.append({'slug':selected['slug'],'name':name})
    return result
