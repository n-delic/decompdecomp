"""Bounded scratch storage for the hypotheses produced by a full ROM audit."""
import json
import sqlite3
import tempfile
from collections import OrderedDict
from pathlib import Path


def editable(rows):
    """Iterate mutable rows for either an ordinary list or a disk-backed audit."""
    return rows.edit() if isinstance(rows,CandidateSpool) else rows


class CandidateSpool:
    """Keep candidate identities and evidence on disk, with a bounded write-back cache."""
    def __init__(self,cache_size=4096):
        if cache_size<1:raise ValueError('The audit cache needs at least one row.')
        self.temp=tempfile.TemporaryDirectory(prefix='decomp-code-audit-')
        self.db=sqlite3.connect(Path(self.temp.name)/'candidates.sqlite3')
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.execute('CREATE TABLE candidates (ordinal INTEGER PRIMARY KEY, offset INTEGER NOT NULL, vram INTEGER NOT NULL, payload TEXT NOT NULL, sort_key BLOB, UNIQUE(offset,vram))')
        self.db.execute('CREATE INDEX candidate_order ON candidates(sort_key,ordinal)')
        self.cache=OrderedDict()
        self.cache_size=cache_size
        self.count=0

    def __enter__(self):return self

    def __exit__(self,*_):
        self.db.close()
        self.temp.cleanup()

    def __len__(self):return self.count

    def __contains__(self,key):
        return key in self.cache or self.db.execute('SELECT 1 FROM candidates WHERE offset=? AND vram=?',key).fetchone() is not None

    def _write(self,key,row):
        self.db.execute('INSERT INTO candidates(offset,vram,payload) VALUES(?,?,?) ON CONFLICT(offset,vram) DO UPDATE SET payload=excluded.payload',(*key,json.dumps(row)))

    def _cache(self,key,row):
        self.cache[key]=row
        self.cache.move_to_end(key)
        if len(self.cache)>self.cache_size:
            old,value=self.cache.popitem(last=False)
            self._write(old,value)
        return row

    def __setitem__(self,key,row):
        if key not in self:self.count+=1
        self._cache(key,row)

    def setdefault(self,key,default):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        row=self.db.execute('SELECT payload FROM candidates WHERE offset=? AND vram=?',key).fetchone()
        if row:return self._cache(key,json.loads(row[0]))
        self.count+=1
        return self._cache(key,default)

    def flush(self):
        while self.cache:
            key,row=self.cache.popitem(last=False)
            self._write(key,row)

    def _rows(self):
        self.flush()
        cursor=self.db.execute('SELECT ordinal,payload FROM candidates ORDER BY sort_key,ordinal')
        try:
            for ordinal,payload in cursor:
                yield ordinal,json.loads(payload)
        finally:
            cursor.close()

    def __iter__(self):
        for _,row in self._rows():yield row

    def edit(self):
        for ordinal,row in self._rows():
            try:
                yield row
            finally:
                self.db.execute('UPDATE candidates SET payload=? WHERE ordinal=?',(json.dumps(row),ordinal))

    def values(self):return iter(self)

    def sort(self,*,key):
        self.flush()
        # Both audit sort keys end in the unique (offset, vram) identity, so
        # their order is total and does not depend on an earlier sort's ties.
        cursor=self.db.execute('SELECT ordinal,payload FROM candidates ORDER BY ordinal')
        try:
            for ordinal,payload in cursor:
                parts=key(json.loads(payload))
                encoded=b''.join((int(part)+(1<<63)).to_bytes(8,'big') for part in parts)
                self.db.execute('UPDATE candidates SET sort_key=? WHERE ordinal=?',(encoded,ordinal))
        finally:
            cursor.close()
