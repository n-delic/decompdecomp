"""Bounded unattended-session recovery and credential-scoped provider cooldowns."""
import hashlib
import json
import re
import time


def account_key(session):
    # CLI helpers share their connector's local sign-in. API helpers share only a credential/endpoint pair.
    # Persist no credential or account identifier in settings keys or messages.
    from .. import agents
    kind = agents.get(session.kind)
    scope = [session.kind]
    if kind.uses_endpoints:
        scope += [session.options.get('base_url') or session.options.get('url') or session.options.get('endpoint') or '',
                  session.options.get('api_key') or '']
    return 'agent-account-cooldown:' + hashlib.sha256(json.dumps(scope).encode()).hexdigest()


def remember_limit(manager, session, until):
    key = account_key(session)
    with manager.net.store.tx() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        prior = float(row['value']) if row else 0
        until = max(prior, until)
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,str(until)))
    return until


def cooldown(manager, session):
    row = manager.net.store.one('SELECT value FROM settings WHERE key=?',account_key(session))
    until = float(row['value']) if row else 0
    if until <= time.time() or until <= session.cooldown_observed:
        return None
    return until


def stagger(session):
    return (int(session.id) % 16) * 2


def premature(manager, session, agent, compiles):
    """One fresh-session nudge, never after a provider/context stop or without eligible work."""
    if (session.options.get('task') == 'readable' or session.continuation_attempts >= 1
        or session.rotated or session.stop.is_set() or session.limited or session.throttled or session.auth_failed):
        return False
    budget = session.context_budget
    if budget and (budget.calls >= budget.tool_limit // 2 or budget.chars >= budget.char_limit // 2):
        return False
    reply = session.last_message.lower()
    if re.search(r'context|turn limit|usage limit|no eligible|nothing (?:left|available)|blocked|missing (?:compiler|header|input)',reply):
        return False
    if compiles and not (compiles < 2 and re.search(r'low effort|effort setting|stopped (?:here|there)|rather than claiming',reply)):
        return False
    return bool(manager.net.selection_remaining(session.slug,agent))


CONTINUATION = ('The previous session ended early while eligible work remained. Claims, releases and notes do not '
                'count as compilation. Make an evidence-backed attempt with the ordinary compiler, or run a bounded '
                'try_local_repairs pass, then continue to another eligible function while capacity remains. '
                'Low effort or difficulty alone is not a reason to stop. If inputs are unavailable, explain the '
                'specific blocker and release the claim; do not invent changes or compile merely to meet a quota.')


def diagnostic_completed(net, agent):
    """A completed diagnostic build is work even though it intentionally creates no scored attempt."""
    counts = getattr(net, '_session_diagnostic_compiles', None)
    if counts is None:
        counts = net._session_diagnostic_compiles = {}
    counts[agent.id] = counts.get(agent.id, 0) + 1
