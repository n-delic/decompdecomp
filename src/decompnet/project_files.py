"""Strict catalog-to-project file registry and recoverable project publication."""
import json
import threading
from contextlib import closing, contextmanager
from pathlib import Path

from .project_migration import save, state
from .store import CorruptDatabaseError, Store

PENDING = 'project-registration:'


class Files:
    def __init__(self,net):
        self.net,self.catalog = net,net.store
        self.lock = threading.RLock()
        self.opened = {}
        from .writer_gate import WriterGate
        self.publication = WriterGate(str(self.catalog.path)+'.project-publication')
        self.recover()

    def path(self,pid,relative):
        expected = f'project-databases/{pid}.sqlite3'
        if relative != expected:raise ValueError('Invalid project database location. Restore a consistent catalog backup.')
        path = Path(self.catalog.path).parent/relative
        if path.is_symlink() or path.parent.is_symlink():raise ValueError('Project database locations cannot be symbolic links.')
        return path

    @contextmanager
    def catalog_write(self):
        with self.catalog.tx() as db:
            previous = self.catalog._migration_admin
            self.catalog._migration_admin = True
            try:yield db
            finally:self.catalog._migration_admin = previous

    def get(self,project):
        row = self.catalog.one('SELECT id,slug FROM projects WHERE '+('id=?' if type(project) is int else 'slug=?'),project)
        if row is None:
            from .network import NetworkError
            raise NetworkError('This project is not in the catalog. Reopen an existing project or restore its database backup.')
        with self.lock:cached = self.opened.get(row['id'])
        if cached is not None:return cached
        journal = state(self.catalog)
        relative = journal['projects'].get(str(row['id'])) if journal and journal['phase']=='ready' else None
        if relative is None:raise ValueError('Project database publication is incomplete. Finish the database upgrade before opening this project.')
        path = self.path(row['id'],relative)
        if not path.is_file():raise ValueError('The project database is missing: '+str(path)+'. Restore it from a consistent backup.')
        if not Path(str(path)+'.transcripts.sqlite3').is_file():
            raise ValueError('The project transcript database is missing. Restore it from a consistent backup.')
        # Do not hold the registry mutex while waiting for a SQLite writer.
        try:
            store = Store(path,project_namespace=row['id'],repair_archive=self.catalog.conn.repair_archive,require_existing=True)
        except CorruptDatabaseError as error:
            raise CorruptDatabaseError(path,project=row['slug']) from error
        if not store.one('SELECT 1 FROM projects WHERE id=? AND slug=?',row['id'],row['slug']):
            store.conn.close()
            raise ValueError('The project database belongs to a different project. Restore a consistent backup.')
        if store.one("SELECT 1 FROM settings WHERE key='project-storage-removed'"):
            store.conn.close()
            raise ValueError('This project database was removed. Restore a consistent backup.')
        with self.lock:existing = self.opened.setdefault(row['id'],store)
        if existing is not store:store.conn.close()
        return existing

    def _publish(self,pid,values,relative):
        with self.catalog_write() as db:
            if not db.execute('SELECT 1 FROM settings WHERE key=?',(PENDING+str(pid),)).fetchone():
                existing = db.execute('SELECT slug FROM projects WHERE id=?',(pid,)).fetchone()
                if existing and existing[0]==values[0] and state(self.catalog)['projects'].get(str(pid))==relative:return
                raise ValueError('Project registration journal is missing. Restore the catalog backup.')
            db.execute('INSERT INTO projects(id,slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?,?)',
                       (pid,*values))
            journal = state(self.catalog)
            journal['projects'][str(pid)] = relative
            save(self.catalog,journal)
            db.execute('DELETE FROM settings WHERE key=?',(PENDING+str(pid),))

    @contextmanager
    def create(self,values):
        with self.publication.hold():
            with self._create(values) as result:yield result

    @contextmanager
    def _create(self,values):
        from .shard_ids import bounds
        # Reserve a durable, never-reused identity without publishing an empty
        # project. The initial project transaction never holds a catalog writer.
        with self.catalog_write() as db:
            if db.execute('SELECT 1 FROM projects WHERE slug=?',(values[0],)).fetchone():
                raise ValueError('A project with this name already exists.')
            for row in db.execute('SELECT value FROM settings WHERE key LIKE ?',(PENDING+'%',)):
                if json.loads(row[0])['values'][0]==values[0]:raise ValueError('This project is already being registered.')
            floor = db.execute("SELECT value FROM settings WHERE key='project-id-floor'").fetchone()
            maximum = db.execute('SELECT COALESCE(MAX(id),0) FROM projects').fetchone()[0]
            pid = max(maximum,int(floor[0]) if floor else 0)+1
            bounds(pid)
            relative = f'project-databases/{pid}.sqlite3'
            db.execute("INSERT OR REPLACE INTO settings VALUES('project-id-floor',?)",(str(pid),))
            db.execute('INSERT INTO settings VALUES(?,?)',(PENDING+str(pid),json.dumps({'values':values,'path':relative})))
        path = self.path(pid,relative)
        path.parent.mkdir(exist_ok=True)
        store = Store(path,project_namespace=pid,repair_archive=self.catalog.conn.repair_archive)
        from .app.transcript_storage import TranscriptStore
        from .app.transcripts import Transcripts
        transcript = TranscriptStore(str(path)+'.transcripts.sqlite3',repair_archive=self.catalog.conn.repair_archive)
        Transcripts(transcript,catalog=self.catalog,sessions=store)
        with transcript.tx() as db:db.execute("INSERT INTO settings VALUES('transcript-project-id',?)",(str(pid),))
        transcript.conn.close()
        committed = False
        try:
            with store.tx() as db:
                db.execute('INSERT INTO projects(id,slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?,?)',(pid,*values))
                yield db,pid
                db.execute("INSERT INTO settings VALUES('project-registration-ready','1')")
            committed = True
            self._publish(pid,values,relative)
            with self.lock:existing = self.opened.setdefault(pid,store)
            if existing is not store:store.conn.close()
        finally:
            if committed:
                with self.lock:published = self.opened.get(pid) is store
                if not published:store.conn.close()
            else:
                store.conn.close()
                with self.catalog_write() as db:db.execute('DELETE FROM settings WHERE key=?',(PENDING+str(pid),))
                # IDs never recycle, so no new project can reuse these sidecars.
                for base in (path,Path(str(path)+'.transcripts.sqlite3')):
                    for suffix in ('','-wal','-shm'):Path(str(base)+suffix).unlink(missing_ok=True)

    def recover(self):
        with self.publication.hold():self._recover()

    def _recover(self):
        # A process can die after committing a new file and before publication.
        # A ready receipt in that same transaction distinguishes it from an
        # interrupted import, whose initial operational rows rolled back.
        import sqlite3
        for row in self.catalog.all('SELECT key,value FROM settings WHERE key LIKE ?',(PENDING+'%')):
            pid = int(row['key'][len(PENDING):]);item = json.loads(row['value'])
            path = self.path(pid,item['path'])
            ready = False
            if path.is_file():
                with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
                        ready = bool(db.execute("SELECT 1 FROM settings WHERE key='project-registration-ready'").fetchone())
            if ready:
                with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    marker = db.execute("SELECT value FROM settings WHERE key='project-id-namespace'").fetchone()
                    project = db.execute('SELECT slug FROM projects WHERE id=?',(pid,)).fetchone()
                    if not marker or marker[0]!=str(pid) or not project or project[0]!=item['values'][0]:
                        raise ValueError('The pending project database identity is incorrect. Restore a consistent backup.')
                if not Path(str(path)+'.transcripts.sqlite3').is_file():raise ValueError('The new project transcript database is missing; restore it before recovery.')
                with closing(sqlite3.connect(Path(str(path)+'.transcripts.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    marker = db.execute("SELECT value FROM settings WHERE key='transcript-project-id'").fetchone()
                    if not marker or marker[0]!=str(pid):raise ValueError('The pending transcript database identity is incorrect. Restore a consistent backup.')
                self._publish(pid,item['values'],item['path'])
            else:
                with self.catalog_write() as db:db.execute('DELETE FROM settings WHERE key=?',(row['key'],))
                for base in (path,Path(str(path)+'.transcripts.sqlite3')):
                    for suffix in ('','-wal','-shm'):Path(str(base)+suffix).unlink(missing_ok=True)

        for row in self.catalog.all("SELECT key,value FROM settings WHERE key LIKE 'project-removal:%'"):
            item = json.loads(row['value'])
            path = self.path(item['id'],item['path'])
            if path.is_file():
                store = Store(path,project_namespace=item['id'],repair_archive=self.catalog.conn.repair_archive,require_existing=True)
                try:self._mark_removed(store)
                finally:store.conn.close()
            self._unpublish(item['id'],item['path'])

    def _mark_removed(self,store):
        from .project_migration import _fence
        with store.tx():
            _fence(store)
            store.conn.execute("INSERT OR REPLACE INTO settings VALUES('project-storage-removed','1')")

    def _unpublish(self,pid,relative):
        with self.catalog_write() as db:
            journal = state(self.catalog)
            journal['projects'].pop(str(pid),None)
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('project-database-removed:'+str(pid),relative))
            db.execute('DELETE FROM projects WHERE id=?',(pid,))
            save(self.catalog,journal)
            db.execute('DELETE FROM settings WHERE key=?',('project-removal:'+str(pid),))

    def remove(self,project):
        store = self.get(project['id'])
        count = store.one('SELECT COUNT(*) n FROM functions')['n']
        relative = state(self.catalog)['projects'][str(project['id'])]
        with self.catalog_write() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                       ('project-removal:'+str(project['id']),json.dumps({'id':project['id'],'path':relative})))
        # Fence stale callbacks before unpublishing. Keep the removed files as a
        # recoverable tombstone; never unlink a WAL under another process.
        self._mark_removed(store)
        self._unpublish(project['id'],relative)
        return count
