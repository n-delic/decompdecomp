"""Retain a reproducible last exact attempt; hashes describe evidence, never bypass a rejudge."""
import hashlib
import json
import math
import re
import time
from functools import lru_cache

from . import toolchains
from .project import compiler_for

SCHEMA = '''CREATE TABLE IF NOT EXISTS verified_matches(
 function_id INTEGER PRIMARY KEY REFERENCES functions(id) ON DELETE CASCADE,
 attempt_id INTEGER NOT NULL, details TEXT NOT NULL, source TEXT NOT NULL, header TEXT NOT NULL)'''


def digest(value):
    return hashlib.sha256(value if isinstance(value,bytes) else str(value).encode()).hexdigest()


@lru_cache(maxsize=128)
def binary_digest(path, size, modified):
    return digest(path.read_bytes())


def compiler_identity(settings):
    if settings and not isinstance(settings, dict):
        # Imported builds may name their compiler version directly. Preserve that
        # identity without treating it as one of the app's ROM toolchain profiles.
        return {'toolchain':None,'registry_sha256':None,'install_sha256':None,'executables':{},
                'build_compiler':settings}
    tool = settings.get('toolchain') if settings else None
    if not tool:
        return {'toolchain':None,'registry_sha256':None,'install_sha256':None,'executables':{}}
    try:
        spec = toolchains.get(tool)
        registry = repr(spec)
        manifest = toolchains.path(tool)/toolchains.MANIFEST
        installed = digest(manifest.read_bytes()) if manifest.is_file() else None
        binaries = {}
        root = toolchains.path(tool)
        for name in ('gcc','cc1','as','gcc.exe','cc1.exe','cc1n64.exe'):
            path = root/name
            if path.is_file():
                stat = path.stat()
                binaries[name] = binary_digest(path,stat.st_size,stat.st_mtime_ns)
        return {'toolchain':tool,'registry_sha256':digest(registry),'install_sha256':installed,'executables':binaries}
    except (toolchains.ToolchainError,KeyError,OSError):
        return {'toolchain':tool,'registry_sha256':None,'install_sha256':None,'executables':{}}


def abi(proj, cfg):
    return {'arch':proj['arch'],'big_endian':bool(proj['big_endian']),'gp':cfg.get('gp'),
            'platform':cfg.get('platform'),'rom_sha1':cfg.get('rom',{}).get('sha1')}


def symbols(db, project_id):
    return digest(repr([tuple(row) for row in db.execute(
        'SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name',(project_id,))]))


def capture(db, fn, attempt_id, source, context_version, settings):
    """Called only after a non-stale normal exact verdict, including later polishing attempts."""
    db.execute(SCHEMA)
    from .large_text_storage import install
    install(db, ('verified_matches',))
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='verification_regressions'").fetchone():
        db.execute('DELETE FROM verification_regressions WHERE function_id=?',(fn['id'],))
    proj = db.execute('SELECT * FROM projects WHERE id=?',(fn['project_id'],)).fetchone()
    cfg = json.loads(proj['config'])
    row = db.execute("SELECT header FROM context_versions WHERE project_id=? AND version=? AND unit IN (?, '') ORDER BY (unit=?) DESC LIMIT 1",
                     (fn['project_id'],context_version,fn['unit'] or '',fn['unit'] or '')).fetchone()
    header = row['header'] if row else ''
    recorded = db.execute('SELECT settings FROM repair_attempt_settings WHERE attempt_id=?',(attempt_id,)).fetchone()
    effective = json.loads(recorded['settings']) if recorded else (settings or {})
    compiler = effective.get('compiler') or (compiler_for(cfg,fn['vram'], fn) if cfg.get('compiler') else {})
    details = {'format':1,'verdict':'exact','abi':abi(proj,cfg),'symbols_sha256':symbols(db,fn['project_id']),'recorded_at':time.time(),'attempt_id':attempt_id,
               'source_sha256':digest(source),'header_sha256':digest(header) if row else None,
               'context_version':context_version,'compiler':compiler,'compiler_identity':compiler_identity(compiler),
               'settings':effective,'target_sha256':digest(fn['target'] or b''),
               'relocations_sha256':digest(fn['relocs'] or ''),'vram':fn['vram'],'unit':fn['unit'],
               'mode':'project' if cfg.get('kind')=='fork' else 'rom'}
    db.execute('INSERT OR REPLACE INTO verified_matches VALUES(?,?,?,?,?)',
               (fn['id'],attempt_id,json.dumps(details),source,header))


