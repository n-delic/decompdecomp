"""Complete selected function snapshots without per-row payload UDF handoffs."""
import json
import re
import sqlite3
import zlib

from . import payload_storage
from .large_text_storage import FIELDS


def selected(store, project_id, ids, *, columns=None, matched_only=False):
    """Keep every compiler/judge field, including exact scalar types and blobs.

    A single statement captures metadata and distinct compressed bodies. By
    default every compiler/judge field is kept. Archiving can explicitly request its
    existing narrow receipt fields without inflating unrelated source or targets.
    """
    if not ids:
        return []
    available = json.loads(store.one(
        "SELECT json_group_array(name) AS names FROM pragma_table_info('functions','main')")['names'])
    columns = available if columns is None else columns
    if (not columns or 'id' not in columns or any(not isinstance(c,str)
            or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', c) or c not in available for c in columns)):
        raise ValueError('Choose function storage columns including id.')
    packed = set(FIELDS['functions']) & set(columns)
    # SQLite JSON rounds REAL values by default. Text with 17 significant digits
    # round-trips the native double; integers remain exact JSON integers.
    values = ','.join(f"json_array(typeof({c}),CASE typeof({c}) WHEN 'blob' THEN hex({c}) "
                      f"WHEN 'real' THEN printf('%!.17g',{c}) ELSE {c} END)" for c in columns)
    # UNION deduplicates across fields; one selected body needs its own DISTINCT.
    distinct = 'DISTINCT ' if len(packed)==1 else ''
    refs = ' UNION '.join(f"SELECT {distinct}lower(hex({c})) AS hash FROM chosen WHERE typeof({c})='blob'"
                          for c in sorted(packed)) or 'SELECT NULL AS hash WHERE 0'
    batches = store.all(f"""WITH chosen AS MATERIALIZED (
        SELECT {','.join(columns)} FROM main.functions WHERE project_id=?
        {"AND status='matched'" if matched_only else ''} AND id IN (SELECT value FROM json_each(?))),
        refs AS ({refs}),
        packets AS (
        SELECT id AS position,'' AS hash,json_object('kind','function','values',json_array({values})) AS record FROM chosen
        UNION ALL SELECT NULL,r.hash,json_object('kind','payload','hash',r.hash,
            'payload',CASE WHEN p.hash IS NOT NULL THEN hex(p.payload) END)
        FROM refs r LEFT JOIN main.text_payloads p ON p.hash=r.hash),
        numbered AS (SELECT record,row_number() OVER (ORDER BY position,hash) AS ordinal FROM packets)
        SELECT json_group_array(json(record)) AS batch FROM numbered
        GROUP BY (ordinal-1)/128 ORDER BY min(ordinal)""", project_id, json.dumps(ids))
    decoded, records = {}, []
    while batches:
        for row in json.loads(batches.pop()['batch']):
            if row['kind'] == 'payload':
                blob = bytes.fromhex(row['payload']) if row['payload'] is not None else None
                try:
                    decoded[row['hash']] = payload_storage.unpack(row['hash'], blob)
                except (ValueError, UnicodeError, zlib.error) as error:
                    raise sqlite3.OperationalError('user-defined function raised exception') from error
            else:
                records.append(row['values'])
    result = []
    while records:
        record = records.pop()  # Release encoded targets as their native bytes are restored.
        row = {}
        for column, (kind, value) in zip(columns, record):
            if kind == 'blob':
                value = decoded[value.lower()] if column in packed else bytes.fromhex(value)
            elif kind == 'real':
                value = float(value)
            row[column] = value
        result.append(row)
    result.sort(key=lambda row: row['id'])
    return result
