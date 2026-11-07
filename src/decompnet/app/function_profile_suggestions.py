"""Explicit function suggestions publish one freshly verified source/header/profile bundle."""
import re

from .. import toolchains
from ..network import NetworkError
from . import prepared_evidence, source_preparation


def contained(fn, spans):
    return bool(fn['vram'] is not None and fn['size'] and fn['size']>0 and
                all(fn['vram']<=lo<hi<=fn['vram']+fn['size'] for lo,hi in spans))


def capture(net, proj, function, spans, source):
    if not function or not net.is_rom(proj) or net.fork_slug(proj['slug']):return None
    fn=net._function(function,proj['slug'])
    if not fn['size'] or fn['size']<=0 or fn['vram'] is None:
        raise NetworkError('Resolve this function boundary before suggesting compiler settings.')
    if not contained(fn,spans):return None
    text=source or net._source_of(fn)
    if not text or not text.strip():
        raise NetworkError('Supply the exact C with this function compiler suggestion.')
    return {'function':fn['name'],'vram':fn['vram'],'size':fn['size'],'source':text,
            'header':(net._context(proj['id'],fn['unit']) or {'header':''})['header']}


def saved(net, proj, suggestion):
    """Older single-entry range checks retained their exact source, but not a private header."""
    if suggestion.get('function_match'):return suggestion['function_match']
    for check in net.cfg(proj).get('range_checks',[]):
        if check.get('suggestion')!=suggestion['id'] or not check.get('origin_source'):continue
        fn=net.store.one('SELECT * FROM functions WHERE project_id=? AND vram=?',proj['id'],check.get('origin'))
        if fn and contained(fn,suggestion.get('ranges') or []) and suggestion.get('ranges'):
            return capture(net,proj,fn['name'],suggestion['ranges'],check['origin_source'])
    return None


def apply(net, agent, proj, bundle, toolchain, flags):
    """Never borrow another worker's claim or overwrite already verified C."""
    fn=net._function(bundle['function'],proj['slug'])
    if fn['status']!='open' or (fn['vram'],fn['size'])!=(bundle['vram'],bundle['size']):
        raise NetworkError('This function is already matched or its boundary changed. Review a fresh suggestion.')
    with net.store.tx() as db:
        net._expire_claims(db)
        claim=db.execute('SELECT agent_id FROM claims WHERE function_id=?',(fn['id'],)).fetchone()
    if claim and claim['agent_id']!=agent.id:
        raise NetworkError('This function is being worked on by another agent. Apply the suggestion after it is released.')
    acquired=not claim
    if acquired and not net.permuter._hold(agent,fn,count_turn=False,seconds=60):
        raise NetworkError('This function became busy. Apply the suggestion after it is released.')
    try:
        fresh=net.project_row(proj['slug']);fn=net._function(fn['name'],proj['slug'])
        if fn['status']!='open' or (fn['vram'],fn['size'])!=(bundle['vram'],bundle['size']):
            raise NetworkError('This function changed while claiming it. Review a fresh suggestion.')
        expected=source_preparation.inputs(net,fresh,fn)
        prepared_evidence.promote_measured(net,fresh,fn,bundle['source'],bundle['header'],
                                          {'toolchain':toolchain,'flags':list(flags)},agent,expected)
    finally:
        if acquired:net.permuter._let_go(agent,fn)


def public(suggestion):
    """Overview cards need scope, not the private compiler input bundle."""
    result={k:v for k,v in suggestion.items() if k!='function_match'}
    if bundle:=suggestion.get('function_match'):
        result['function_scope']={k:bundle[k] for k in ('function','vram','size')}
    return result


def settle_checks(cfg, fn, settings):
    """Close only same-profile, single-entry checks in the exact bundle transaction."""
    ids=set()
    for check in cfg.get('range_checks',[]):
        if (check.get('origin')!=fn['vram'] or check.get('toolchain')!=settings['toolchain']
                or check.get('flags')!=settings['flags'] or not check.get('spans')
                or not contained(fn,check['spans'])):continue
        check.update(status='function_applied',range=[fn['vram'],fn['vram']+fn['size']],
                     said='Exact source/header/profile applied to this function only; no neighbouring compiler inference.')
        ids.add(check.get('suggestion'))
    for item in cfg.get('suggestions',[]):
        if item['id'] in ids:
            item.update(status='applied',check='Freshly verified and applied to the originating function only.')