def status(net, fn):
    store = net.project_store(fn['project_id'])
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"):
        return None
    row = store.one('SELECT * FROM verified_matches WHERE function_id=?',fn['id'])
    if not row:
        return None
    details = json.loads(row['details'])
    proj = store.one('SELECT * FROM projects WHERE id=?',fn['project_id'])
    cfg = json.loads(proj['config'])
    context = net._context(proj['id'],fn['unit'])
    reasons = []
    source = fn['matched_source'] if fn['status']=='matched' else fn['best_source']
    if digest(source or '') != details['source_sha256']: reasons.append('source changed')
    if details['header_sha256'] is not None and digest(context['header'] if context else '') != details['header_sha256']: reasons.append('header changed')
    compiler = compiler_for(cfg,fn['vram'], fn) if cfg.get('compiler') else {}
    if compiler != details['compiler']: reasons.append('compiler settings changed')
    if compiler_identity(compiler) != details['compiler_identity']: reasons.append('compiler installation/registry changed')
    if digest(fn['target'] or b'') != details['target_sha256'] or digest(fn['relocs'] or '') != details['relocations_sha256'] or fn['vram'] != details['vram']: reasons.append('target/address changed')
    if details.get('abi') != abi(proj,cfg): reasons.append('ABI/platform/game identity changed')
    current_symbols = digest(repr([tuple(row) for row in store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name',fn['project_id'])]))
    if details.get('symbols_sha256') != current_symbols: reasons.append('symbol table changed')
    if fn['unit'] != details['unit']: reasons.append('function unit changed')
    if details.get('imported'): reasons.append('historical record imported; verify locally before accepting it')
    if details['header_sha256'] is None: reasons.append('header was not captured in the original record')
    if details['mode']=='project': reasons.append('imported build must be rechecked in its own build system')
    alarm = store.one('SELECT * FROM verification_regressions WHERE function_id=?',fn['id']) if store.one("SELECT 1 FROM sqlite_master WHERE name='verification_regressions'") else None
    return details | {'regression':dict(alarm) if alarm else None,'changed_inputs':reasons,'source':row['source'],'header':row['header'],
                      'note':'Last recorded exact attempt; recheck with the normal judge after any input change.'}


def portable(net, project_id):
    store = net.project_store(project_id)
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"):
        return {}
    # Group physical text/hash references before the detail view decodes them.
    # In particular, a large shared header must not be fetched once per match.
    # One statement keeps records and their distinct text in the same snapshot.
    rows = store.all('''WITH selected AS MATERIALIZED (
        SELECT b.function_id,f.name,
            min(b.function_id) OVER (PARTITION BY b.source) AS source_id,
            min(b.function_id) OVER (PARTITION BY b.header) AS header_id
        FROM main.verified_matches b JOIN functions f ON f.id=b.function_id WHERE f.project_id=?)
        SELECT 'match' AS kind,s.function_id,s.name,b.details AS value,s.source_id,s.header_id
        FROM selected s JOIN verified_matches b ON b.function_id=s.function_id
        UNION ALL
        SELECT 'source',b.function_id,NULL,b.source,NULL,NULL FROM verified_matches b
        JOIN (SELECT DISTINCT source_id FROM selected) s ON b.function_id=s.source_id
        UNION ALL
        SELECT 'header',b.function_id,NULL,b.header,NULL,NULL FROM verified_matches b
        JOIN (SELECT DISTINCT header_id FROM selected) s ON b.function_id=s.header_id
        ORDER BY kind,function_id''', project_id)
    text = {'source':{}, 'header':{}}
    for row in rows:
        if row['kind'] != 'match':text[row['kind']][row['function_id']] = row['value']
    sources, headers, matches = {}, {}, {}
    for row in rows:
        if row['kind'] != 'match':continue
        details = json.loads(row['value'])
        sources[details['source_sha256']] = text['source'][row['source_id']]
        if details['header_sha256']: headers[details['header_sha256']] = text['header'][row['header_id']]
        matches[row['name']] = details
    return {'matches':matches,'sources':sources,'headers':headers} if matches else {}


def _imported_evidence(records, sources, headers, old_name, checked_digest):
    detail = records.get(old_name)
    required = {'source_sha256','header_sha256','compiler','compiler_identity','target_sha256',
                'relocations_sha256','vram','unit','mode','recorded_at','abi','symbols_sha256'}
    if not isinstance(detail,dict) or not required <= detail.keys(): return None
    if detail.get('format') != 1 or detail.get('verdict') != 'exact': return None
    for key in ('source_sha256','header_sha256','target_sha256','relocations_sha256','symbols_sha256'):
        if key == 'header_sha256' and detail[key] is None: continue
        if not isinstance(detail[key],str) or not re.fullmatch('[0-9a-f]{64}',detail[key]): return None
    if not isinstance(detail['recorded_at'],(int,float)) or not math.isfinite(detail['recorded_at']): return None
    source, header = sources.get(detail['source_sha256']), headers.get(detail['header_sha256'],'')
    if not isinstance(source,str) or not isinstance(header,str) or len(source)>2_000_000 or len(header)>2_000_000: return None
    if checked_digest(source)!=detail['source_sha256'] or (detail['header_sha256'] is not None and checked_digest(header)!=detail['header_sha256']): return None
    if not isinstance(detail['compiler'],dict) or not isinstance(detail['compiler_identity'],dict): return None
    if not isinstance(detail['unit'],str) or not isinstance(detail['mode'],str) or detail['mode'] not in {'rom','project'}: return None
    return json.dumps(dict(detail, imported=True)), source, header


def restore_evidence_many(net, project_id, names, manifest, check_cancel=lambda: None):
    """Import missing history in short batches, hashing shared text once per scan.

    Existing rows win just as with INSERT OR IGNORE. Historical evidence never selects
    source, changes compiler settings or awards a match; the judge still does that.
    """
    store = net.project_store(project_id)
    section = manifest.get('verification')
    if not isinstance(section, dict): return
    records, sources, headers = (section.get(key) for key in ('matches','sources','headers'))
    if not all(isinstance(value, dict) for value in (records,sources,headers)): return
    check_cancel()
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"):
        with store.tx() as db:
            db.execute(SCHEMA)
            from .large_text_storage import install
            install(db, ('verified_matches',))
    missing = {row['name']:row['id'] for row in store.all(
        'SELECT f.id,f.name FROM functions f LEFT JOIN verified_matches v ON v.function_id=f.id '
        'WHERE f.project_id=? AND v.function_id IS NULL', project_id)}
    if not missing: return
    # The cache lives only for this immutable manifest scan, never across file edits.
    hashes = {}
    def checked_digest(text):
        if text not in hashes: hashes[text] = digest(text)
        return hashes[text]
    pending = []
    def flush():
        check_cancel()
        with store.tx() as db:
            db.executemany('INSERT OR IGNORE INTO verified_matches VALUES(?,?,?,?,?)', pending)
        pending.clear()
    for name, old_name in names:
        check_cancel()
        if name not in missing: continue
        evidence = _imported_evidence(records, sources, headers, old_name, checked_digest)
        if evidence is not None:
            pending.append((missing.pop(name), -1, *evidence))
            if len(pending) >= 64: flush()
    if pending: flush()


def restore_evidence(net, project_id, name, old_name, manifest):
    """Import hash-checked historical text only, without applying a verdict."""
    restore_evidence_many(net, project_id, [(name, old_name)], manifest)


ALERT_SCHEMA = '''CREATE TABLE IF NOT EXISTS verification_regressions(
 function_id INTEGER PRIMARY KEY REFERENCES functions(id) ON DELETE CASCADE,
 attempt_id INTEGER NOT NULL, score INTEGER, observed_at REAL NOT NULL)'''


def failed_recheck(db, fn, attempt_id, source, score):
    """Only a new judge result for the captured exact source can raise an alarm, never a random variant."""
    # Scratch and polishing attempts with score zero still passed the judge, even though
    # _record's matched flag is false to avoid awarding another match or replacing its owner.
    if score == 0:
        return False
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='verified_matches'").fetchone():
        return False
    row = db.execute('SELECT details,source FROM verified_matches WHERE function_id=?',(fn['id'],)).fetchone()
    if not row or row['source'] != source or json.loads(row['details']).get('imported'):
        return False
    db.execute(ALERT_SCHEMA)
    exists = db.execute('SELECT 1 FROM verification_regressions WHERE function_id=?',(fn['id'],)).fetchone()
    db.execute('INSERT OR REPLACE INTO verification_regressions VALUES(?,?,?,?)',(fn['id'],attempt_id,score,time.time()))
    return not exists
