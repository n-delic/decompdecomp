"""Keep durable search keys and scores; oversized misses do not need full diffs."""
import hashlib
import json

from . import trial_bookkeeping

MAX_DETAIL_BYTES = 16 * 1024


_MISS = """INSERT INTO reuse_trials(function_id,signature,outcome,reason,feedback)
    VALUES(?,?,'miss',?,?)
    ON CONFLICT(function_id,signature) DO UPDATE SET
        outcome=excluded.outcome, reason=excluded.reason, feedback=excluded.feedback
    WHERE reuse_trials.outcome IS NOT excluded.outcome
       OR reuse_trials.reason IS NOT excluded.reason
       OR reuse_trials.feedback IS NOT CASE WHEN typeof(reuse_trials.feedback)='blob'
           THEN unhex(payload_digest(excluded.feedback)) ELSE excluded.feedback END"""


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
    trial_bookkeeping.count(store, _MISS, (fid, key, reason, value), 2,
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
