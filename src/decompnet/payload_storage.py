"""Compressed immutable text payloads with stable attempt IDs and lazy SQL detail views.

Physical tables retain metadata, foreign keys and INSERT/lastrowid semantics. Store connections
route reads through detail views; maintenance uses explicit main.table names to inspect raw storage.
Old rows remain readable until an explicit, bounded backfill packs them.
"""
import functools
import hashlib
import json
import re
import sqlite3
import threading
import zlib
from collections import OrderedDict

_cache=OrderedDict()
_cache_bytes=0
_verified=OrderedDict()
_cache_lock=threading.Lock()
# Decompressed text kept for repeat reads, bounded by its size: a few 80-MiB progress manifests must
# not turn the cache into GiBs, and the old 32-text cache missed on nearly every read of a busy game.
CACHE_BYTES=64*1024*1024
CACHED_TEXT_BYTES=4*1024*1024
# Payloads whose SHA-256 this process has already checked, by (hash, compressed length, CRC-32). Hashing
# the whole text on every read held the GIL inside SQLite's steps; zlib's own Adler-32 still catches a
# damaged stream on each read, and a different (even valid) payload under a hash has another CRC-32, so
# it is checked again and still fails closed.
VERIFIED_PAYLOADS=16384


def unpack(digest, payload):
    global _cache_bytes
    if payload is None:raise ValueError('Missing database payload; restore a database backup.')
    key=(digest,len(payload),zlib.crc32(payload))
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key][0]
        verified=key in _verified
    raw=zlib.decompress(payload)
    if not verified and hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Database payload checksum failed.')
    text=raw.decode('utf-8')
    with _cache_lock:
        _verified[key]=None
        _verified.move_to_end(key)
        while len(_verified)>VERIFIED_PAYLOADS:_verified.popitem(last=False)
        if len(raw)<=CACHED_TEXT_BYTES and key not in _cache:
            _cache[key]=(text,len(raw))
            _cache_bytes+=len(raw)
            while _cache_bytes>CACHE_BYTES:_cache_bytes-=_cache.popitem(last=False)[1][1]
    return text


_recent=OrderedDict()
_recent_lock=threading.Lock()
# A packing trigger asks for the same text's hash up to three times (the duplicate check, the insert and
# the reference), each time with a new Python copy of the value: remember the last few by value.
RECENT_DIGESTS=8
RECENT_DIGEST_BYTES=4*1024*1024


def digest(text):
    if text is None:return None
    with _recent_lock:
        known=_recent.get(text)
    if known is not None:return known
    value=hashlib.sha256(text.encode('utf-8')).hexdigest()
    if len(text)<=RECENT_DIGEST_BYTES:
        with _recent_lock:
            _recent[text]=value
            while len(_recent)>RECENT_DIGESTS:_recent.popitem(last=False)
    return value


# Fast compression for very large text (whole progress manifests): level 1 is several times faster
# than the default and still small; zlib.decompress reads every level.
FAST_PACK_BYTES=1024*1024


def pack(text):
    raw=text.encode('utf-8')
    return zlib.compress(raw,1 if len(raw)>FAST_PACK_BYTES else -1)


def metadata(text):
    # Listing/grouping fields remain queryable; exact complete evidence stays in the immutable blob.
    try:value=json.loads(text)
    except (ValueError,TypeError):return text
    def small(item):
        if isinstance(item,dict):
            return {k:small(v) for k,v in item.items() if k not in
                    {'source','header','asm','assembly','target_asm','candidate_asm','diff','rows','log','diagnostics','target_hex','target_relocs'}}
        if isinstance(item,list):return [small(v) for v in item]
        return item
    return json.dumps(small(value),separators=(',',':'))


def register(conn):
    from .repair_archive import register as register_archive
    register_archive(conn)
    conn.create_function('unhex',1,lambda value:bytes.fromhex(value) if value is not None else None,deterministic=True)
    conn.create_function('payload_digest',1,digest,deterministic=True)
    conn.create_function('payload_pack',1,pack,deterministic=True)
    conn.create_function('payload_read',2,unpack,deterministic=True)
    conn.create_function('repair_metadata',1,metadata,deterministic=True)
    if isinstance(conn,Connection):
        from .large_text_storage import FIELDS
        installed={r[0] for r in sqlite3.Connection.execute(conn,
            "SELECT name FROM sqlite_master WHERE type='view' AND (name IN ('attempt_details','repair_details') OR name LIKE 'packed_%')")}
        views={'attempts':'attempt_details','repair_examples':'repair_details',**{t:'packed_'+t for t in FIELDS}}
        conn.detail_tables=frozenset(t for t,view in views.items() if view in installed)


