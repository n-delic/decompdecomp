"""The inspector's saved before/after C; historical C is never a new matching verdict."""


def detail(store, fn, *, history=True):
    polished = fn['status'] == 'matched' and bool(fn['readable'])
    source = fn['unpolished_source'] if polished else None
    if polished and not source and history and fn['matched_at'] is not None:
        # Older installations did not snapshot the pre-polish source. Restrict
        # recovery to a successful attempt recorded no later than a recorded match; use the earliest one so a later
        # recheck of polished C cannot become its own unpolished comparison.
        row = store.one('SELECT source FROM attempts WHERE function_id=? AND score=0 '
                        'AND created_at<=? AND (source_hash IS NOT NULL OR source IS NOT NULL) '
                        'ORDER BY id LIMIT 1', fn['id'], fn['matched_at'])
        source = row['source'] if row else None
    return {'polished':polished, 'unpolished_source':source}