def actionable(net, proj, suggestion):
    """Single-function compiler proposals expire when that function is no longer open."""
    if not suggestion.get('toolchain'):
        return True
    bundle = suggestion.get('function_match')
    if bundle:
        fn = net.store.one('SELECT status,vram,size FROM functions WHERE project_id=? AND name=?',
                           proj['id'], bundle['function'])
        return bool(fn and fn['status'] == 'open' and (fn['vram'], fn['size']) == (bundle['vram'], bundle['size']))
    # Legacy map suggestions identify their originating function through the range check.
    checks = net.cfg(proj).get('range_checks', [])
    for check in checks:
        if check.get('suggestion') != suggestion['id'] or check.get('origin') is None:
            continue
        rows = net.store.all('SELECT status,vram,size FROM functions WHERE project_id=? AND vram=?',
                             proj['id'], check['origin'])
        if len(rows) == 1 and suggestion.get('ranges') and contained(rows[0], suggestion['ranges']):
            return rows[0]['status'] == 'open'
    return True


def accept_all(net, slug, log):
    """Apply a snapshot sequentially through the usual verification and match protections."""
    log.check_cancel()
    # The inbox lives in config. An overview also loads coverage, reuse results
    # and diagnostics, and _project can synchronize saved files before returning.
    proj = net.store.one('SELECT id,slug,config FROM projects WHERE slug=?', slug)
    if proj is None:raise NetworkError('Project not found; reopen it and try again.')
    items = [{'id':s['id'], 'toolchain':s['toolchain']}
             for s in net.cfg(proj).get('suggestions', [])
             if s.get('status') == 'open' and s.get('toolchain')
             and not re.search(r"does not match|doesn't compile", s.get('check') or '')]
    results = []
    def publish(item=None, current=None):
        log.progress({'phase':'Applying waiting suggestions', 'done':len(results), 'total':len(items),
                      'applied':sum(r['applied'] for r in results), 'skipped':sum(not r['applied'] for r in results),
                      'suggestion':item['id'] if item else None, 'compiler':item.get('toolchain', '') if item else '',
                      'current':current or {}})
    class SuggestionLog:
        def __init__(self, item):self.item = item
        def __call__(self, line):log(line)
        def __getattr__(self, name):return getattr(log, name)
        def progress(self, value):publish(self.item, value)
    publish()
    for index, item in enumerate(items, 1):
        log.check_cancel()
        publish(item, {'phase':'Checking suggested settings'})
        current = net.store.one('SELECT * FROM projects WHERE slug=?', slug)
        if current is None:raise NetworkError('Project not found; reopen it and try again.')
        fresh = next((s for s in net.cfg(current).get('suggestions', []) if s['id'] == item['id']), None)
        reason = None
        try:
            if not fresh or fresh['status'] != 'open' or not actionable(net, current, fresh):
                raise NetworkError('Already resolved or no longer needs a compiler suggestion; skipped.')
            net.resolve_suggestion(slug, item['id'], True, log=SuggestionLog(item))
        except (NetworkError, toolchains.ToolchainError, OSError) as error:
            reason = str(error)
        results.append({'id':item['id'], 'applied':reason is None, 'reason':reason})
        log(f"{index}/{len(items)}: suggestion #{item['id']}: {reason or 'applied'}")
        publish(item, {'phase':'Suggestion skipped' if reason else 'Settings and scores saved'})
    publish(current={'phase':'Saving project progress'})
    net.progress.sync(slug, force=True, logger=log)
    log.check_cancel()
    publish(current={'phase':'Refreshing project overview'})
    return {'overview':net.overview(slug), 'results':results,
            'applied':sum(r['applied'] for r in results), 'skipped':sum(not r['applied'] for r in results)}


def dismiss_all(net, slug, ids):
    """Dismiss only the displayed compiler inbox snapshot, in one config transaction."""
    if not isinstance(ids, list) or any(type(sid) is not int for sid in ids):
        raise NetworkError('Refresh the compiler suggestions and try dismissing them again.')
    selected = set(ids)
    dismissed = []
    def change(cfg):
        for item in cfg.get('suggestions', []):
            if item['id'] in selected and item.get('toolchain') and item.get('status') == 'open':
                item['status'] = 'dismissed'
                dismissed.append(item['id'])
    net._update_cfg(slug, change)
    return len(dismissed)
