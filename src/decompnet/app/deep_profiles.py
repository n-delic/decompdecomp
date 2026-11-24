"""Bounded, match-ranked compiler profiles for ROM deep searches, including host-verified room submissions."""
import hashlib
import json

from ..network import NetworkError
from . import trial_bookkeeping


def validate(count):
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 16:
        raise NetworkError('Choose 1–16 compiler profiles per candidate.')
    return count


def _profile(compiler):
    return {'toolchain':compiler['toolchain'],
            'flags':compiler['flags'].split() if isinstance(compiler['flags'], str) else list(compiler['flags'])}


def selected(net, proj, count, fn=None):
    """The function's own settings first (its range's or its own profile, as the judge builds it), then the project
    default (always, when it differs), then distinct profiles ranked by verified matched-function usage. With one profile per candidate the
    default alone left an N64 function with its own -fno-schedule-insns unscored: every candidate went to the
    alternate-profile probe, which only reports exact matches, so 10,000 variants could never count as better."""
    from ..project import compiler_for
    validate(count)
    cfg = net.cfg(proj)
    default = _profile(cfg['compiler'])
    first = _profile(compiler_for(cfg, fn['vram'], fn)) if fn is not None else default
    key = lambda p:(p['toolchain'], tuple(p['flags']))
    # (a function whose own settings differ keeps the default as an exact-only probe even at one profile: an own
    # setting once applied on a draft's score hid a match that only the default compiled)
    result = [first] + ([default] if key(default) != key(first) else [])
    # (a function setting the recheck removed for want of evidence stays a hypothesis: tried for an exact match only)
    from .unverified_profiles import hypothesis
    if (guess := hypothesis(cfg, fn)) is not None and key(guess) not in {key(p) for p in result}:
        result.append(guess)
    # (the target's own moves can name another compiler: an IDO library in a GCC game, never probed before)
    if fn is not None and proj:
        from .move_fingerprints import profiles as fingerprinted
        for guess in fingerprinted(net, proj, cfg, fn, first):
            if key(guess) not in {key(p) for p in result}:
                result.append(guess)
    # (the profile compiler detection found for this saved C goes first: every candidate is probed under it before
    # anything else, the function's own settings still scored right after it)
    if fn is not None and proj:
        from .profile_detection import detected
        if (found := detected(net, proj, fn)) is not None:
            result = [found['profile']] + [p for p in result if key(p) != key(found['profile'])]
            count += 1  # (it never takes the place of a profile the user asked for)
    if count <= len(result):return result
    others = sorted(net.compiler_profile_usage(proj), key=lambda p:(-p['matched'], key(p)))
    seen = {key(p) for p in result}
    for profile in others:
        if profile['matched'] > 0 and key(profile) not in seen:
            result.append({'toolchain':profile['toolchain'], 'flags':list(profile['flags'])})
            seen.add(key(profile))
    return result[:count]


def tail_dispatch(net, proj, fn):
    """One installed sibling-call profile for the saved source of a proven tail dispatcher."""
    from .. import consoles, toolchains
    from ..project import compiler_for
    from .wrapper_contracts import tail_dispatch_hint

    if not tail_dispatch_hint(proj, fn):
        return None
    cfg = net.cfg(proj)
    compiler = 'gcc-4.4.0-n64'
    if not any(option.id == compiler for option in consoles.of(cfg).compilers):
        return None
    profile = {'toolchain': compiler, 'flags': consoles.default_flags(cfg, compiler)}
    own = compiler_for(cfg, fn['vram'], fn)
    if profile != _profile(own) and toolchains.installed(compiler):
        return profile
    return None


