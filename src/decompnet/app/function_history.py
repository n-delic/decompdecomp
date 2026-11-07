"""Bounded inspector history. Full attempts and notes remain in the database."""
import json

from .. import agent_notes
from ..search_notes import useful

NOTE_PAGE = 40
CHART_BUCKETS = 160  # At most five points per bucket, preserving failures and extrema.


def notes_page(store, fn, cursor=None, *, net=None):
    before = ''
    args = [fn['id']]
    if cursor:
        try:
            at, note_id = json.loads(cursor)
            at, note_id = (float(at) if at is not None else None), int(note_id)
        except (ValueError, TypeError, OverflowError):
            raise ValueError('Invalid notes cursor.') from None
        if at is None:
            before = ' AND n.created_at IS NULL AND n.id<?'
            args.append(note_id)
        else:
            before = ' AND (n.created_at IS NULL OR (n.created_at,n.id)<(?,?))'
            args.extend((at,note_id))
    columns = 'n.*' if net is not None else 'n.*,a.name AS agent'
    join = '' if net is not None else ' LEFT JOIN agents a ON a.id=n.agent_id'
    rows = store.all('SELECT '+columns+' FROM notes n'+join+' '
                     'WHERE n.function_id=?' + before +
                     ' ORDER BY n.created_at DESC,n.id DESC LIMIT ?', *args, NOTE_PAGE + 1)
    if net is not None:
        from .project_labels import names
        labels = names(net, (row['agent_id'] for row in rows))
        rows = [dict(row) | {'agent':labels.get(row['agent_id'])} for row in rows]
    page = rows[:NOTE_PAGE]
    next_cursor = json.dumps([page[-1]['created_at'], page[-1]['id']]) if len(rows) > NOTE_PAGE else None
    return {'notes': agent_notes.grouped(store, useful(page), fn['project_id']), 'notes_next': next_cursor}


def history(store, fn, *, net=None):
    bounds = store.one('SELECT COUNT(*) AS n,MIN(id) AS lo,MAX(id) AS hi FROM attempts WHERE function_id=?', fn['id'])
    args = [fn['id']]
    selection = ''
    if bounds['n'] > CHART_BUCKETS * 5:
        width = max(1, (bounds['hi'] - bounds['lo']) // CHART_BUCKETS + 1)
        # SQL reduces the history before Python materialises it. For each bucket keep its ends,
        # best/worst successful score, and a failed compile. Ties use the earliest observation.
        selection = '''WITH points AS MATERIALIZED (
            SELECT id,score,(id-?)/? AS bucket FROM attempts WHERE function_id=?),
            buckets AS (SELECT bucket,MIN(id) AS first,MAX(id) AS last,MIN(score) AS best,MAX(score) AS worst
                        FROM points GROUP BY bucket),
            selected AS (SELECT b.first,b.last,MIN(CASE WHEN p.score=b.best THEN p.id END) AS best,
                MIN(CASE WHEN p.score=b.worst THEN p.id END) AS worst,
                MIN(CASE WHEN p.score IS NULL THEN p.id END) AS failed
                FROM buckets b JOIN points p USING(bucket) GROUP BY b.bucket)
            '''
        args = [bounds['lo'], width, fn['id']]
        selection += '''SELECT t.score,t.created_at,a.name AS agent,t.id FROM attempts t
            LEFT JOIN agents a ON a.id=t.agent_id WHERE t.id IN
            (SELECT first FROM selected UNION SELECT last FROM selected UNION SELECT best FROM selected
             UNION SELECT worst FROM selected UNION SELECT failed FROM selected) ORDER BY t.id'''
    else:
        selection = 'SELECT t.score,t.created_at,a.name AS agent,t.id FROM attempts t LEFT JOIN agents a ON a.id=t.agent_id WHERE t.function_id=? ORDER BY t.id'
    if net is not None:
        selection = selection.replace('a.name AS agent', 't.agent_id').replace('LEFT JOIN agents a ON a.id=t.agent_id', '')
    points = store.all(selection, *args)
    if net is not None:
        from .project_labels import label
        points = label(net, points, {'agent':'agent_id'})
    return {'scores': [{'score': p['score'], 't': p['created_at'], 'agent': p['agent'], 'id': p['id']} for p in points],
            'scores_total': bounds['n'], 'scores_sampled': len(points) < bounds['n'], **notes_page(store, fn, net=net)}
