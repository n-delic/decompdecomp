"""Keep durable search keys and scores; oversized misses do not need full diffs."""
import hashlib
import json
from contextlib import contextmanager

from .. import compile_timing, payload_storage
from . import trial_bookkeeping

MAX_DETAIL_BYTES = 16 * 1024


_MISS = """INSERT INTO reuse_trials(function_id,signature,outcome,reason,feedback)
    VALUES(?,?,?,?,?)
    ON CONFLICT(function_id,signature) DO UPDATE SET
        outcome=excluded.outcome, reason=excluded.reason, feedback=excluded.feedback
    WHERE reuse_trials.outcome != 'exact' AND (
       reuse_trials.outcome IS NOT excluded.outcome
       OR reuse_trials.reason IS NOT excluded.reason
       OR reuse_trials.feedback IS NOT CASE WHEN typeof(reuse_trials.feedback)='blob'
           THEN unhex(payload_digest(excluded.feedback)) ELSE excluded.feedback END)"""


# The composite trial index bounds each range to this function. Load rich
# evidence in its stored form and only decompress it when a trial uses it.
_ABSENT = object()
_SAVED_MISSES = """SELECT signature, outcome, feedback,
    CASE WHEN typeof(feedback)='blob' THEN
        (SELECT payload FROM text_payloads WHERE hash=lower(hex(reuse_trials.feedback)))
    END AS packed
    FROM main.reuse_trials
    WHERE function_id=? AND outcome IN ('miss','compile_failed') AND (
        (signature >= 'deep-v1:' AND signature < 'deep-v1;') OR
        (signature >= 'deep-profile-v1:' AND signature < 'deep-profile-v1;'))"""


class FunctionMisses:
    """One snapshot and one flush for a function, including its parallel paths."""

    def __init__(self, store, fid, check):
        self.store = store
        self.fid = fid
        self.saved = {}
        self.pending = {}
        with compile_timing.phase('history_read'):
            store.scan(_SAVED_MISSES, lambda rows: self._load(rows, check), fid)
        compile_timing.add('history_read_rows', len(self.saved))

    def _load(self, rows, check):
        while page := rows.fetchmany(1024):
            check()
            for row in page:
                value = row['feedback'] if row['outcome'] == 'miss' else None
                if isinstance(value, bytes):
                    value = (value, row['packed'])
                self.saved[row['signature']] = value

    def get(self, key):
        queued = self.pending.get(key)
        if queued is not None:
            outcome, _, value = queued
            return outcome, value
        value = self.saved.get(key, _ABSENT)
        if value is _ABSENT:
            return None
        if value is None:
            return 'compile_failed', None
        if isinstance(value, tuple):
            digest, packed = value
            value = payload_storage.unpack(digest.hex(), packed)
        return 'miss', value

    def forget(self, key):
        self.pending.pop(key, None)
        self.saved.pop(key, None)

    def record(self, key, outcome, reason, value):
        # Parallel paths only replace individual dictionary entries. Their owner
        # waits for every path before iterating the dictionary to flush it.
        self.pending[key] = (outcome, reason, value)

    def flush(self):
        if not self.pending:
            return
        rows = ((self.fid, key, *row) for key, row in self.pending.items())
        trial_bookkeeping.flush_with(self.store, _MISS, rows, len(self.pending))
        self.pending.clear()


@contextmanager
def function_turn(log, store, fid):
    log.check_cancel()
    misses = FunctionMisses(store, fid, log.check_cancel)
    previous = getattr(log, 'miss_cache', None)
    log.miss_cache = misses
    maximum = getattr(getattr(log, 'compile_pool', None), 'maximum', False)
    with trial_bookkeeping.scope(maximum):
        try:
            yield misses
        finally:
            log.miss_cache = previous
            misses.flush()


def signature(scope, source):
    return 'deep-v1:' + hashlib.sha256((scope + source).encode()).hexdigest()


def miss_feedback(store, fid, key, primed, *, read=False):
    slot = (fid, key)
    pending = trial_bookkeeping.lookup(store, ('reuse_trials', *slot))
    if pending is not None:
        return pending
    if slot in primed:
        return primed[slot]
    if read:
        row = store.one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?', *slot)
        return row['feedback'] if row else None


def record_miss(store, fid, key, reason, value):
    # These keys are separate from probes and saved roots. Keep the latest record per
    # key in the flush and preserve the feedback for work reusing it before commit.
    trial_bookkeeping.count(store, _MISS, (fid, key, 'miss', reason, value), 2,
                            memo=(('reuse_trials', fid, key), value))


def feedback(value):
    if not isinstance(value,dict):return value
    packed=json.dumps(value,separators=(',',':'))
    if len(packed.encode())<=MAX_DETAIL_BYTES:return value
    # The output hash can guide same-output repairs without keeping assembly or C.
    # Missing local diff rows are advisory: an improving candidate is always rejudged.
    return {key:value[key] for key in ('score','output') if key in value}


def prune(store,log):
    from .maintenance import checkpoint
    cursor=-9223372036854775808;changed=0
    while True:
        log.check_cancel()
        # Read only a bounded page of row IDs first. Decode one feedback at a time;
        # the page may reference very large compressed legacy payloads.
        rows=store.all("SELECT rowid AS identity,function_id,signature FROM main.reuse_trials "
                       "WHERE rowid>? ORDER BY rowid LIMIT 128",cursor)
        if not rows:break
        for row in rows:
            log.check_cancel();cursor=row['identity']
            if not row['signature'].startswith('deep-v1:'):continue
            current=store.one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?',row['function_id'],row['signature'])
            if not current:continue
            try:value=json.loads(current['feedback'])
            except (TypeError,ValueError):continue
            compact=feedback(value)
            if compact==value:continue
            packed=json.dumps(compact,separators=(',',':'))
            with store.tx(wait=log.check_cancel) as db:
                # Never replace newer search feedback with an old read snapshot.
                changed+=db.execute('UPDATE reuse_trials SET feedback=? WHERE function_id=? AND signature=? AND '
                                    "CASE WHEN typeof(feedback)='blob' THEN lower(hex(feedback)) ELSE payload_digest(feedback) END=?",(packed,row['function_id'],row['signature'],hashlib.sha256(current['feedback'].encode()).hexdigest())).rowcount
            checkpoint()
        log.progress({'phase':'Reducing oversized search diagnostics','done':changed})
        checkpoint()
    return changed