# Quoted values/comments must never be rewritten (including JSON paths and recorded SQL text).
QUOTED=re.compile(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/)")
from .large_text_storage import FIELDS

TABLE=re.compile(r'\b(FROM|JOIN)\s+('+ '|'.join(('attempts','repair_examples',*FIELDS))+r')\b',re.I)
ALIAS=re.compile(r'\s+(?:AS\s+)?([a-zA-Z_]\w*)',re.I)
DDL=re.compile(r'\s*(?:(?:--[^\n]*(?:\n|$)|/\*[\s\S]*?\*/)\s*)*(CREATE|DROP|ALTER|PRAGMA)\b',re.I)
ROUTES=4096
_routes=OrderedDict()
_routes_lock=threading.Lock()
KEYWORDS={'where','on','using','join','left','right','inner','outer','cross','full','order','group','limit',
          'having','union','except','intersect','set','returning','indexed','not','window'}


def serialized(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        connection = self.connection if isinstance(self, sqlite3.Cursor) else self
        # sqlite3_column_text retains the GIL while waiting for SQLite's mutex.
        # Another thread's SQL callback needs that GIL: serialize before entering
        # SQLite, using a Python lock whose wait releases it.
        with connection.call_lock:
            return method(self, *args, **kwargs)
    return call


class Cursor(sqlite3.Cursor):
    execute = serialized(sqlite3.Cursor.execute)
    executemany = serialized(sqlite3.Cursor.executemany)
    executescript = serialized(sqlite3.Cursor.executescript)
    fetchone = serialized(sqlite3.Cursor.fetchone)
    fetchmany = serialized(sqlite3.Cursor.fetchmany)
    fetchall = serialized(sqlite3.Cursor.fetchall)
    __next__ = serialized(sqlite3.Cursor.__next__)
    close = serialized(sqlite3.Cursor.close)


class Connection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        self.call_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    @serialized
    def cursor(self, factory=Cursor):
        return super().cursor(factory)

    @serialized
    def executemany(self, sql, parameters, /):
        return self.cursor().executemany(sql, parameters)

    close = serialized(sqlite3.Connection.close)
    commit = serialized(sqlite3.Connection.commit)
    rollback = serialized(sqlite3.Connection.rollback)
    __enter__ = serialized(sqlite3.Connection.__enter__)
    __exit__ = serialized(sqlite3.Connection.__exit__)

    detail_tables=frozenset()
    _absent_views=(None,frozenset())
    stable_ids=False
    project_namespace=None

    @serialized
    def executescript(self,sql_script,/):
        if self.project_namespace is not None:
            from .shard_ids import script
            return script(self,sql_script)
        if self.stable_ids:
            from .shard_ids import ddl
            sql_script = ddl(sql_script)
        return self.cursor().executescript(sql_script)

    def _view_installed(self,table):
        """Whether another connection has installed `table`'s packed view since this one's list was made. The
        answer "not yet" is kept until the schema changes: scanning sqlite_master (hundreds of triggers) on
        every statement was a large part of each read."""
        version=sqlite3.Connection.execute(self,'PRAGMA schema_version').fetchone()[0]
        if self._absent_views[0]!=version:self._absent_views=(version,set())
        if table in self._absent_views[1]:return False
        if sqlite3.Connection.execute(self,"SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",('packed_'+table,)).fetchone():
            return True
        self._absent_views[1].add(table)
        return False

    def _route(self,sql):
        """Reads of a packed table go through its detail view. The rewrite depends only on the statement and
        the installed views, so it is done once per distinct statement (a regex pass per execute held the GIL
        on every read)."""
        tables=self.detail_tables
        if type(tables) is not frozenset:tables=self.detail_tables=frozenset(tables)
        key=(sql,tables)
        with _routes_lock:
            routed=_routes.get(key)
            if routed is not None:
                _routes.move_to_end(key)
                return routed
        probed=False
        if not DDL.match(sql):
            def part(text):
                def table(match):
                    nonlocal probed
                    table=match.group(2).lower()
                    if table not in self.detail_tables and table in FIELDS:
                        # Not cached: a view installed later by another connection must still be found.
                        probed=True
                        if self._view_installed(table):self.detail_tables=self.detail_tables|{table}
                    if table not in self.detail_tables:return match.group()
                    if re.search(r'\bDELETE\s*$',text[:match.start()],re.I):return match.group()
                    view={'attempts':'attempt_details','repair_examples':'repair_details'}.get(table,'packed_'+table)
                    alias=ALIAS.match(text,match.end())
                    suffix='' if alias and alias[1].lower() not in KEYWORDS else ' AS '+match.group(2)
                    return match.group(1)+' '+view+suffix
                return TABLE.sub(table,text)
            bits=QUOTED.split(sql)
            routed=''.join(part(bit) if i%2==0 else bit for i,bit in enumerate(bits))
        else:routed=sql
        if not probed:
            with _routes_lock:
                _routes[key]=routed
                while len(_routes)>ROUTES:_routes.popitem(last=False)
        return routed

    @serialized
    def execute(self,sql,parameters=(),/):
        creating = False
        if self.stable_ids:
            from .shard_ids import ddl, table_ddl
            creating = table_ddl(sql)
            if creating:
                sql = ddl(sql)
                if self.project_namespace is not None:
                    before = sqlite3.Connection.execute(self,'PRAGMA schema_version').fetchone()[0]
        sql=self._route(sql)
        result = self.cursor().execute(sql,parameters)
        if creating and self.project_namespace is not None:
            after = sqlite3.Connection.execute(self,'PRAGMA schema_version').fetchone()[0]
            if after != before:
                from .shard_ids import seed
                seed(self)
        return result


def install(conn,table='attempts'):
    conn.execute('CREATE TABLE IF NOT EXISTS text_payloads(hash TEXT PRIMARY KEY,payload BLOB NOT NULL,raw_bytes INTEGER NOT NULL)')
    from .payload_sizes import install as install_sizes
    install_sizes(conn)
    field,ref,view=('source','source_hash','attempt_details') if table=='attempts' else ('evidence','evidence_hash','repair_details')
    columns=[r[1] for r in conn.execute('PRAGMA table_info('+table+')')]
    if ref not in columns:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {ref} TEXT')
        columns.append(ref)
    from .payload_links import install as install_links
    install_links(conn,table,ref)
    if table=='repair_examples':
        from .repair_archive import install as install_archive
        install_archive(conn,columns)
        if isinstance(conn,Connection):conn.detail_tables=frozenset(conn.detail_tables)|{table}
        return
    if table=='attempts':
        if 'source_digest' not in columns:
            conn.execute('ALTER TABLE attempts ADD COLUMN source_digest TEXT')
            columns.append('source_digest')
        for event in ('INSERT','UPDATE OF source_hash'):
            name='attempt_digest_insert' if event=='INSERT' else 'attempt_digest_update'
            conn.execute(f'CREATE TRIGGER IF NOT EXISTS {name} AFTER {event} ON attempts '
                         'WHEN NEW.source_hash IS NOT NULL BEGIN UPDATE attempts SET '
                         'source_digest=NEW.source_hash WHERE id=NEW.id; END')
    expression=f'CASE WHEN {ref} IS NULL THEN {field} ELSE payload_read({ref},(SELECT payload FROM text_payloads WHERE hash={ref})) END AS {field}'
    conn.execute(f'DROP VIEW IF EXISTS {view}')
    conn.execute(f'CREATE VIEW {view} AS SELECT '+','.join(expression if col==field else col for col in columns)+f' FROM {table}')
    put=f'INSERT OR IGNORE INTO text_payloads SELECT payload_digest(NEW.{field}),payload_pack(NEW.{field}),length(CAST(NEW.{field} AS BLOB)) WHERE NOT EXISTS(SELECT 1 FROM text_payloads WHERE hash=payload_digest(NEW.{field}));'
    stored='NULL' if table=='attempts' else f'repair_metadata(NEW.{field})'
    for event in ('INSERT','UPDATE'):
        condition=f'NEW.{field} IS NOT NULL AND '+(f'NEW.{ref} IS NULL' if event=='INSERT' else f'NEW.{ref} IS OLD.{ref}')
        conn.execute(f'CREATE TRIGGER IF NOT EXISTS pack_{table}_{event.lower()} AFTER {event}'+(f' OF {field}' if event=='UPDATE' else '')+f' ON {table} WHEN {condition} BEGIN '+put+
                     f'UPDATE {table} SET {ref}=payload_digest(NEW.{field}),{field}={stored} WHERE id=NEW.id; END')
    if table=='attempts':
        conn.execute('CREATE TRIGGER IF NOT EXISTS clear_attempt_source AFTER UPDATE OF source ON attempts '
                     'WHEN NEW.source IS NULL AND OLD.source IS NULL AND NEW.source_hash IS OLD.source_hash AND NEW.source_hash IS NOT NULL '
                     'BEGIN UPDATE attempts SET source_hash=NULL WHERE id=NEW.id; END')
    if isinstance(conn,Connection):conn.detail_tables=frozenset(conn.detail_tables)|{table}
