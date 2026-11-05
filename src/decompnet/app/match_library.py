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
ARCHIVE_BATCH = 256
ARCHIVE_VERSION = 1  # Bump when the archived representation changes, not for unrelated app updates.
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
CREATE TABLE IF NOT EXISTS startup_scans(game_id TEXT PRIMARY KEY, stamp TEXT NOT NULL);
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

    def _inputs(self, proj):
        """Share project reads within one bounded batch; the next batch observes live changes."""
        from .shared_reuse import machine
        fork = bool(self.net.fork_slug(proj['slug']))
        symbols = {} if fork else self.net.symbols(proj['id']).by_name
        # Preserve the existing archive's symbol order without scanning the inventory
        # per function. A changed symbol revision or replacement table rebuilds the index.
        order = self.net.store.read_cache.get(('library-symbol-order',proj['id']),
            (self.net.store.read_version('scope_symbols',proj['id']),id(symbols)),
            lambda:(symbols,{name:index for index,name in enumerate(symbols)}))[1] if not fork else {}
        return {'fork':fork, 'cfg':self.net.cfg(proj),
                'prints':self.net.twins.prints(proj), 'machine':machine(self.net, proj),
                'game':self.game_key(proj), 'headers':{}, 'reserved':{}, 'fork_project':None, 'file_compilers':{},
                'symbols':symbols, 'symbol_order':order,
                'proofs':bool(self.net.store.one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"))}

    def _snapshot(self, proj, fid, *, inputs=None, fn=None, proof=None):
        from ..project import compiler_for
        net = self.net
        if inputs is None:
            proj = net.store.one('SELECT * FROM projects WHERE id=?',proj['id']) or proj
            fn = net.store.one("SELECT * FROM functions WHERE id=? AND project_id=? AND status='matched'", fid, proj['id'])
            if not fn:return None
            inputs = self._inputs(proj)
            if inputs['proofs']:
                proof = net.store.one('SELECT source,header,details FROM verified_matches WHERE function_id=?',fid)
        p = inputs['prints'].get(fid)
        source = fn['matched_source']
        fork = inputs['fork']
        # Most matches have a saved original compiler; do not search every configured
        # function profile for a fallback which that evidence will immediately replace.
        if fork:
            from .twins import _file_compiler
            if inputs['fork_project'] is None:
                inputs['fork_project'] = net.forks.get(proj['slug'])[1]
            fp = inputs['fork_project']
            source = source or net.forks._current_source(fp, fn)
        if not source:
            return None
        compiler = (json.loads(proof['details']).get('compiler') if proof and proof['source']==source else None)
        if not compiler:
            if fork:
                unit = fn['unit'] or ''
                if unit not in inputs['file_compilers']:
                    inputs['file_compilers'][unit] = _file_compiler(net.console(proj), fp, fn)
                compiler = {'toolchain':inputs['file_compilers'][unit], 'flags':[]}
            else:
                compiler = compiler_for(inputs['cfg'], fn['vram'], fn)
        if proof and proof['source']==source:
            header = proof['header']
        else:
            unit = fn['unit'] or ''
            if unit not in inputs['headers']:
                inputs['headers'][unit] = (net._context(proj['id'], unit) or {'header':''})['header']
            header = inputs['headers'][unit]
        # Store only the named symbols needed to relocate this C, not the game's full symbol table.
        words = set(re.findall(r'\b[A-Za-z_]\w*\b', source))
        names = [name for name in words if name in inputs['symbols']]
        symbols = [asdict(inputs['symbols'][name]) for name in sorted(names,key=inputs['symbol_order'].__getitem__)]
        processor, big = inputs['machine']
        # Same source copied under a generated function name shares its stored body.
        if p is None:
            from .twins import Print
            p=Print('source-only:'+hashlib.sha256(source.encode()).hexdigest(),[],fn['size'] or 0)
        own_name = p.name or fn['name']
        token='__library_function'
        if header not in inputs['reserved']:
            inputs['reserved'][header] = set(re.findall(r'\b__library_function_*\b', header))
        while token!=own_name and (token in words or token in inputs['reserved'][header]):token+='_'
        mapping = {own_name:token} if re.fullmatch(r'[A-Za-z_]\w*', own_name) else {}
        if mapping:
            source = re.sub(r'\b[A-Za-z_]\w*\b', lambda match: mapping.get(match[0], match[0]), source)
        p = asdict(p);p['name'] = mapping.get(own_name, own_name)
        for s in symbols:
            s['name'] = mapping.get(s['name'], s['name'])
        game = inputs['game']
        return {'arch':processor,'big':int(big),'fingerprint':p['key'],'size':p['size'],
                'source':source,'header':header,'compiler':encoded(compiler),
                'game':game,'game_name':proj['name'],'slug':proj['slug'],'name':fn['name'],
                'mode':'fork' if fork else 'rom','print':encoded(p),'symbols':encoded(symbols),'seen':fn['matched_at'] or 0}

    def write(self, rows):
        with self.connect() as db:
            texts, functions, solutions, games = {}, {}, {}, set()
            for item in rows:
                hashes = {}
                for kind in ('source','header','print','symbols'):
                    value = item[kind]
                    digest = texts.get(value)
                    if digest is None:
                        digest = hashlib.sha256(value.encode()).hexdigest()
                        db.execute('INSERT OR IGNORE INTO texts VALUES(?,?)',(digest,value))
                        texts[value] = digest
                    hashes[kind] = digest
                identity = tuple(item[k] for k in ('arch','big','fingerprint','size'))
                fid = functions.get(identity)
                if fid is None:
                    db.execute('INSERT OR IGNORE INTO functions(arch,big,fingerprint,size) VALUES(?,?,?,?)',identity)
                    fid = db.execute('SELECT id FROM functions WHERE arch=? AND big=? AND fingerprint=? AND size=?',
                                     identity).fetchone()['id']
                    functions[identity] = fid
                variant = (fid,hashes['source'],hashes['header'],item['compiler'])
                sid = solutions.get(variant)
                if sid is None:
                    db.execute('INSERT OR IGNORE INTO solutions(function_id,source,header,compiler) VALUES(?,?,?,?)',variant)
                    sid = db.execute('SELECT id FROM solutions WHERE function_id=? AND source=? AND header=? AND compiler=?',
                                     variant).fetchone()['id']
                    solutions[variant] = sid
                game = (item['game'],item['game_name'],item['slug'])
                if game not in games:
                    db.execute("""INSERT INTO games VALUES(?,?,?) ON CONFLICT(id) DO UPDATE
                        SET name=excluded.name,slug=excluded.slug
                        WHERE name!=excluded.name OR slug!=excluded.slug""",game)
                    games.add(game)
                # Retrying an interrupted scan should not rewrite unchanged memberships.
                db.execute("""INSERT INTO occurrences VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(game_id,solution_id,name,print,symbols) DO UPDATE
                    SET mode=excluded.mode,seen=excluded.seen WHERE mode!=excluded.mode OR seen!=excluded.seen""",
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
        for start in range(0,len(rows),ARCHIVE_BATCH):
            from .maintenance import checkpoint
            checkpoint()
            if log:log.check_cancel()
            # Narrow reads avoid loading target bytes and unfinished candidate/history
            # data. Never hold a main-database transaction while writing the library.
            with self.net.progress.view():
                current=self.net.store.one('SELECT * FROM projects WHERE id=?',proj['id']) or proj
                inputs=self._inputs(current)
                ids=[row['id'] for row in rows[start:start+ARCHIVE_BATCH]]
                marks=','.join('?' for _ in ids)
                fields='id,name,unit,vram,rom_offset,size,matched_source,matched_at,meta'
                functions={row['id']:row for row in self.net.store.all(
                    f"SELECT {fields} FROM functions WHERE project_id=? AND status='matched' AND id IN ({marks})",
                    proj['id'],*ids)}
                proofs={row['function_id']:row for row in self.net.store.all(
                    f'SELECT function_id,source,header,details FROM verified_matches WHERE function_id IN ({marks})',
                    *ids)} if inputs['proofs'] else {}
                batch=[]
                for offset,row in enumerate(rows[start:start+ARCHIVE_BATCH]):
                    checkpoint()
                    report(start+offset,'Reading verified matches',row['name'])
                    fn=functions.get(row['id'])
                    if fn is not None:
                        item=self._snapshot(current,row['id'],inputs=inputs,fn=fn,proof=proofs.get(row['id']))
                        if item:batch.append(item)
            if batch:
                report(start+len(ids)-1,'Writing matches to library',rows[start+len(ids)-1]['name'])
                self.write(batch)
        report(len(rows),'Game indexed')
        return len(rows)

    def _startup_stamp(self, proj):
        if self.net.cfg(proj).get('kind') != 'rom':return None
        # Imported objects can change outside SQLite; keep their full file-based scan.
        revisions = [self.net.store.read_version(kind, proj['id']) for kind in
                     ('inputs','scope_context','scope_symbols','matched_evidence','fingerprints')]
        return encoded([ARCHIVE_VERSION,str(self.net.store.path),proj['id'],proj['slug'],revisions,proj['config'],proj['root'],proj['name']])

    def backfill(self, log=None, *, only_changed=False):
        failures=[]
        if log:log.progress({'phase':'Counting verified matches','done':0,'total':0})
        projects=[]
        for proj in self.net.store.all('SELECT * FROM projects ORDER BY id'):
            if log:log.check_cancel()
            stamp = self._startup_stamp(proj) if only_changed else None
            if stamp is not None:
                with self.connect() as db:
                    previous = db.execute('SELECT stamp FROM startup_scans WHERE game_id=?',(self.game_key(proj),)).fetchone()
                if previous and previous['stamp'] == stamp:
                    if log:log(f"{proj['name']}: verified-match archive is unchanged")
                    continue
            rows=self.net.store.all("SELECT id,name FROM functions WHERE project_id=? AND status='matched'",proj['id'])
            projects.append((proj,rows,stamp))
        total=sum(len(rows) for _,rows,_ in projects)
        completed=0
        for index,(proj,rows,stamp) in enumerate(projects,1):
            from .maintenance import checkpoint
            checkpoint()
            if log:log.check_cancel()
            game_total=len(rows)
            def progress(value, game_total=game_total, game_index=index, offset=completed):
                log.progress({**value,'game_done':value['done'],'game_total':game_total,
                              'game_index':game_index,'games_total':len(projects),
                              'done':offset+value['done'],'total':total})
            try:
                self.archive_project(proj,log,rows=rows,progress=progress)
                if log:log.check_cancel()
                if stamp is not None and stamp == self._startup_stamp(self.net.project_row(proj['slug'])):
                    with self.connect() as db:
                        db.execute('INSERT OR REPLACE INTO startup_scans VALUES(?,?)',(self.game_key(proj),stamp))
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
                o.name,o.mode,g.name game,g.slug source_slug FROM functions f JOIN solutions s ON s.function_id=f.id
                JOIN occurrences o ON o.solution_id=s.id JOIN games g ON g.id=o.game_id
                JOIN texts src ON src.hash=s.source JOIN texts h ON h.hash=s.header
                JOIN texts p ON p.hash=o.print JOIN texts y ON y.hash=o.symbols
                WHERE f.arch=? AND f.big=? AND f.fingerprint=? AND f.size=?
                AND g.id NOT IN (SELECT value FROM json_each(?))
                GROUP BY s.id ORDER BY o.seen DESC LIMIT 16''',(processor,int(big),fingerprint,size,encoded(list(exclude)))).fetchall()
            return [dict(r) for r in rows]

    def available(self, processor, big, fingerprints, exclude=()):
        """Metadata-only shortlist; source/header blobs are fetched only for actual shared code."""
        if not fingerprints:return {}
        with self.connect() as db:
            rows=db.execute("""SELECT f.fingerprint,f.size,COUNT(DISTINCT o.game_id) games
                FROM functions f JOIN solutions s ON s.function_id=f.id
                JOIN occurrences o ON o.solution_id=s.id
                WHERE f.arch=? AND f.big=? AND f.fingerprint IN (SELECT value FROM json_each(?))
                AND o.game_id NOT IN (SELECT value FROM json_each(?)) GROUP BY f.id""",
                (processor,int(big),encoded(list(fingerprints)),encoded(list(exclude)))).fetchall()
        return {(r['fingerprint'],r['size']):r['games'] for r in rows}

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
        from ..project import compiler_for
        from .cross_game_progress import report
        # Archived evidence from open games is just as useful as closed-game evidence.
        # Exclude only this game; every candidate still passes the destination judge.
        exclude={self.game_key(proj)}
        report(job,'Reading target fingerprints',game=proj['name'])
        prints=twins.prints(proj);matched=0
        rows=net.store.all("SELECT id,name FROM functions WHERE project_id=? AND status='open'",proj['id'])
        targets=[r for r in rows if (p:=prints.get(r['id'])) and not p.asm and p.size>=ACROSS_MIN_BYTES]
        report(job,'Looking up indexed matches',done=0,total=len(targets))
        available=self.available(processor,big,{prints[r['id']].key for r in targets},exclude)
        targets=[r for r in targets if (prints[r['id']].key,prints[r['id']].size) in available]
        targets.sort(key=lambda r:(-available[(prints[r['id']].key,prints[r['id']].size)],prints[r['id']].size,r['id']))
        if job is not None:job.cross_indexed=len(targets)
        for index,row in enumerate(targets):
            report(job,'Checking indexed matches',function=row['name'],done=index,total=len(targets))
            b=net.store.one("SELECT * FROM functions WHERE id=? AND status='open'",row['id'])
            if b is None:continue
            if job is not None and (job.stop.is_set() or (bounded and exhausted(job))):break
            pb=prints.get(b['id'])
            if not pb or pb.asm or pb.size<ACROSS_MIN_BYTES or net.store.one('SELECT 1 FROM claims WHERE function_id=?',b['id']):continue
            candidates=self.candidates(processor,big,pb.key,pb.size,exclude)
            if not fork:
                own=compiler_for(net.cfg(proj),b['vram'],b)
                own_flags=own['flags'].split() if isinstance(own['flags'],str) else own['flags']
                def rank(item,toolchain=own['toolchain'],flags_used=own_flags):
                    profile=json.loads(item['compiler'])
                    flags=profile.get('flags',[])
                    if isinstance(flags,str):flags=flags.split()
                    return profile.get('toolchain')!=toolchain,flags!=flags_used
                candidates.sort(key=rank)
            for item in candidates:
                if job is not None and (job.stop.is_set() or (bounded and exhausted(job))):return matched
                pa=Print(**json.loads(item['print']))
                symbols=SymbolTable([Symbol(**s) for s in json.loads(item['symbols'])])
                names=(rom_renames(symbols,item['source'],pa,pb,pa.name,b['name'],net.symbols(proj['id']))
                       if item['mode']=='rom' and not fork else renames(pa,pb,pa.name,pb.name or b['name']))
                if names is None:continue
                header=(net._context(proj['id'],b['unit']) or {'header':''})['header']
                source=_declared(item['header'],header,names,pa.name)+apply(item['source'],names)
                if twins._missed(b['id'],source):
                    if job is not None:job.cross_cached=getattr(job,'cross_cached',0)+1
                    continue
                if job is not None:
                    job.cross_candidates+=1;job.cross_tried+=1
                    if bounded:job.reuse_attempts+=1
                from .cross_game_progress import report
                report(job, 'Compiling candidate', game=item['game'], function=b['name'], donor=item['name'])
                live=net.store.one("SELECT f.name,p.slug,p.name AS game FROM cross_game_index i JOIN functions f ON f.id=i.function_id JOIN projects p ON p.id=i.project_id WHERE p.slug=? AND i.fingerprint=? AND i.size=? AND f.status='matched' ORDER BY f.name=? DESC LIMIT 1",item['source_slug'],pb.key,pb.size,item['name'])
                donor_name=live['name'] if live else item['name']
                outcome,why=twins._attempt_twin(agent,proj,{'name':donor_name},b,source,
                                              origin=' in '+item['game']+' (local match library)',theirs=fork)
                import time
                with net.store.tx() as db:
                    db.execute('INSERT INTO library_transfers(project_id,function_id,source_game,source_name,source_slug,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?,?)',
                               (proj['id'],b['id'],live['game'] if live else item['game'],donor_name,live['slug'] if live else '',outcome,why[:2000],time.time()))
                if outcome=='miss':
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
