"""Explicit function suggestions publish one freshly verified source/header/profile bundle."""
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
