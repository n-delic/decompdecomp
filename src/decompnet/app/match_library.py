"""Private verified-function index. Source and headers stay in their donor projects.

Fingerprints find candidates, never grant a match. Every transfer uses the destination's
ordinary compiler and byte/object judge. No ROM or object bytes are stored here.
"""
import hashlib
import json
import logging
import re
import sqlite3
import zlib
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from pathlib import Path

from ..network import NetworkError

log = logging.getLogger(__name__)
ARCHIVE_BATCH = 256
ARCHIVE_VERSION = 3  # Bump when the archived representation changes, not for unrelated app updates.
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
CREATE INDEX IF NOT EXISTS occurrences_print ON occurrences(print);
CREATE INDEX IF NOT EXISTS occurrences_symbols ON occurrences(symbols);
CREATE TABLE IF NOT EXISTS preferred_names(function_id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS locations(
 function_id INTEGER NOT NULL, game_id TEXT NOT NULL, name TEXT NOT NULL,
 vram INTEGER, rom_offset INTEGER, unit TEXT, source_path TEXT,
 UNIQUE(function_id,game_id,name,vram,unit));
CREATE TABLE IF NOT EXISTS startup_scans(game_id TEXT PRIMARY KEY, stamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS archive_receipts(game_id TEXT NOT NULL,function_id INTEGER NOT NULL,
 stamp TEXT NOT NULL,PRIMARY KEY(game_id,function_id));
'''


def packed_text(value):
    raw=value.encode('utf-8')
    return zlib.compress(raw) if len(raw)>=512 else value


def read_text(key, value):
    if not isinstance(value,bytes):return value
    text=zlib.decompress(value).decode('utf-8')
    if hashlib.sha256(text.encode('utf-8')).hexdigest()!=key:
        raise ValueError('Stored match-library text checksum failed.')
    return text


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


class Library:
    def __init__(self, net):
        self.net = net
        self.path = Path(net.store.path).parent / 'match-library.sqlite3'
        from ..writer_gate import WriterGate
        self.writer_gate=WriterGate(self.path)
        # No connection is shared between threads or held open by idle projects.
        with self.connect(write=True) as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self,*,write=False,wait=None):
        from ..sqlite_diagnostics import report
        try:
            db = sqlite3.connect(self.path, timeout=30)
        except sqlite3.Error as error:
            report(error, self.path, 'connect')
            raise
        db.row_factory = sqlite3.Row
        db.create_function('library_text',2,read_text,deterministic=True)
        try:
            with self.writer_gate.hold(wait=wait) if write else nullcontext(), db:
                yield db
        except sqlite3.Error as error:
            report(error, self.path, 'execute')
            raise
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
        order = self.net.project_store(proj['id']).read_cache.get(('library-symbol-order',proj['id']),
            (self.net.project_store(proj['id']).read_version('scope_symbols',proj['id']),id(symbols)),
            lambda:(symbols,{name:index for index,name in enumerate(symbols)}))[1] if not fork else {}
        return {'fork':fork, 'cfg':self.net.cfg(proj),
                'prints':self.net.twins.prints(proj), 'machine':machine(self.net, proj),
                'game':self.game_key(proj), 'headers':{}, 'reserved':{}, 'fork_project':None, 'file_compilers':{},
                'symbols':symbols, 'symbol_order':order,
                'proofs':bool(self.net.project_store(proj['id']).one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"))}

    def _snapshot(self, proj, fid, *, inputs=None, fn=None, proof=None):
        from ..project import compiler_for
        net = self.net
        if inputs is None:
            proj = net.project_store(proj['id']).one('SELECT * FROM projects WHERE id=?',proj['id']) or proj
            fn = net.project_store(proj['id']).one("SELECT * FROM functions WHERE id=? AND project_id=? AND status='matched'", fid, proj['id'])
            if not fn:return None
            inputs = self._inputs(proj)
            if inputs['proofs']:
                proof = net.project_store(proj['id']).one('SELECT source,header,details FROM verified_matches WHERE function_id=?',fid)
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
                'location':{k:dict(fn).get(k) for k in ('vram','rom_offset','unit','source_path')},
                'mode':'fork' if fork else 'rom','print':encoded(p),'symbols':encoded(symbols),'seen':fn['matched_at'] or 0}

    def write(self, rows):
        rows=list(rows)
        # Hash and compress before admission; only SQL publication holds the writer.
        prepared={}
        for item in rows:
            for kind in ('source','header','print','symbols'):
                value=item[kind]
                if value not in prepared:prepared[value]=(hashlib.sha256(value.encode()).hexdigest(),None)
                if kind in ('print','symbols') and prepared[value][1] is None:
                    prepared[value]=(prepared[value][0],packed_text(value))
        with self.connect(write=True) as db:
            texts, functions, solutions, games = {}, {}, {}, set()
            stored=set()
            for item in rows:
                hashes = {}
                for kind in ('source','header','print','symbols'):
                    value = item[kind]
                    digest = texts.get(value)
                    if digest is None:
                        digest,_=prepared[value]
                        texts[value] = digest
                    if kind in ('print','symbols') and digest not in stored:
                        db.execute('INSERT OR IGNORE INTO texts VALUES(?,?)',(digest,prepared[value][1]))
                        stored.add(digest)
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
                loc = item.get('location', {})
                db.execute("""INSERT INTO locations VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(function_id,game_id,name,vram,unit) DO UPDATE
                    SET rom_offset=excluded.rom_offset,source_path=excluded.source_path
                    WHERE rom_offset IS NOT excluded.rom_offset OR source_path!=excluded.source_path""",
                           (fid,item['game'],item['name'],loc.get('vram') if loc.get('vram') is not None else -1,
                            loc.get('rom_offset'),loc.get('unit') or '',loc.get('source_path') or ''))
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
                self.net.project_store(proj['id']).after_commit(publish)
        except Exception:  # archiving must never turn a committed exact verdict into a judge failure
            log.exception('Could not snapshot verified match for the local library')

    def archive_project(self, proj, log=None, *, rows=None, progress=None, incremental=False):
        if rows is None:
            rows=self.net.project_store(proj['id']).all("SELECT id,name FROM functions WHERE project_id=? AND status='matched'",proj['id'])
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
                current=self.net.project_store(proj['id']).one('SELECT * FROM projects WHERE id=?',proj['id']) or proj
                # Capture dependency revisions before reading inputs. A racing context edit
                # then makes this receipt stale instead of acknowledging old data as new.
                dependency = self._startup_stamp(current)
                if dependency is not None:
                    value=json.loads(dependency)
                    value[4][0]=None  # broad inputs also includes unrelated attempts and matches
                    value[4][3]=None  # matched_evidence is represented by each function/proof below
                    dependency=encoded(value)
                inputs=self._inputs(current)
                ids=[row['id'] for row in rows[start:start+ARCHIVE_BATCH]]
                marks=','.join('?' for _ in ids)
                from ..function_rows import selected
                fields=('id','name','unit','vram','rom_offset','size','matched_source','matched_at','meta')
                functions={row['id']:row for row in selected(self.net.project_store(proj['id']),
                    proj['id'],ids,columns=fields,matched_only=True)}
                proofs={row['function_id']:row for row in self.net.project_store(proj['id']).all(
                    f'SELECT function_id,source,header,details FROM verified_matches WHERE function_id IN ({marks})',
                    *ids)} if inputs['proofs'] else {}
                # Match-only changes need not tokenize, normalize and compress every old match.
                # Include all project dependencies except the match revision itself; a header,
                # symbol, compiler or fingerprint change still invalidates every receipt.
                with self.connect() as db:
                    receipts={r['function_id']:r['stamp'] for r in db.execute(
                        'SELECT function_id,stamp FROM archive_receipts WHERE game_id=? AND function_id IN ('+marks+')',
                        (inputs['game'],*ids))} if incremental and dependency else {}
                batch=[]; acknowledged=[]
                for offset,row in enumerate(rows[start:start+ARCHIVE_BATCH]):
                    checkpoint()
                    report(start+offset,'Reading verified matches',row['name'])
                    fn=functions.get(row['id'])
                    if fn is not None:
                        proof=proofs.get(row['id'])
                        stamp=hashlib.sha256(encoded([dependency,dict(fn),dict(proof) if proof else None]).encode()).hexdigest()
                        if incremental and dependency and receipts.get(row['id'])==stamp:continue
                        item=self._snapshot(current,row['id'],inputs=inputs,fn=fn,proof=proofs.get(row['id']))
                        if item:
                            batch.append(item)
                            if dependency:acknowledged.append((inputs['game'],row['id'],stamp))
            if batch:
                report(start+len(ids)-1,'Writing matches to library',rows[start+len(ids)-1]['name'])
                self.write(batch)
                # Publish receipts only after the archive write succeeds. Interrupted runs replay safely.
                if acknowledged:
                    with self.connect(write=True) as db:
                        db.executemany('INSERT OR REPLACE INTO archive_receipts VALUES(?,?,?)',acknowledged)
        report(len(rows),'Game indexed')
        return len(rows)

    def _startup_stamp(self, proj):
        if self.net.cfg(proj).get('kind') != 'rom':return None
        # Imported objects can change outside SQLite; keep their full file-based scan.
        revisions = [self.net.project_store(proj['id']).read_version(kind, proj['id']) for kind in
                     ('inputs','scope_context','scope_symbols','matched_evidence','fingerprints')]
        return encoded([ARCHIVE_VERSION,str(self.net.store.path),proj['id'],proj['slug'],revisions,proj['config'],proj['root'],proj['name']])

    def backfill(self, log=None, *, only_changed=False, slug=None):
        if slug is None:self.prune_payloads(log)
        failures=[]
        if log:log.progress({'phase':'Counting verified matches','done':0,'total':0})
        projects=[]
        for catalog in self.net.store.all('SELECT id FROM projects' + (' WHERE slug=?' if slug else '') + ' ORDER BY id',
                                          *((slug,) if slug else ())):
            proj = self.net.project_store(catalog['id']).one('SELECT * FROM projects WHERE id=?', catalog['id'])
            if proj is None:raise NetworkError('A project is missing from its database. Restore it before archiving.')
            if log:log.check_cancel()
            stamp = self._startup_stamp(proj) if only_changed else None
            if stamp is not None:
                with self.connect() as db:
                    previous = db.execute('SELECT stamp FROM startup_scans WHERE game_id=?',(self.game_key(proj),)).fetchone()
                if previous and previous['stamp'] == stamp:
                    if log:log(f"{proj['name']}: verified-match archive is unchanged")
                    continue
            rows=self.net.project_store(proj['id']).all("SELECT id,name FROM functions WHERE project_id=? AND status='matched'",proj['id'])
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
                self.archive_project(proj,log,rows=rows,progress=progress,incremental=only_changed)
                if log:log.check_cancel()
                if stamp is not None and stamp == self._startup_stamp(self.net.project_row(proj['slug'])):
                    with self.connect(write=True) as db:
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
            rows=db.execute('''SELECT f.id,f.arch,f.size,COALESCE(n.name,MIN(o.name)) name,COUNT(DISTINCT g.id) games,
                GROUP_CONCAT(DISTINCT g.name) game_names,COUNT(DISTINCT s.id) variants
                FROM functions f LEFT JOIN preferred_names n ON n.function_id=f.id
                JOIN solutions s ON s.function_id=f.id
                JOIN occurrences o ON o.solution_id=s.id JOIN games g ON g.id=o.game_id
                WHERE (?='' OR n.name LIKE ? OR f.id IN (SELECT s2.function_id FROM solutions s2
                    JOIN occurrences o2 ON o2.solution_id=s2.id JOIN games g2 ON g2.id=o2.game_id
                    WHERE o2.name LIKE ? OR g2.name LIKE ?)) GROUP BY f.id
                ORDER BY games DESC,f.id LIMIT 100''',(search,'%'+search+'%','%'+search+'%','%'+search+'%')).fetchall()
        # Include SQLite's live journal and shared-memory files; they can disappear
        # between samples when another connection checkpoints or closes.
        disk_bytes=0
        for suffix in ('','-wal','-shm'):
            try:disk_bytes+=Path(str(self.path)+suffix).stat().st_size
            except FileNotFoundError:pass
        return {**counts,'disk_bytes':disk_bytes,'items':[dict(r) for r in rows]}

    def detail(self, fid):
        """Historical aliases remain visible even when a project is no longer loaded."""
        with self.connect() as db:
            row=db.execute('SELECT f.*,n.name FROM functions f LEFT JOIN preferred_names n ON n.function_id=f.id WHERE f.id=?',
                           (fid,)).fetchone()
            if row is None:raise NetworkError('That library function no longer exists. Refresh the library.')
            item=dict(row)
            rows=db.execute("""SELECT DISTINCT g.id game_id,g.name game,g.slug,o.name,l.vram,l.rom_offset,l.unit,l.source_path
                FROM solutions s JOIN occurrences o ON o.solution_id=s.id JOIN games g ON g.id=o.game_id
                LEFT JOIN locations l ON l.function_id=s.function_id AND l.game_id=o.game_id AND l.name=o.name
                WHERE s.function_id=? ORDER BY g.name,o.name,l.vram""",(fid,)).fetchall()
        project_rows=[self.net.project_store(row['id']).one('SELECT * FROM projects WHERE id=?', row['id'])
                      for row in self.net.store.all('SELECT id FROM projects')]
        if any(row is None for row in project_rows):raise NetworkError('A project is missing from its database. Restore it before browsing the library.')
        projects={self.game_key(p):p for p in project_rows}
        occurrences=[];prints={}
        for row in rows:
            r=dict(row);proj=projects.get(r['game_id']);r['available']=False
            if r['vram']==-1:r['vram']=None
            if proj and Path(proj['root']).is_dir():
                fn=self.net.project_store(proj['id']).one('SELECT * FROM functions WHERE project_id=? AND name=?',proj['id'],r['name'])
                if fn and fn['status']!='matched':fn=None
                if fn:
                    if proj['id'] not in prints:prints[proj['id']]=self.net.twins.prints(proj)
                    proof=prints[proj['id']].get(fn['id'])
                    if proof is None or proof.key!=item['fingerprint'] or proof.size!=item['size']:
                        fn=None
                if fn:
                    r.update(available=True,slug=proj['slug'],function_id=fn['id'])
                    for key in ('vram','rom_offset','unit','source_path'):r[key]=dict(fn).get(key)
            occurrences.append(r)
        # A project may have renamed a symbol since the last archive scan. Add its
        # current locations by verified identity while retaining the historical aliases.
        available={r.get('function_id') for r in occurrences if r['available']}
        games={r['game_id'] for r in occurrences}
        for proj in project_rows:
            game=self.game_key(proj)
            if game not in games or not Path(proj['root']).is_dir():continue
            if proj['id'] not in prints:prints[proj['id']]=self.net.twins.prints(proj)
            ids=[key for key,p in prints[proj['id']].items()
                 if p.key==item['fingerprint'] and p.size==item['size'] and key not in available]
            for fn in self.net.project_store(proj['id']).all("SELECT * FROM functions WHERE project_id=? AND status='matched' AND id IN (SELECT value FROM json_each(?))",
                                         proj['id'],encoded(ids)):
                fn=dict(fn)
                occurrences.append({'game_id':game,'game':proj['name'],'slug':proj['slug'],'name':fn['name'],
                                    'function_id':fn['id'],'available':True,
                                    **{k:fn.get(k) for k in ('vram','rom_offset','unit','source_path')}})
        item['occurrences']=occurrences
        item['name']=item['name'] or min(r['name'] for r in occurrences)
        return item

    def set_name(self, fid, name):
        name=str(name).strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,199}',name):
            raise NetworkError('Choose a C identifier, up to 200 characters, for the default name.')
        with self.connect(write=True) as db:
            if not db.execute('SELECT 1 FROM functions WHERE id=?',(fid,)).fetchone():
                raise NetworkError('That library function no longer exists. Refresh the library.')
            db.execute('INSERT INTO preferred_names VALUES(?,?) ON CONFLICT(function_id) DO UPDATE SET name=excluded.name',
                       (fid,name))
        return name

    def apply_name(self, fid, name, log=None):
        """Use the ordinary checked rename, including room and imported-project rules."""
        name=self.set_name(fid,name)
        rows=self.detail(fid)['occurrences'];results=[];seen=set()
        agent,_=self.net.local_agent('you')
        for i,row in enumerate(rows):
            if log:
                log.check_cancel()
                log.progress({'phase':'Applying default name','game':row['game'],'function':row['name'],
                              'done':i,'total':len(rows)})
            key=(row['slug'] if row['available'] else row['game_id'],row['name'])
            if key in seen:continue
            seen.add(key)
            result={'game':row['game'],'name':row['name']}
            if not row['available']:result['message']='Project unavailable; default saved in the library only.'
            elif row['name']==name:result['message']='Already uses this name.'
            else:
                try:
                    message=self.net.rename_symbol(agent,row['slug'],row['name'],name,'Default name from the local match library')
                    result['message']=message
                    if not message.startswith('❌'):
                        self.capture(self.net.project_row(row['slug']),row['function_id'])
                except NetworkError as error:result['message']=str(error)
            results.append(result)
            if log:log(row['game']+': '+result['message'])
        if log:log.progress({'phase':'Naming complete','done':len(rows),'total':len(rows)})
        return {'results':results}

    def pack(self, log):
        """Bounded legacy conversion; readers stay available and content hashes stay stable."""
        from .maintenance import checkpoint
        done=self.prune_payloads(log)
        cursor=0
        while True:
            log.check_cancel()
            log.progress({'phase':'Packing match library','done':done})
            with self.connect() as db:
                sizes=db.execute("SELECT rowid AS id,length(CAST(text AS BLOB)) size FROM texts WHERE rowid>? AND typeof(text)='text' "
                                 "AND length(CAST(text AS BLOB))>=512 ORDER BY rowid LIMIT 50",(cursor,)).fetchall()
                ids=[];used=0
                for row in sizes:
                    if ids and used+row['size']>2*1024*1024:break
                    ids.append(row['id']);used+=row['size']
                rows=db.execute('SELECT rowid AS id,hash,text FROM texts WHERE rowid IN (SELECT value FROM json_each(?)) '
                                "AND typeof(text)='text' ORDER BY rowid",(encoded(ids),)).fetchall()
            if not rows:break
            # Compression happens outside the library's writer admission/transaction.
            values=[]
            for row in rows:
                log.check_cancel();cursor=row['id']
                values.append((packed_text(row['text']),row['hash'],row['text']))
            with self.connect(write=True,wait=log.check_cancel) as db:
                changed=db.executemany('UPDATE texts SET text=? WHERE hash=? AND text=?',values).rowcount
            done+=changed
            checkpoint()
        return done

    def candidates(self, processor, big, fingerprint, size, exclude=()):
        """Resolve a compact reference only when its donor is still locally available.

        Never fall back to historical library source. Names/addresses locate the function,
        and its current matched fingerprint must still agree before loading its inputs.
        """
        with self.connect() as db:
            refs=db.execute("""SELECT o.name,g.id game_id,g.slug,l.vram,l.rom_offset,l.unit
                FROM functions f JOIN solutions s ON s.function_id=f.id
                JOIN occurrences o ON o.solution_id=s.id JOIN games g ON g.id=o.game_id
                JOIN locations l ON l.function_id=f.id AND l.game_id=g.id AND l.name=o.name
                WHERE f.arch=? AND f.big=? AND f.fingerprint=? AND f.size=?
                AND g.id NOT IN (SELECT value FROM json_each(?))
                GROUP BY g.id,l.name,l.vram,l.rom_offset,l.unit ORDER BY MAX(o.seen) DESC""",
                (processor,int(big),fingerprint,size,encoded(list(exclude)))).fetchall()
        results=[];seen=set()
        with self.net.progress.view():
            for ref in refs:
                donor=self.net.store.one('SELECT id FROM projects WHERE slug=?',ref['slug'])
                if donor is None:continue
                store=self.net.project_store(donor['id'])
                proj=store.one('SELECT * FROM projects WHERE id=?',donor['id'])
                if proj is None or self.game_key(proj)!=ref['game_id'] or not Path(proj['root']).is_dir():continue
                # ROM offset disambiguates overlays; unit disambiguates imported objects.
                rows=store.all("SELECT id,name FROM functions WHERE project_id=? AND status='matched' "
                               "AND vram IS ? AND rom_offset IS ? AND COALESCE(unit,'')=? ORDER BY name=? DESC",
                               donor['id'],None if ref['vram']==-1 else ref['vram'],ref['rom_offset'],ref['unit'],ref['name'])
                if len(rows)!=1:rows=[r for r in rows if r['name']==ref['name']]
                if len(rows)!=1:continue
                fn=rows[0]
                key=(donor['id'],fn['id'])
                if key in seen:continue
                seen.add(key)
                item=self.snapshot(proj,fn['id'])
                if item is None or item['arch']!=processor or item['big']!=int(big) or item['fingerprint']!=fingerprint or item['size']!=size:continue
                results.append({**item,'id':fn['id'],'game':proj['name'],'source_slug':proj['slug']})
                if len(results)>=16:break
        return results

    def prune_payloads(self, log=None):
        """Remove legacy source/header copies in bounded, cancellable transactions.

        Keep hashes and donor references. Freed pages are reused; explicit database
        compaction returns them to disk without blocking ordinary startup on VACUUM.
        """
        from .maintenance import checkpoint
        done=0;cursor=0
        while True:
            if log:log.check_cancel()
            with self.connect(write=True,wait=log.check_cancel if log else None) as db:
                rows=db.execute("SELECT rowid,hash FROM texts WHERE rowid>? AND hash NOT IN (SELECT print FROM occurrences) "
                                "AND hash NOT IN (SELECT symbols FROM occurrences) ORDER BY rowid LIMIT 256",(cursor,)).fetchall()
                if not rows:break
                cursor=rows[-1]['rowid']
                db.executemany('DELETE FROM texts WHERE hash=?',[(r['hash'],) for r in rows])
            done+=len(rows)
            if log:log.progress({'phase':'Removing legacy library source copies','done':done})
            checkpoint()
        return done

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
        rows=net.project_store(proj['id']).all("SELECT id,name FROM functions WHERE project_id=? AND status='open'",proj['id'])
        targets=[r for r in rows if (p:=prints.get(r['id'])) and not p.asm and p.size>=ACROSS_MIN_BYTES]
        report(job,'Looking up indexed matches',done=0,total=len(targets))
        available=self.available(processor,big,{prints[r['id']].key for r in targets},exclude)
        targets=[r for r in targets if (prints[r['id']].key,prints[r['id']].size) in available]
        targets.sort(key=lambda r:(-available[(prints[r['id']].key,prints[r['id']].size)],prints[r['id']].size,r['id']))
        if job is not None:job.cross_indexed=len(targets)
        for index,row in enumerate(targets):
            report(job,'Checking indexed matches',function=row['name'],done=index,total=len(targets))
            b=net.project_store(proj['id']).one("SELECT * FROM functions WHERE id=? AND status='open'",row['id'])
            if b is None:continue
            if job is not None and (job.stop.is_set() or (bounded and exhausted(job))):break
            pb=prints.get(b['id'])
            if not pb or pb.asm or pb.size<ACROSS_MIN_BYTES or net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=?',b['id']):continue
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
                donor=net.store.one('SELECT id FROM projects WHERE slug=?',item['source_slug'])
                live=net.project_store(donor['id']).one("SELECT f.name,p.slug,p.name AS game FROM cross_game_index i JOIN functions f ON f.id=i.function_id JOIN projects p ON p.id=i.project_id WHERE p.slug=? AND i.fingerprint=? AND i.size=? AND f.status='matched' ORDER BY f.name=? DESC LIMIT 1",item['source_slug'],pb.key,pb.size,item['name']) if donor else None
                donor_name=live['name'] if live else item['name']
                outcome,why=twins._attempt_twin(agent,proj,{'name':donor_name},b,source,
                                              origin=' in '+item['game']+' (local match library)',theirs=fork)
                import time
                with net.project_store(proj['id']).tx() as db:
                    db.execute('INSERT INTO library_transfers(project_id,function_id,source_game,source_name,source_slug,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?,?)',
                               (proj['id'],b['id'],live['game'] if live else item['game'],donor_name,live['slug'] if live else '',outcome,why[:2000],time.time()))
                if outcome=='miss':
                    with net.project_store(proj['id']).tx() as db:
                        db.execute('INSERT OR REPLACE INTO twin_misses(function_id,source_hash,project_id,at,why) VALUES(?,?,?,?,?)',
                                   (b['id'],twins._miss_key(source),proj['id'],time.time(),why))
                if outcome=='match':
                    matched+=1
                    if job is not None:
                        job.cross_matched+=1
                        if bounded:job.reuse_matched+=1;job.reuse_bytes+=b['size']
                    break
        return matched
