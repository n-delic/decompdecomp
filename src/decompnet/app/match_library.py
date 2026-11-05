"""Private, deduplicated verified C and game membership, independent of project lifetimes.

Fingerprints find candidates, never grant a match. Every transfer uses the destination's
ordinary compiler and byte/object judge. No ROM or object bytes are stored here.
"""
import hashlib
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from ..network import NetworkError

log = logging.getLogger(__name__)
SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS functions(
 id INTEGER PRIMARY KEY, arch TEXT NOT NULL, big INTEGER NOT NULL, fingerprint TEXT NOT NULL,
 size INTEGER NOT NULL, UNIQUE(arch,big,fingerprint,size));
CREATE TABLE IF NOT EXISTS texts(hash TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS solutions(
 id INTEGER PRIMARY KEY, function_id INTEGER NOT NULL, source TEXT NOT NULL, header TEXT NOT NULL,
 compiler TEXT NOT NULL, UNIQUE(function_id,source,header,compiler));
CREATE TABLE IF NOT EXISTS games(id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS occurrences(
 game_id TEXT NOT NULL, solution_id INTEGER NOT NULL, name TEXT NOT NULL, mode TEXT NOT NULL,
 print TEXT NOT NULL, symbols TEXT NOT NULL, seen REAL NOT NULL,
 UNIQUE(game_id,solution_id,name,print,symbols));
CREATE INDEX IF NOT EXISTS occurrences_solution ON occurrences(solution_id);
'''


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


class Library:
    def __init__(self, net):
        self.net = net
        self.path = Path(net.store.path).parent / 'match-library.sqlite3'
        # No connection is shared between threads or held open by idle projects.
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def game_key(self, proj):
        cfg=self.net.cfg(proj)
        identity=cfg.get('rom',{}).get('sha1') or cfg.get('repo') or proj['slug']
        return hashlib.sha256(encoded((identity,cfg.get('platform') or proj['arch'],proj['name'])).encode()).hexdigest()

    def snapshot(self, proj, fid):
        # Snapshot already verified database state. Calling project helpers here must not
        # re-read/reconcile an entire project's progress files for every matched function.
        with self.net.progress.view():
            return self._snapshot(proj, fid)

    def _snapshot(self, proj, fid):
        from ..project import compiler_for
        from .shared_reuse import machine
        net = self.net
        proj = net.store.one('SELECT * FROM projects WHERE id=?',proj['id']) or proj
        fn = net.store.one("SELECT * FROM functions WHERE id=? AND project_id=? AND status='matched'", fid, proj['id'])
        if not fn:
            return None
        p = net.twins.prints(proj).get(fid)
        source = fn['matched_source']
        fork = bool(net.fork_slug(proj['slug']))
        compiler = compiler_for(net.cfg(proj), fn['vram'], fn) if not fork else {}
        if fork:
            from .twins import _file_compiler
            _, fp, _ = net.forks.get(proj['slug'])
            source = source or net.forks._current_source(fp, fn)
            compiler = {'toolchain':_file_compiler(net.console(proj), fp, fn), 'flags':[]}
        if not source:
            return None
        header = (net._context(proj['id'], fn['unit']) or {'header':''})['header']
        if net.store.one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"):
            proof=net.store.one('SELECT source,header,details FROM verified_matches WHERE function_id=?',fid)
            if proof and proof['source']==source:
                header=proof['header']
                compiler=json.loads(proof['details']).get('compiler') or compiler
        # Store only the named symbols needed to relocate this C, not the game's full symbol table.
        words = set(re.findall(r'\b[A-Za-z_]\w*\b', source))
        symbols = [] if fork else [asdict(s) for s in net.symbols(proj['id']).by_name.values() if s.name in words]
        processor, big = machine(net, proj)
        # Same source copied under a generated function name shares its stored body.
        from .twins import apply
        if p is None:
            from .twins import Print
            p=Print('source-only:'+hashlib.sha256(source.encode()).hexdigest(),[],fn['size'] or 0)
        own_name = p.name or fn['name']
        token='__library_function'
        while token!=own_name and re.search(r'\b'+token+r'\b',source+'\n'+header):token+='_'
        mapping = {own_name:token} if re.fullmatch(r'[A-Za-z_]\w*', own_name) else {}
        source = apply(source, mapping)
        p = asdict(p);p['name'] = mapping.get(own_name, own_name)
        for s in symbols:
            s['name'] = mapping.get(s['name'], s['name'])
        game = self.game_key(proj)
        return {'arch':processor,'big':int(big),'fingerprint':p['key'],'size':p['size'],
                'source':source,'header':header,'compiler':encoded(compiler),
                'game':game,'game_name':proj['name'],'slug':proj['slug'],'name':fn['name'],
                'mode':'fork' if fork else 'rom','print':encoded(p),'symbols':encoded(symbols),'seen':fn['matched_at'] or 0}

    def write(self, rows):
        with self.connect() as db:
            for item in rows:
                hashes = {}
                for kind in ('source','header','print','symbols'):
                    value = item[kind]
                    digest = hashlib.sha256(value.encode()).hexdigest()
                    db.execute('INSERT OR IGNORE INTO texts VALUES(?,?)',(digest,value))
                    hashes[kind] = digest
                db.execute('INSERT OR IGNORE INTO functions(arch,big,fingerprint,size) VALUES(?,?,?,?)',
                           tuple(item[k] for k in ('arch','big','fingerprint','size')))
                fid = db.execute('SELECT id FROM functions WHERE arch=? AND big=? AND fingerprint=? AND size=?',
                                 tuple(item[k] for k in ('arch','big','fingerprint','size'))).fetchone()['id']
                db.execute('INSERT OR IGNORE INTO solutions(function_id,source,header,compiler) VALUES(?,?,?,?)',
                           (fid,hashes['source'],hashes['header'],item['compiler']))
                sid = db.execute('SELECT id FROM solutions WHERE function_id=? AND source=? AND header=? AND compiler=?',
                                 (fid,hashes['source'],hashes['header'],item['compiler'])).fetchone()['id']
                db.execute('INSERT OR REPLACE INTO games VALUES(?,?,?)',(item['game'],item['game_name'],item['slug']))
                db.execute('INSERT OR REPLACE INTO occurrences VALUES(?,?,?,?,?,?,?)',
                           (item['game'],sid,item['name'],item['mode'],hashes['print'],hashes['symbols'],item['seen']))

    def capture(self, proj, fid):
        """Freeze the verified inputs now; publish only after the enclosing judge commits."""
        try:
            item = self.snapshot(proj, fid)
            if item:
                def publish():
                    try:self.write([item])
                    except (sqlite3.Error, OSError):log.exception('Could not archive verified match; startup will retry')
                self.net.store.after_commit(publish)
        except Exception:  # archiving must never turn a committed exact verdict into a judge failure
            log.exception('Could not snapshot verified match for the local library')

    def archive_project(self, proj, log=None, *, rows=None, progress=None):
        if rows is None:
            rows=self.net.store.all("SELECT id,name FROM functions WHERE project_id=? AND status='matched'",proj['id'])
        def report(done, phase, name=''):
            if log:
                log.check_cancel()
                value={'phase':phase,'function':name,'game':proj['name'],'done':done,'total':len(rows)}
                if progress:progress(value)
                else:log.progress(value)
        report(0,'Reading verified matches')
        batch=[]
        for i, row in enumerate(rows):
            report(i,'Reading verified matches',row['name'])
            item=self.snapshot(proj,row['id'])
            if item:batch.append(item)
            if len(batch)>=64:
                report(i,'Writing matches to library',row['name'])
                self.write(batch);batch=[]
        if batch:
            report(len(rows)-1,'Writing matches to library')
            self.write(batch)
        report(len(rows),'Game indexed')
        return len(rows)

    def backfill(self, log=None):
        failures=[]
        if log:log.progress({'phase':'Counting verified matches','done':0,'total':0})
        projects=[]
        for proj in self.net.store.all('SELECT * FROM projects ORDER BY id'):
            if log:log.check_cancel()
            rows=self.net.store.all("SELECT id,name FROM functions WHERE project_id=? AND status='matched'",proj['id'])
            projects.append((proj,rows))
        total=sum(len(rows) for _,rows in projects)
        completed=0
        for index,(proj,rows) in enumerate(projects,1):
            if log:log.check_cancel()
            game_total=len(rows)
            def progress(value, game_total=game_total, game_index=index, offset=completed):
                log.progress({**value,'game_done':value['done'],'game_total':game_total,
                              'game_index':game_index,'games_total':len(projects),
                              'done':offset+value['done'],'total':total})
            try:self.archive_project(proj,log,rows=rows,progress=progress)
            except (NetworkError,OSError,ValueError) as error:
                failures.append(proj['name']+': '+str(error))
            completed+=len(rows)
        if failures:raise NetworkError('Some projects could not be indexed: '+'; '.join(failures))
        if log:log.progress({'phase':'Indexing complete','done':total,'total':total})
        return self.summary()

    def summary(self, search=''):
        with self.connect() as db:
            counts={table:db.execute(f'SELECT COUNT(*) n FROM {table}').fetchone()['n']
                    for table in ('functions','solutions','games','occurrences')}
            rows=db.execute('''SELECT f.id,f.arch,f.size,MIN(o.name) name,COUNT(DISTINCT g.id) games,
                GROUP_CONCAT(DISTINCT g.name) game_names,COUNT(DISTINCT s.id) variants
                FROM functions f JOIN solutions s ON s.function_id=f.id
                JOIN occurrences o ON o.solution_id=s.id JOIN games g ON g.id=o.game_id
                WHERE (?='' OR f.id IN (SELECT s2.function_id FROM solutions s2
                    JOIN occurrences o2 ON o2.solution_id=s2.id JOIN games g2 ON g2.id=o2.game_id
                    WHERE o2.name LIKE ? OR g2.name LIKE ?)) GROUP BY f.id
                ORDER BY games DESC,f.id LIMIT 100''',(search,'%'+search+'%','%'+search+'%')).fetchall()
        # Include SQLite's live journal and shared-memory files; they can disappear
        # between samples when another connection checkpoints or closes.
        disk_bytes=0
        for suffix in ('','-wal','-shm'):
            try:disk_bytes+=Path(str(self.path)+suffix).stat().st_size
            except FileNotFoundError:pass
        return {**counts,'disk_bytes':disk_bytes,'items':[dict(r) for r in rows]}

    def candidates(self, processor, big, fingerprint, size, exclude=()):
        with self.connect() as db:
            rows=db.execute('''SELECT s.id,s.compiler,src.text source,h.text header,p.text print,y.text symbols,
                o.name,o.mode,g.name game FROM functions f JOIN solutions s ON s.function_id=f.id
                JOIN occurrences o ON o.solution_id=s.id JOIN games g ON g.id=o.game_id
                JOIN texts src ON src.hash=s.source JOIN texts h ON h.hash=s.header
                JOIN texts p ON p.hash=o.print JOIN texts y ON y.hash=o.symbols
                WHERE f.arch=? AND f.big=? AND f.fingerprint=? AND f.size=?
                AND g.id NOT IN (SELECT value FROM json_each(?))
                GROUP BY s.id ORDER BY o.seen DESC LIMIT 16''',(processor,int(big),fingerprint,size,encoded(list(exclude)))).fetchall()
            return [dict(r) for r in rows]

    def reuse(self, twins, proj, job=None):
        from ..rom.symbols import Symbol, SymbolTable
        from .free_candidates import exhausted
        from .shared_reuse import machine
        from .twins import ACROSS_MIN_BYTES, Print, _declared, apply, renames, rom_renames
        net=self.net
        fork=bool(net.fork_slug(proj['slug']))
        if proj['slug'] in net.links:return 0
        bounded=job is not None and hasattr(job,'reuse_limit')
        processor,big=machine(net,proj)
        agent,_=net.local_agent('autopilot')
        # Live projects already have the richer existing donor path. The library supplies
        # independent closed-project evidence without duplicating those attempts.
        active={self.game_key(p) for p in net.store.all('SELECT * FROM projects')}
        prints=twins.prints(proj);matched=0
        for b in net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open'",proj['id']):
            if job is not None and (job.stop.is_set() or (bounded and exhausted(job))):break
            pb=prints.get(b['id'])
            if not pb or pb.asm or pb.size<ACROSS_MIN_BYTES or net.store.one('SELECT 1 FROM claims WHERE function_id=?',b['id']):continue
            for item in self.candidates(processor,big,pb.key,pb.size,active):
                if job is not None and (job.stop.is_set() or (bounded and exhausted(job))):return matched
                pa=Print(**json.loads(item['print']))
                symbols=SymbolTable([Symbol(**s) for s in json.loads(item['symbols'])])
                names=(rom_renames(symbols,item['source'],pa,pb,pa.name,b['name'],net.symbols(proj['id']))
                       if item['mode']=='rom' and not fork else renames(pa,pb,pa.name,pb.name or b['name']))
                if names is None:continue
                header=(net._context(proj['id'],b['unit']) or {'header':''})['header']
                source=_declared(item['header'],header,names,pa.name)+apply(item['source'],names)
                if twins._missed(b['id'],source):continue
                if job is not None:
                    job.cross_candidates+=1;job.cross_tried+=1
                    if bounded:job.reuse_attempts+=1
                from .cross_game_progress import report
                report(job, 'Compiling candidate', game=item['game'], function=b['name'], donor=item['name'])
                outcome,why=twins._attempt_twin(agent,proj,{'name':item['name']},b,source,
                                              origin=' in '+item['game']+' (local match library)',theirs=fork)
                if outcome=='miss':
                    import time
                    with net.store.tx() as db:
                        db.execute('INSERT OR REPLACE INTO twin_misses(function_id,source_hash,project_id,at,why) VALUES(?,?,?,?,?)',
                                   (b['id'],twins._miss_key(source),proj['id'],time.time(),why))
                if outcome=='match':
                    matched+=1
                    if job is not None:
                        job.cross_matched+=1
                        if bounded:job.reuse_matched+=1;job.reuse_bytes+=b['size']
                    break
        return matched
