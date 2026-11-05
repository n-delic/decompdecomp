"""Bounded, cancellable transcript retention; saved code and lifetime summaries remain."""
import json
from datetime import date, datetime, time, timezone
from pathlib import Path

KEY='database-cleanup-options'
BATCH=500


def options(value):
    if not isinstance(value,dict) or type(value.get('removed',True)) is not bool:
        raise ValueError('Choose whether to clean transcripts from removed agents.')
    if type(value.get('automatic',False)) is not bool:raise ValueError('Choose whether to enforce the cutoff automatically.')
    before=value.get('before','')
    if not isinstance(before,str):raise ValueError('Choose a valid cutoff date.')
    cutoff=None
    if before:
        try:
            day=date.fromisoformat(before)
            if day.isoformat()!=before or day>datetime.now(timezone.utc).date():raise ValueError
            cutoff=datetime.combine(day,time.min,timezone.utc).timestamp()
        except ValueError:raise ValueError('Choose a cutoff date no later than today (UTC).') from None
    if value.get('automatic',False) and cutoff is None:raise ValueError('Choose a cutoff date for automatic cleanup.')
    if not value.get('removed',True) and cutoff is None:
        raise ValueError('Choose removed agents or a cutoff date before starting cleanup.')
    return {'removed':value.get('removed',True),'before':before,'automatic':value.get('automatic',False)},cutoff


def saved(net):
    row=net.store.one('SELECT value FROM settings WHERE key=?',KEY)
    return json.loads(row['value']) if row else {'removed':True,'before':'','automatic':False}


def eligible(net,value):
    value,cutoff=options(value)
    return net.store.all('SELECT s.id,s.helper_id FROM helper_sessions s WHERE '
        '((? AND NOT EXISTS(SELECT 1 FROM helpers h WHERE h.id=s.helper_id AND h.project=s.project AND h.name=s.agent)) '
        'OR (? IS NOT NULL AND s.ended_at IS NOT NULL AND s.ended_at<?)) '
        'AND EXISTS(SELECT 1 FROM helper_messages m WHERE m.session_id=s.id) ORDER BY s.id',
        value['removed'],cutoff,cutoff)


def summary(net,value=None):
    value,_=options(saved(net) if value is None else value)
    rows=eligible(net,value)
    ids=json.dumps([r['id'] for r in rows])
    count=net.store.one('SELECT COUNT(*) n FROM helper_messages WHERE session_id IN (SELECT value FROM json_each(?))',ids)['n']
    path=Path(net.store.path)
    disk=sum(f.stat().st_size for f in [path,Path(str(path)+'-wal'),Path(str(path)+'-shm')] if f.is_file())
    return {'options':value,'sessions':len(rows),'messages':count,'disk_bytes':disk}


def run(net,value,log):
    from . import maintenance, tool_policy
    value,_=options(value)
    rows=eligible(net,value)
    tool_policy.ensure(net)  # Preserve lifetime counters before their legacy backfill evidence disappears.
    deleted=0;done=0;total=len(rows)
    def report(phase):
        getattr(log,'progress',lambda *_:None)({'phase':phase,'done':done,'total':total,'deleted_messages':deleted})
    report('Clearing old agent transcripts')
    for session in rows:
        first=True
        while True:
            getattr(log,'check_cancel',lambda:None)()
            with net.store.tx() as db:
                if first:
                    db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('helper-transcript-session-cleared:'+str(session['id']),'1'))
                    last=db.execute('SELECT COALESCE(MAX(id),0) FROM helper_messages').fetchone()[0]
                    db.execute("INSERT INTO settings VALUES('helper-message-id-floor',?) ON CONFLICT(key) DO UPDATE SET "
                               "value=CAST(MAX(CAST(value AS INTEGER),CAST(excluded.value AS INTEGER)) AS TEXT)",(str(last),))
                removed=db.execute('DELETE FROM helper_messages WHERE id IN (SELECT id FROM helper_messages '
                                   'WHERE session_id=? ORDER BY id LIMIT ?)',(session['id'],BATCH)).rowcount
                if not removed:
                    db.execute('DELETE FROM helper_score_events WHERE session_id=?',(session['id'],))
                    # A current worker's resumable conversation is separate from its old session history.
                    if not db.execute('SELECT 1 FROM helpers WHERE id=?',(session['helper_id'],)).fetchone():
                        db.execute('DELETE FROM settings WHERE key=?',('helper-conversation:'+str(session['helper_id']),))
            first=False;deleted+=removed
            maintenance.checkpoint()  # Pace only outside the write transaction.
            report('Clearing old agent transcripts')
            if not removed:break
        done+=1
        report('Clearing old agent transcripts')
    log(f'Cleanup complete: {deleted:,} transcript records removed from {done:,} sessions. Saved work and usage totals kept.')
    return {'sessions':done,'messages':deleted}


def save(net,value):
    value,_=options(value)
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(KEY,json.dumps(value)))
    retention=getattr(net,'database_retention',None)
    if retention is not None:retention.next_check=0
    return value


def start(net,jobs,value):
    value,_=options(value)
    with jobs._lock:
        active=next((j for j in jobs.jobs.values() if j['kind']=='Database transcript cleanup' and j['state']=='running'),None)
        if active:return active['id']
        def work(log):
            from .maintenance import slot
            log('Waiting for the background maintenance lane')
            with slot(getattr(log,'cancel_event',None)) as acquired:
                if not acquired:log.check_cancel()
                return run(net,value,log)
        return jobs.start('Database transcript cleanup',work,
                          cancel_message='Cleanup stopped. Records already removed stay deleted; you can run cleanup again.')


class Retention:
    """Checked by the existing autosave coordinator; no additional polling thread."""
    def __init__(self,net,jobs):
        self.net,self.jobs=net,jobs
        self.next_check=0

    def tick(self,now=None):
        import time
        now=time.monotonic() if now is None else now
        if now<self.next_check:return
        self.next_check=now+300
        value=saved(self.net)
        if value.get('automatic') and eligible(self.net,value):start(self.net,self.jobs,value)
