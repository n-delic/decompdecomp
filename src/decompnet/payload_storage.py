"""Compressed immutable text payloads with stable attempt IDs and lazy SQL detail views.

Physical tables retain metadata, foreign keys and INSERT/lastrowid semantics. Store connections
route reads through detail views; maintenance uses explicit main.table names to inspect raw storage.
Old rows remain readable until an explicit, bounded backfill packs them.
"""
import hashlib
import json
import re
import sqlite3
import threading
import zlib
from collections import OrderedDict

_cache=OrderedDict()
_cache_lock=threading.Lock()


def unpack(digest, payload):
    if payload is None:raise ValueError('Missing database payload; restore a database backup.')
    key=(digest,payload)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    raw=zlib.decompress(payload)
    if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Database payload checksum failed.')
    text=raw.decode('utf-8')
    # A few 80-MiB progress manifests must not turn a count-bounded cache into GiBs.
    if len(raw)<=256*1024:
        with _cache_lock:
            _cache[key]=text
            while len(_cache)>32:_cache.popitem(last=False)
    return text


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest() if text is not None else None


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
    conn.create_function('payload_pack',1,lambda text:zlib.compress(text.encode('utf-8')),deterministic=True)
    conn.create_function('payload_read',2,unpack,deterministic=True)
    conn.create_function('repair_metadata',1,metadata,deterministic=True)
    if isinstance(conn,Connection):
        conn.detail_tables={r[0].removesuffix('_details') for r in sqlite3.Connection.execute(conn,
            "SELECT name FROM sqlite_master WHERE type='view' AND name IN ('attempt_details','repair_details')")}
        conn.detail_tables={'attempts' if x=='attempt' else 'repair_examples' for x in conn.detail_tables}
        from .large_text_storage import FIELDS
        installed={r[0] for r in sqlite3.Connection.execute(conn,
            "SELECT name FROM sqlite_master WHERE type='view' AND name LIKE 'packed_%'")}
        conn.detail_tables|={t for t in FIELDS if 'packed_'+t in installed}


# Quoted values/comments must never be rewritten (including JSON paths and recorded SQL text).
QUOTED=re.compile(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/)")
from .large_text_storage import FIELDS

TABLE=re.compile(r'\b(FROM|JOIN)\s+('+ '|'.join(('attempts','repair_examples',*FIELDS))+r')\b',re.I)
ALIAS=re.compile(r'\s+(?:AS\s+)?([a-zA-Z_]\w*)',re.I)
KEYWORDS={'where','on','using','join','left','right','inner','outer','cross','full','order','group','limit',
          'having','union','except','intersect','set','returning','indexed','not','window'}


class Connection(sqlite3.Connection):
    detail_tables=frozenset()
    stable_ids=False
    project_namespace=None

    def executescript(self,sql_script,/):
        if self.project_namespace is not None:
            from .shard_ids import script
            return script(self,sql_script)
        if self.stable_ids:
            from .shard_ids import ddl
            sql_script = ddl(sql_script)
        return super().executescript(sql_script)

    def execute(self,sql,parameters=(),/):
        creating = False
        if self.stable_ids:
            from .shard_ids import ddl, table_ddl
            creating = table_ddl(sql)
            if creating:
                sql = ddl(sql)
                if self.project_namespace is not None:
                    before = sqlite3.Connection.execute(self,'PRAGMA schema_version').fetchone()[0]
        if not re.match(r'\s*(?:(?:--[^\n]*(?:\n|$)|/\*[\s\S]*?\*/)\s*)*(CREATE|DROP|ALTER|PRAGMA)\b',sql,re.I):
            def part(text):
                def table(match):
                    table=match.group(2).lower()
                    if table not in self.detail_tables and table in FIELDS:
                        if sqlite3.Connection.execute(self,"SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",('packed_'+table,)).fetchone():
                            self.detail_tables=set(self.detail_tables)|{table}
                    if table not in self.detail_tables:return match.group()
                    if re.search(r'\bDELETE\s*$',text[:match.start()],re.I):return match.group()
                    table=match.group(2).lower()
                    view={'attempts':'attempt_details','repair_examples':'repair_details'}.get(table,'packed_'+table)
                    alias=ALIAS.match(text,match.end())
                    suffix='' if alias and alias[1].lower() not in KEYWORDS else ' AS '+match.group(2)
                    return match.group(1)+' '+view+suffix
                return TABLE.sub(table,text)
            bits=QUOTED.split(sql)
            sql=''.join(part(bit) if i%2==0 else bit for i,bit in enumerate(bits))
        result = super().execute(sql,parameters)
        if creating and self.project_namespace is not None:
            after = sqlite3.Connection.execute(self,'PRAGMA schema_version').fetchone()[0]
            if after != before:
                from .shard_ids import seed
                seed(self)
        return result


def install(conn,table='attempts'):
    conn.execute('CREATE TABLE IF NOT EXISTS text_payloads(hash TEXT PRIMARY KEY,payload BLOB NOT NULL,raw_bytes INTEGER NOT NULL)')
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
        if isinstance(conn,Connection):conn.detail_tables=set(conn.detail_tables)|{table}
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
    if isinstance(conn,Connection):conn.detail_tables=set(conn.detail_tables)|{table}