def probe(search, agent, proj, fn, source, profile, *, scored=False):
    """Cache misses separately by profile; only a reverified exact bundle can change settings. 0 for an exact pair,
    else None, or with `scored` a miss's score (a cached one's too, when its record kept it): what compiler
    detection compares (profile_detection)."""
    from . import agent_tokens, ido_compat
    search.check()
    search.visited += 1
    # (a draft IDO cannot build, for want of a `char *` cursor, is judged as the text that does build: that text is
    # what a match is rechecked with and saved as)
    source = ido_compat.adapted(profile['toolchain'], source)
    scope = agent_tokens.target_scope(search.net, {**dict(fn), 'best_source':source}, '')
    key = 'deep-profile-v1:'+hashlib.sha256(repr((scope, source, profile)).encode()).hexdigest()
    store = search.net.project_store(proj['id'])
    queued = trial_bookkeeping.lookup(store, ('profile-probe', fn['id'], key))
    cached = {'outcome': queued} if queued is not None else store.one(
        'SELECT outcome,feedback FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], key)
    # (a compile failure is as settled as a miss: retrying one recompiled 88 broken rewrites 9,844 times)
    if cached and cached['outcome'] in ('miss', 'compile_failed'):
        search.skipped += 1
        if scored and cached['outcome'] == 'miss' and 'feedback' in cached.keys():
            try:
                return json.loads(cached['feedback'] or '{}').get('score')
            except (TypeError, ValueError):
                return None
        return None
    header=search.net._context(proj['id'],fn['unit'])['header']
    search.tested += 1
    search.report()
    exact, diff, _ = search.net._judge_with(proj, fn, profile['toolchain'], profile['flags'], source)
    score = (0 if exact else max(1, diff.score)) if diff is not None else None
    outcome = 'exact' if exact else 'miss' if score is not None else 'compile_failed'
    # A miss is advisory (it only saves compiling the same text again), so a search queues it with its batch, and a
    # repeat inside the batch finds it there; an exact result is written at once. It was a transaction through the
    # single writer for every candidate: 15% of a lane's CPU on a sampled 29-lane pass.
    trial_bookkeeping.submit(store, [('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                   (fn['id'], key, outcome, 'deep compiler profile probe',
                    json.dumps({'score':score, 'profile':profile, **({'source':source} if exact else {})})))],
                   immediate=bool(exact), memo=(('profile-probe', fn['id'], key), outcome))
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
        from .deep_permuter import SearchLimit
        settings = profile['toolchain']+' '+' '.join(profile['flags'])
        if search.limit-search.tested < 2:
            search.log('Exact profile evidence saved. More compile allowance is needed to reverify and apply it.')
            # (the rest of the allowance can't reverify it either: the next pass starts from the saved evidence)
            raise SearchLimit(f'Exact match found with {settings}; run the pass again to reverify and apply it')
        search.check()
        search.tested += 2
        # (the floating-point register model changes every FP register of the build: the user reviews it, even when
        # automatic compiler changes are on, as with a `-ffast-math` trial)
        from .. import consoles
        from ..project import compiler_for
        from .fp_fingerprints import is_fp_variant
        cfg = search.net.cfg(proj)
        own = compiler_for(cfg, fn['vram'], fn)
        bases = [own['flags']] if own['toolchain'] == profile['toolchain'] else []
        try:
            bases.append(consoles.default_flags(cfg, profile['toolchain']))
        except Exception:  # noqa: BLE001 - a toolchain this install doesn't know has no default to compare with
            pass
        review = {'review_only': True} if any(is_fp_variant(flags, profile['flags']) for flags in bases) else {}
        said = search.net.suggest(agent, proj['slug'], 'Deep permuter found an exact source/compiler profile pair.',
                                  compiler=profile['toolchain'], flags=' '.join(profile['flags']),
                                  function=fn['name'], source=source, **review)
        if search.net._function(fn['name'],proj['slug'])['status'] == 'matched':
            return 0
        # The exact pair waits for the user (automatic compiler changes off) or was refused. Searching on spent the
        # whole budget and reported "0 improvements" over a match waiting on the project page (an N64 function:
        # 10,000 more variants after its exact pair turned up in the targeted repairs).
        search.log(f'Exact match with {settings}, not applied: {said}')
        raise SearchLimit(f'Exact match found with {settings}; it waits for your approval as a compiler '
                          'suggestion on the project page')
    return score if scored else None
