"""Detected-code progress is not a whole-image coverage proof; retain unresolved boundaries."""
from ..network import IN_BUILD
from . import code_audit_reviews

LIMITS=('candidate_limit','mapping_limit','pointer_probe_limit','callback_scan_limit','hint_scan_limit')


def _view(net, proj, kind, build):
    # Target decoding is diagnostic work, not a per-poll scan. Function edits
    # invalidate the map revision; audit reviews and project mappings have their own keys.
    from .view_snapshots import cache
    if getattr(net.store._local, 'in_tx', False):
        return build()
    row = net.store.one('SELECT map_revision FROM project_totals WHERE project_id=?', proj['id'])
    audit = net.store.one('SELECT value FROM settings WHERE key=?', code_audit_reviews.key(proj['id']))
    key = (kind, proj['id'], row['map_revision'] if row else None, proj['config'], audit['value'] if audit else None)
    return cache(net).get(key, build, weight=lambda result: len(result) if isinstance(result, list) else 1)


def boundaries(net, proj):
    return _view(net, proj, 'coverage-boundaries', lambda: _boundaries(net, proj))


def _boundaries(net,proj):
    """Compute a review state from frozen bytes, including legacy assembly classifications.

    Keep the persisted status, claims and history: an operator decision is not
    erased, but neither can that decision hide an incomplete extent in progress.
    """
    if not net.is_rom(proj):return []
    console=net.console(proj);out=[]
    for row in net.store.all(f"SELECT name,vram,size,target,status FROM functions WHERE project_id=? AND {IN_BUILD} AND status!='matched'",proj['id']):
        if reason:=console.boundary_reason(row['target'] or b''):
            out.append({'function':row['name'],'vram':row['vram'],'size':row['size'],'stored_status':row['status'],
                        'analysis_state':'boundary_review','reason':reason})
    return out


def snapshot(net, proj):
    return _view(net, proj, 'code-coverage', lambda: _snapshot(net, proj))


def _snapshot(net,proj):
    """Cheap current boundary checks plus the last scoped CPU gap audit, never an implicit completion."""
    if not net.is_rom(proj):return None
    findings=boundaries(net,proj)
    counts=net.store.one(f"SELECT COUNT(*) total,SUM(status='open') open,SUM(size) bytes FROM functions WHERE project_id=? AND {IN_BUILD}",proj['id'])
    supported=proj['arch']=='mips'
    state=code_audit_reviews.load(net,proj['id']) if supported else {'report':None}
    report=state['report'];queue_empty=bool(counts['total'] and not counts['open'])
    unresolved=0
    if report:
        unresolved=sum(state.get('reviews',{}).get(r.get('review_id'),{}).get('state')!='dismissed' for r in report['candidates'])
        # A confirmed-code review is still missing code until it is recovered into
        # the project. Acknowledging a warning cannot turn it into a match.
    limits=bool(report and any(report.get(k) for k in LIMITS))
    status=('unsupported' if not supported else 'required' if not report else
            'needs_review' if findings or unresolved or limits else 'no_pending_findings')
    return {'scope':'detected code only','whole_game_complete':False,'supported':supported,
            'detected_bytes':counts['bytes'] or 0,'incomplete_entries':findings,
            'incomplete_assembly':sum(r['stored_status']=='asm' for r in findings),
            'audit_state':status,'audit_stale':state.get('stale',False),
            'audit_revision':code_audit_reviews.revision(net,proj['id']) if supported else None,
            'audit_required':supported and not report,'queue_empty':queue_empty,
            'completion_audit_required':queue_empty and supported and not report,
            'unresolved_candidates':unresolved,'search_limited':limits,
            'raw_bytes':report.get('gap_bytes',0) if report else None,
            'interior_bytes':report.get('interior_bytes',0) if report else None,
            'note':'Percentages cover detected candidates. Raw regions can contain code, data or microcode. An empty queue or an identical rebuild does not establish whole-game completion.'}
