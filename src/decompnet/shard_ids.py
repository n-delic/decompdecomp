"""Stable SQLite row IDs for isolated project files, without a shared writer.

Legacy IDs occupy the low 32 bits. A project's catalog ID selects a disjoint
32-bit allocation range, kept below JavaScript's exact integer limit. SQLite's
AUTOINCREMENT sequence owns allocation, rollback and deletion semantics.
This changes schema initialization only; it never chooses a database from SQL.
"""
import hashlib
import re
import sqlite3

BLOCK = 1 << 32
MAX_NAMESPACE = (1 << 21)-1
KEY = 'project-id-namespace'
STABLE = 'stable-row-identifiers'
TOKEN = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]|--[^\n]*|/\*[\s\S]*?\*/|\s+|\w+|.")


def bounds(namespace):
    if type(namespace) is not int or not 1<=namespace<=MAX_NAMESPACE:
        raise ValueError('Project identifier namespace must be a positive catalog ID below 2,097,152.')
    return namespace*BLOCK,(namespace+1)*BLOCK-1


def tokens(sql):
    return TOKEN.findall(sql)


def significant(parts):
    return [i for i,part in enumerate(parts) if not part.isspace() and not part.startswith(('--','/*'))]


def word(token):
    if token[:1] in ('"',"'",'`','['):token = token[1:-1]
    return token.upper()


LEADING = r"\s*(?:(?:--[^\n]*(?:\n|$)|/\*[\s\S]*?\*/)\s*)*"


def table_ddl(sql):
    return re.match(LEADING+r'CREATE\s+(?:(?:TEMP|TEMPORARY)\s+)?TABLE\b',sql,re.I) is not None


def script(conn, sql):
    """Keep executescript's commit behavior, seeding each table before later INSERTs."""
    if conn.in_transaction:sqlite3.Connection.execute(conn,'COMMIT').close()
    start = 0
    for end,char in enumerate(sql):
        if char == ';' and sqlite3.complete_statement(sql[start:end+1]):
            conn.execute(sql[start:end+1]).close()
            start = end+1
    if sql[start:].strip():conn.execute(sql[start:]).close()
    return sqlite3.Connection.cursor(conn)


def ddl(sql):
    """Preserve literals/comments while making generated INTEGER PRIMARY KEY IDs durable."""
    parts = tokens(sql)
    indices = significant(parts)
    for at in range(len(indices)-3):
        group = indices[at:at+4]
        if [word(parts[i]) for i in group] != ['ID','INTEGER','PRIMARY','KEY']:continue
        end = at+3
        if end+1 < len(indices) and word(parts[indices[end+1]]) == 'DESC':continue  # Not a rowid alias in SQLite.
        if end+1 < len(indices) and word(parts[indices[end+1]]) == 'ASC':end += 1
        if end+3 < len(indices) and [word(parts[indices[end+j]]) for j in (1,2)] == ['ON','CONFLICT']:end += 3
        if end+1 < len(indices) and word(parts[indices[end+1]]) == 'AUTOINCREMENT':continue
        parts[indices[end]] += ' AUTOINCREMENT'
    return ''.join(parts)


def generated_tables(conn):
    raw = lambda sql:sqlite3.Connection.execute(conn,sql)
    result = []
    for name,sql in raw("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall():
        quoted = '"'+name.replace('"','""')+'"'
        primary = [c for c in raw('PRAGMA table_info('+quoted+')').fetchall() if c[5]]
        if len(primary)!=1 or primary[0][1].lower()!='id' or primary[0][2].upper()!='INTEGER':continue
        if any(row[3]=='pk' for row in raw('PRAGMA index_list('+quoted+')').fetchall()):continue  # Not a rowid alias.
        if not any(part.upper()=='AUTOINCREMENT' for part in tokens(sql)):
            raise ValueError('Legacy project identifier tables must be migrated before assigning an identifier namespace.')
        result.append(name)
    return result


def bind(conn, namespace, stable=False):
    """Validate an existing file before its schema or counters can be changed."""
    if type(stable) is not bool:raise ValueError('Stable row identifiers must be enabled or disabled.')
    if namespace is not None:bounds(namespace)
    raw = lambda sql,*args:sqlite3.Connection.execute(conn,sql,args)
    has_settings = raw("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
    marker = raw('SELECT value FROM settings WHERE key=?',KEY).fetchone() if has_settings else None
    if marker and (namespace is None or marker[0] != str(namespace)):
        raise ValueError('This project database must be opened with its original catalog identifier namespace.')
    saved = raw('SELECT value FROM settings WHERE key=?',STABLE).fetchone() if has_settings else None
    if namespace is not None:generated_tables(conn)
    conn.project_namespace = namespace
    conn.stable_ids = bool(namespace is not None or stable or saved and saved[0]=='1')


def seed(conn):
    """Called after creating tables; all writes stay on this file's current transaction."""
    namespace = getattr(conn,'project_namespace',None)
    if namespace is None:return
    floor,maximum = bounds(namespace)
    raw = lambda sql,*args:sqlite3.Connection.execute(conn,sql,args)
    tables = generated_tables(conn)
    plan = []
    for name in tables:
        quoted = '"'+name.replace('"','""')+'"'
        counter = raw('SELECT seq FROM sqlite_sequence WHERE name=?',name).fetchone()
        if counter and int(counter[0]) > maximum:
            raise ValueError('Project identifier allocation exceeds its catalog namespace. Restore a consistent backup.')
        plan.append((name,quoted,counter))
    for name,quoted,counter in plan:
        if counter is None:raw('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)',name,floor)
        elif int(counter[0]) < floor:raw('UPDATE sqlite_sequence SET seq=? WHERE name=?',floor,name)
        trigger = 'project_id_bound_'+hashlib.sha256(name.encode()).hexdigest()[:20]
        raw(f'CREATE TRIGGER IF NOT EXISTS {trigger} AFTER INSERT ON {quoted} '
            f'WHEN NEW.id>={BLOCK} AND (NEW.id<{floor} OR NEW.id>{maximum}) BEGIN '
            "SELECT RAISE(ABORT,'Project identifier range exhausted or incorrect project identifier'); END")


def remember(conn):
    raw = lambda sql,*args:sqlite3.Connection.execute(conn,sql,args)
    if getattr(conn,'stable_ids',False):raw('INSERT OR REPLACE INTO settings VALUES(?,?)',STABLE,'1')
    namespace = getattr(conn,'project_namespace',None)
    if namespace is not None:raw('INSERT OR REPLACE INTO settings VALUES(?,?)',KEY,str(namespace))
