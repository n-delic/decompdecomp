"""Batch metadata across SQLite's Python boundary under background CPU contention."""
import json
import re


def rows(store, columns, query, *args):
    """Trusted metadata queries only: column values must be JSON scalars, not blobs.

    The subquery retains its own ordering/limits. Returning one JSON value avoids
    a Python GIL handoff for every stepped row; nullable text stays nullable text.
    """
    if not columns or any(not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name) for name in columns):
        raise ValueError('Choose simple metadata column names.')
    packed=store.one('SELECT json_group_array(json_array('+','.join(columns)+
                     ')) AS metadata FROM ('+query+')',*args)
    return [dict(zip(columns,row)) for row in json.loads(packed['metadata'])]
