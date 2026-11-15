"""Bounded, match-ranked compiler profiles for ROM deep searches, including host-verified room submissions."""
import hashlib
import json

from ..network import NetworkError


def validate(count):
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 16:
        raise NetworkError('Choose 1–16 compiler profiles per candidate.')
    return count


def selected(net, proj, count):
    """Default first, then distinct profiles ranked by verified matched-function usage."""
    validate(count)
    default = net.cfg(proj)['compiler']
    default = {'toolchain':default['toolchain'], 'flags':default['flags'].split() if isinstance(default['flags'], str) else list(default['flags'])}
    if count == 1:return [default]
    key = lambda p:(p['toolchain'], tuple(p['flags']))
    others = sorted(net.compiler_profile_usage(proj), key=lambda p:(-p['matched'], key(p)))
    result = [default]
    seen = {key(default)}
    for profile in others:
        if profile['matched'] > 0 and key(profile) not in seen:
            result.append({'toolchain':profile['toolchain'], 'flags':list(profile['flags'])})
            seen.add(key(profile))
    return result[:count]


def probe(search, agent, proj, fn, source, profile):
    """Cache misses separately by profile; only a reverified exact bundle can change settings."""
    from . import agent_tokens
    search.check()
    search.visited += 1
    scope = agent_tokens.target_scope(search.net, {**dict(fn), 'best_source':source}, '')
    key = 'deep-profile-v1:'+hashlib.sha256(repr((scope, source, profile)).encode()).hexdigest()
    cached = search.net.project_store(proj['id']).one('SELECT outcome FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], key)
    if cached and cached['outcome'] == 'miss':
        search.skipped += 1
        return None
    header=search.net._context(proj['id'],fn['unit'])['header']
    search.tested += 1
    search.report()
    exact, diff, _ = search.net._judge_with(proj, fn, profile['toolchain'], profile['flags'], source)
    score = (0 if exact else max(1, diff.score)) if diff is not None else None
    with search.net.project_store(proj['id']).tx() as db:
        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                   (fn['id'], key, 'exact' if exact else 'miss' if score is not None else 'compile_failed',
                    'deep compiler profile probe', json.dumps({'score':score, 'profile':profile, **({'source':source} if exact else {})})))
    search.log('Profile '+profile['toolchain']+' '+' '.join(profile['flags'])+': '+
               ('exact; rechecking source/profile bundle' if exact else 'score '+str(score) if score is not None else 'does not compile'))
    if exact:
        from . import prepared_evidence
        fresh = search.net.project_row(proj['slug'])
        current = search.net._function(fn['name'], proj['slug'])
        if agent_tokens.target_scope(search.net, {**dict(current), 'best_source':source}, '') != scope:
            raise NetworkError('Deep profile inputs changed during verification. Run the function again.')
        link=search.net.links.get(proj['slug'])
        if link:
            from .. import match_bundle
            from . import deep_room, room_matches
            # Freeze the tested compiler and private header into the durable room packet.
            # Never change local assignments or report a win before the host accepts it.
            evidence=room_matches.packet(search.net,proj['slug'],fn['name'],source)
            evidence.update(compiler=profile,compiler_identity=match_bundle.compiler_identity(profile),
                            header=header,header_sha256=match_bundle.digest(header))
            search.log.check_cancel()
            search.phase='awaiting host profile verification'
            search.report()
            search.log('Submitting exact source/header/compiler bundle for host verification…')
            result=deep_room.submit(search,link,agent,fn,source,evidence=evidence)
            search.room_holds.pop(fn['id'],None)
            if result.get('matched'):
                try:link.sync()
                except NetworkError as error:deep_room.disconnected(search,link,error)
                search.log('Host verified the alternate compiler profile match.')
                return 0
            from .deep_permuter import SearchLimit
            raise SearchLimit('Exact alternate profile submitted; awaiting host verification. '+
                              (result.get('reason') or 'Evidence retained for room verification retry.'))
        evidence = {'exact':True, 'score':0, 'source':source,
                    'header':search.net._context(proj['id'], fn['unit'])['header'],
                    **profile, 'inputs':prepared_evidence.digest(search.net, fresh, current),
                    'category':'deep compiler profile', 'diagnostic':'Fresh linked-byte profile verdict; recheck before applying.'}
        with search.net.project_store(proj['id']).tx() as db:
            db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                       (fn['id'], 'local-source-'+key, '0', 'deep compiler profile exact bundle', json.dumps(evidence)))
        # suggest rechecks inputs and respects the user's automatic-compiler-changes preference.
        # Reserve the two verification compiles used by suggest's check and atomic promotion.
        if search.limit-search.tested < 2:
            search.log('Exact profile evidence saved. More compile allowance is needed to reverify and apply it.')
            return None
        search.check()
        search.tested += 2
        search.net.suggest(agent, proj['slug'], 'Deep permuter found an exact source/compiler profile pair.',
                           compiler=profile['toolchain'], flags=' '.join(profile['flags']),
                           function=fn['name'], source=source)
        if search.net._function(fn['name'],proj['slug'])['status'] == 'matched':
            return 0
    return None
