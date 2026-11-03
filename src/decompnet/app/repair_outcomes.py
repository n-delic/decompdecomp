"""Bounded post-match analysis of frozen captures; never adopt source or change review state."""
import json
import logging
import os
import threading
import time

from . import repair_replay, workers

_SLOT = threading.BoundedSemaphore(1)
KEY = 'automatic_repair_analysis'
SCHEMA = '''CREATE TABLE IF NOT EXISTS repair_outcome_analysis(
 example_id INTEGER PRIMARY KEY REFERENCES repair_examples(id) ON DELETE CASCADE,
 state TEXT NOT NULL, report TEXT NOT NULL, updated_at REAL NOT NULL)'''


def prepare(feedback):
    with feedback.store.tx() as db:
        db.execute(SCHEMA)
        db.execute(QUEUE)
        db.execute('CREATE INDEX IF NOT EXISTS idx_repair_analysis_queue_order ON repair_analysis_queue(created_at,example_id)')
        pending=tuple(feedback.net.__dict__.get('_repair_analysis_pending',()))
        placeholders=','.join('?' for _ in pending) or '-1'
        db.execute('UPDATE repair_outcome_analysis SET state=?,report=?,updated_at=? WHERE state=? AND example_id NOT IN ('+placeholders+')',
                   ('interrupted',json.dumps({'skipped':'Analysis interrupted. Run manual replay to retry; selected source is unchanged.'}),time.time(),'running',*pending))
        db.execute("UPDATE repair_outcome_analysis SET state='pending',report='{}' WHERE state='interrupted' AND example_id IN (SELECT example_id FROM repair_analysis_queue)")


def enabled(feedback):
    row = feedback.store.one('SELECT value FROM settings WHERE key=?',KEY)
    return row is None or row['value']=='true'


def configure(feedback,value):
    if not isinstance(value,bool):raise ValueError('Automatic repair analysis must be true or false.')
    with feedback.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(KEY,json.dumps(value)))
    # The next capture or analysis request resumes a paused queue.


def result(feedback,case_id,include_source=True):
    prepare(feedback)
    row = feedback.store.one('SELECT * FROM repair_outcome_analysis WHERE example_id=?',case_id)
    if not row:return None
    report=json.loads(row['report'])
    if not include_source:
        # Compiler errors/dumps can quote source too. Summary exports retain only counts.
        minimized=report.get('minimization') or {}
        report={'summary':report.get('summary',{}),'minimization':{k:minimized[k] for k in
                ('edits','retained','reverted','exhausted') if k in minimized},
                'baseline_score':report.get('baseline_score'),'skipped':bool(report.get('skipped'))}
    return {'state':row['state'],'report':report}


def analyze(example):
    # Replay validates captured source/header hashes, installed toolchain and identical settings.
    # Four ablations plus two control compiles, no candidate-family search in the automatic pass.
    return repair_replay.replay(example,trials=4,seconds=20,minimize=True,diagnostics=True,ablation_only=True)


QUEUE = """CREATE TABLE IF NOT EXISTS repair_analysis_queue(
 example_id INTEGER PRIMARY KEY REFERENCES repair_examples(id) ON DELETE CASCADE,
 expected_hash TEXT, created_at REAL NOT NULL)"""


def backlog(feedback):
    prepare(feedback)
    return {row['state']:row['count'] for row in feedback.store.all(
        'SELECT state,COUNT(*) AS count FROM repair_outcome_analysis GROUP BY state')}


def resume(feedback):
    """One draining worker, with durable rows rather than one future per capture."""
    if os.environ.get('DECOMP_REPAIR_ANALYSIS')=='off' or not hasattr(feedback.net,'forks') or not enabled(feedback):return
    if not _SLOT.acquire(blocking=False):return
    lock=feedback.net.__dict__.setdefault('_repair_analysis_lock',threading.Lock())
    pending=feedback.net.__dict__.setdefault('_repair_analysis_pending',set())
    def run():
        try:
            while enabled(feedback):
                prepare(feedback)
                row=feedback.store.one('SELECT * FROM repair_analysis_queue ORDER BY created_at,example_id LIMIT 1')
                if not row:break
                case_id=row['example_id']
                with lock:pending.add(case_id)
                try:
                    example=feedback.detail('',case_id)
                    if row['expected_hash'] and example['evidence']['after']['source_sha256']!=row['expected_hash']:
                        raise ValueError('Captured source changed; analysis skipped.')
                    with feedback.store.tx() as db:
                        db.execute('UPDATE repair_outcome_analysis SET state=?,updated_at=? WHERE example_id=?',('running',time.time(),case_id))
                    report=analyze(example)
                    state='skipped' if report.get('skipped') else 'done'
                except Exception as error:
                    logging.getLogger('decompdecomp').warning('Repair outcome analysis #%s: %s',case_id,error)
                    state,report='failed',{'skipped':str(error)[:300]}
                with feedback.store.tx() as db:
                    db.execute('UPDATE repair_outcome_analysis SET state=?,report=?,updated_at=? WHERE example_id=?',
                               (state,json.dumps(report),time.time(),case_id))
                    db.execute('DELETE FROM repair_analysis_queue WHERE example_id=?',(case_id,))
                with lock:pending.discard(case_id)
        finally:
            with lock:pending.clear()
            _SLOT.release()
        # Close the empty-queue/enqueue race without allocating a future per capture.
        if enabled(feedback) and feedback.store.one('SELECT 1 FROM repair_analysis_queue LIMIT 1'):
            resume(feedback)
    try:workers.pool().submit(run)
    except Exception as error:
        _SLOT.release()
        logging.getLogger('decompdecomp').warning('Unable to schedule repair analysis: %s',error)


def schedule(feedback,case_id,expected_hash=None):
    if os.environ.get('DECOMP_REPAIR_ANALYSIS')=='off' or not hasattr(feedback.net,'forks'):return
    prepare(feedback)
    with feedback.store.tx() as db:
        # Enqueue inside the capture transaction: rollback removes the queue row too.
        old=db.execute('SELECT state FROM repair_outcome_analysis WHERE example_id=?',(case_id,)).fetchone()
        if not old:
            db.execute('INSERT OR IGNORE INTO repair_analysis_queue VALUES(?,?,?)',(case_id,expected_hash,time.time()))
            db.execute('INSERT OR IGNORE INTO repair_outcome_analysis VALUES(?,?,?,?)',(case_id,'pending','{}',time.time()))
    resume(feedback)
