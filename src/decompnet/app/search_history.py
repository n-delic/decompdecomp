"""Keep durable search keys and scores; oversized misses do not need full diffs."""
import hashlib
import json

MAX_DETAIL_BYTES = 16 * 1024


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
