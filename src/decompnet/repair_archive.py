"""Immutable, portable repair cases outside the project's operational database.

Archive commits precede main-index references. Interrupted main transactions may leave
unreferenced archive cases, but can never publish a reference before its case is durable.
"""
import hashlib
import json
import sqlite3
import threading
import time
import zlib
from contextlib import contextmanager
from pathlib import Path

from .writer_gate import WriterGate

SCHEMA='''CREATE TABLE IF NOT EXISTS contents(hash TEXT PRIMARY KEY,payload BLOB NOT NULL,raw_bytes INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cases(hash TEXT PRIMARY KEY,record_hash TEXT NOT NULL,snapshot_hash TEXT NOT NULL,created_at REAL NOT NULL);
PRAGMA user_version=1;'''


def archive_path(path):
    path=Path(path).resolve()
    return path.with_name('repair-evidence.sqlite3' if path.name=='decomp.db' else path.name+'.repair-evidence.sqlite3')


class Archive:
    def __init__(self,path):
        self.path=str(Path(path).resolve()) if str(path)!=':memory:' else ':memory:'
        self.gate=WriterGate(self.path)
        self.lock=threading.RLock()
        self.memory=sqlite3.connect(':memory:',check_same_thread=False) if self.path==':memory:' else None
        if self.memory:self.memory.executescript(SCHEMA)

    @contextmanager
    def connection(self,write=False):
        if self.memory:
            with self.lock:yield self.memory
            return
        if not write and not Path(self.path).is_file():raise ValueError('Repair evidence database is missing. Restore it from backup.')
        conn=sqlite3.connect(self.path if write else Path(self.path).as_uri()+'?mode=ro',uri=not write,timeout=5)
        try:
            if write:
                if conn.execute('PRAGMA user_version').fetchone()[0] not in (0,1):raise ValueError('Repair evidence database requires a newer app.')
                conn.execute('PRAGMA journal_mode=WAL')
                conn.executescript(SCHEMA)
            yield conn
        finally:conn.close()

    def put(self,record,snapshot):
        shared={}
        def separate(value):
            if isinstance(value,str) and len(value.encode())>=512:
                key=hashlib.sha256(value.encode()).hexdigest();shared[key]=value
                return {'$repair_text_sha256':key}
            if isinstance(value,dict):return {k:separate(v) for k,v in value.items()}
            if isinstance(value,list):return [separate(v) for v in value]
            return value
        texts=[record,json.dumps(separate(snapshot),sort_keys=True,separators=(',',':'))]
        keys=[hashlib.sha256(t.encode()).hexdigest() for t in texts]
        key=hashlib.sha256(('1:'+':'.join(keys)).encode()).hexdigest()
        shared.update(zip(keys,texts))
        blobs=[(k,zlib.compress(t.encode()),len(t.encode())) for k,t in shared.items()]
        with self.gate.hold(),self.connection(write=True) as db:
            with db:
                db.executemany('INSERT OR IGNORE INTO contents VALUES(?,?,?)',blobs)
                db.execute('INSERT OR IGNORE INTO cases VALUES(?,?,?,?)',(key,*keys,time.time()))
        return key

    def read(self,key,snapshot=False):
        with self.connection() as db:
            row=db.execute('SELECT record_hash,snapshot_hash FROM cases WHERE hash=?',(key,)).fetchone()
            if row is None:raise ValueError('Repair evidence is missing. Restore its separate database from backup.')
            def content(digest):
                row=db.execute('SELECT payload FROM contents WHERE hash=?',(digest,)).fetchone()
                if row is None:raise ValueError('Repair evidence content is missing.')
                raw=zlib.decompress(row[0])
                if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Repair evidence checksum failed.')
                return raw.decode()
            if hashlib.sha256(('1:'+':'.join(row)).encode()).hexdigest()!=key:raise ValueError('Repair evidence case checksum failed.')
            text=content(row[1] if snapshot else row[0])
            def resolve(value):
                if isinstance(value,dict):
                    if set(value)=={'$repair_text_sha256'}:return content(value['$repair_text_sha256'])
                    return {k:resolve(v) for k,v in value.items()}
                if isinstance(value,list):return [resolve(v) for v in value]
                return value
            return json.dumps(resolve(json.loads(text))) if snapshot else text


    def export(self,destination,reviews=()):
        """Consistent standalone SQLite copy, including committed WAL content."""
        if Path(destination).resolve()==Path(self.path).resolve():raise ValueError('Export to a different file.')
        with self.connection() as source:
            target=sqlite3.connect(destination)
            try:
                source.backup(target,pages=256)
                target.execute('CREATE TABLE IF NOT EXISTS reviews(case_hash TEXT,example_id INTEGER,project TEXT,function TEXT,category TEXT,explanation TEXT,automation TEXT,status TEXT,review_note TEXT,PRIMARY KEY(case_hash,example_id))')
                target.executemany('INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',reviews)
                target.commit()
            finally:target.close()


