"""Bounded unattended-session recovery and credential-scoped provider cooldowns."""
import hashlib
import json
import re
import time

# Long analysis-only sessions can evade the existing fast-failure backoff forever.
NO_PROGRESS_SESSIONS = 3


def account_key(session):
    # CLI helpers share their connector's local sign-in. API helpers share only a credential/endpoint pair.
    # A connector whose models belong to separate sign-ins (opencode's providers, each with its own
    # allowance) adds its own scope, so a limit on one account or model never pauses the others.
    # Persist no credential or account identifier in settings keys or messages.
    from .. import agents
    kind = agents.get(session.kind)
    scope = [session.kind, session.options.get('account_id') or 'default']
    if kind.uses_endpoints:
        scope += [session.options.get('base_url') or session.options.get('url') or session.options.get('endpoint') or '',
                  session.options.get('api_key') or '']
    scope.extend(kind.limit_scope(session.model))
    return 'agent-account-cooldown:' + hashlib.sha256(json.dumps(scope).encode()).hexdigest()


def remember_limit(manager, session, until):
    key = account_key(session)
    with manager.net.store.tx() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        prior = float(row['value']) if row else 0
        until = max(prior, until)
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,str(until)))
    return until


def active_limit(manager, session):
    row=manager.net.store.one('SELECT value FROM settings WHERE key=?',account_key(session))
    return bool(row and float(row['value'])>time.time())


def clear_limit(manager, session):
    """A successful retry releases only the cooldown it observed, never a newer limit."""
    if not session.cooldown_observed:return
    with manager.net.store.tx() as db:
        db.execute('DELETE FROM settings WHERE key=? AND CAST(value AS REAL)<=?',
                   (account_key(session),session.cooldown_observed))


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


def diagnostic_completed(net, agent, function="", slug=None):
    """A completed diagnostic build is work even though it intentionally creates no scored attempt."""
    counts = getattr(net, '_session_diagnostic_compiles', None)
    if counts is None:
        counts = net._session_diagnostic_compiles = {}
    counts[agent.id] = counts.get(agent.id, 0) + 1
    if function:
        fn=net._function(function,slug)
        ledger=net.__dict__.setdefault('_function_diagnostic_compiles',{})
        ledger[agent.id,fn['id']]=time.time()
        while len(ledger)>256:
            del ledger[next(iter(ledger))]


def configuration_key(session):
    return (account_key(session), session.model)


def configuration_error(manager, session, reason=None):
    """Share explicit connector/model rejections until restart or a different configuration."""
    with manager._lock:
        errors = getattr(manager, '_configuration_errors', {})
        manager._configuration_errors = errors
        key = configuration_key(session)
        if reason is not None:
            errors[key] = reason
        return errors.get(key)


def investigation_completed(net, agent, slug, tool):
    """Successful structured reports count as investigation, never as compilation or a verified match."""
    proj = net.project_store(slug).one('SELECT id FROM projects WHERE slug=?', slug)
    with net.project_store(slug).tx() as db:
        net._event(db, proj['id'], agent.id, 'investigation', tool)


def investigation_outcome(net, agent, began, *, project=None):
    store = net.project_store(project) if project is not None else net.store
    row = store.one("SELECT detail FROM events WHERE agent_id=? AND kind='investigation' AND created_at>=? ORDER BY id DESC LIMIT 1", agent.id, began)
    return {'report_boundary':'Boundary review submitted', 'report_data_region':'Data investigation completed',
            'report_candidate_issue':'Candidate concern submitted', 'suggest_boundary':'Boundary recommendation submitted'}.get(row['detail']) if row else None


OUTPUT_RECOVERY = (
    'The previous response reached the model output limit and an unfinished tool call may not have reached the app. '
    'Recover live claims with get_claims and read the current candidate and revision with get_candidate before editing. '
    'Use small compile_patch edits with exact anchors and focused edit_context changes. Keep the complete candidate '
    'in the app and submit one bounded change per call. Compile through the ordinary judge after each change. '
    'Report the specific blocker if a required edit cannot fit; saved work remains available.')
