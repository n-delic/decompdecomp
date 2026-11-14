"""Saved-layout follow-ups: the rewrites are `decomprepair.layout_frontiers`; reading a function's earlier attempts
needs the project database, so `saved` stays here."""
from decomprepair.layout_frontiers import *  # noqa: F403 - spans, safe, equivalent, project, transplant, offer, followups
from decomprepair.layout_frontiers import LIMIT, project


def saved(net, proj, fn, source, header=''):
    """Read at most 32 private attempts; old scores only choose candidates, never verdicts."""
    result=[]
    for row in net.project_store(proj['id']).all(
            'SELECT source FROM attempts WHERE function_id=? AND score IS NOT NULL ORDER BY id DESC LIMIT 32', fn['id']):
        candidate=row['source']
        if candidate and candidate != source and project(source,candidate,header) and candidate not in result:
            result.append(candidate)
        if len(result)>=LIMIT:
            break
    return result