def freeze(conn,text):
    """Resolve historical references while inputs are still present; record missing inputs honestly."""
    try:evidence=json.loads(text)
    except (ValueError,TypeError):return {'format':1,'evidence':text}
    if not isinstance(evidence,dict):return {'format':1,'evidence':evidence}
    snapshot=json.loads(text)
    def one(sql,args):
        cursor=conn.execute(sql,args);row=cursor.fetchone()
        return dict(zip((c[0] for c in cursor.description),row)) if row is not None else None
    fid=evidence.get('function_id')
    fn=one('SELECT f.target,f.relocs,p.slug,p.name,p.arch,p.big_endian FROM functions f JOIN projects p ON p.id=f.project_id WHERE f.id=?',(fid,)) if fid else None
    missing=[]
    if fn:
        snapshot['project']={k:fn[k] for k in ('slug','name','arch','big_endian')}
        target=fn['target']
        if target and hashlib.sha256(target).hexdigest()==evidence.get('target_sha256'):
            snapshot['target_hex']=target.hex();snapshot['target_relocs']=fn['relocs']
        else:missing.append('original target bytes')
    for stage in ('before','after','previous'):
        value=snapshot.get(stage)
        if not isinstance(value,dict):continue
        aid=value.get('attempt_id')
        row=one('SELECT source,score,function_id FROM attempts WHERE id=?',(aid,)) if aid else None
        if row and row['function_id']==fid and row['source'] is not None and hashlib.sha256(row['source'].encode()).hexdigest()==value.get('source_sha256'):
            value['source']=row['source'];value['score']=row['score']
        if value.get('source') is None:missing.append(stage+' source')
        settings=value.get('settings') or {}
        if settings.get('mode')=='project':missing.append(stage+' imported build tree')
        expected=settings.get('context_sha256')
        if expected:
            header=one('SELECT header FROM context_versions WHERE project_id=(SELECT project_id FROM functions WHERE id=?) AND version=? AND unit=?',
                                (fid,value.get('context_version'),settings.get('context_unit') or ''))
            if header and header['header'] is not None and hashlib.sha256(header['header'].encode()).hexdigest()==expected:value['header']=header['header']
            elif value.get('header') is None:missing.append(stage+' header')
    return {'format':1,'evidence':snapshot,'missing_inputs':missing}


def register(conn):
    # PRAGMA must bypass Connection's SQL rewriting while its functions are registered.
    path=sqlite3.Connection.execute(conn,'PRAGMA database_list').fetchone()[2]
    archive=getattr(conn,'repair_archive',None) or Archive(archive_path(path) if path else ':memory:')
    try:conn.repair_archive=archive
    except AttributeError:pass
    conn.create_function('repair_archive_read',1,archive.read)
    conn.create_function('repair_archive_put',1,lambda text:archive.put(text,freeze(conn,text)))


def install(conn,columns):
    if 'evidence_archive' not in columns:
        conn.execute('ALTER TABLE repair_examples ADD COLUMN evidence_archive TEXT')
        columns.append('evidence_archive')
    expression='CASE WHEN evidence_archive IS NOT NULL THEN repair_archive_read(evidence_archive) WHEN evidence_hash IS NOT NULL THEN payload_read(evidence_hash,(SELECT payload FROM text_payloads WHERE hash=evidence_hash)) ELSE evidence END AS evidence'
    conn.execute('DROP VIEW IF EXISTS repair_details')
    conn.execute('CREATE VIEW repair_details AS SELECT '+','.join(expression if c=='evidence' else c for c in columns)+' FROM repair_examples')
    for event in ('insert','update'):
        conn.execute('DROP TRIGGER IF EXISTS pack_repair_examples_'+event)
        conn.execute(f'CREATE TRIGGER pack_repair_examples_{event} AFTER {event.upper()}'+(' OF evidence' if event=='update' else '')+
            ' ON repair_examples WHEN NEW.evidence IS NOT NULL AND '+('NEW.evidence_archive IS NULL' if event=='insert' else 'NEW.evidence_archive IS OLD.evidence_archive')+
            ' BEGIN UPDATE repair_examples SET evidence_archive=repair_archive_put(NEW.evidence),evidence_hash=NULL,evidence=repair_metadata(NEW.evidence) WHERE id=NEW.id; END')


def migrate(net,log, *, store=None):
    """Move one historical case per short main transaction; stop/resume without a startup scan."""
    from .app.maintenance import checkpoint
    store = net.store if store is None else store
    cursor=0;done=0
    while True:
        log.check_cancel()
        row=store.one('SELECT id FROM main.repair_examples WHERE id>? AND evidence_archive IS NULL ORDER BY id LIMIT 1',cursor)
        if row is None:break
        cursor=row['id']
        with store.tx(wait=log.check_cancel) as db:
            row=db.execute('SELECT evidence FROM repair_examples WHERE id=? AND evidence_archive IS NULL',(cursor,)).fetchone()
            if row:
                db.execute('UPDATE repair_examples SET evidence=? WHERE id=? AND evidence_archive IS NULL',(row['evidence'],cursor))
                done+=1
        log.progress({'phase':'Moving repair evidence','done':done,'function':str(cursor)})
        checkpoint()
    # Shared-payload GC remains in explicit Compress existing records, rather than
    # scanning every large operational table at the end of startup migration.
    return done
