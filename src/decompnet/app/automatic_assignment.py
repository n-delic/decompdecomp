"""Hand the next matching claim to an opted-in worker at its tool boundary."""
from contextlib import nullcontext

from ..network import NetworkError
from ..read_cache import ReadPending
from . import session_budget

RELEASE = 'release_and_continue'
HANDOFF_TOOLS = frozenset({RELEASE, 'compile_and_diff', 'compile_patch', 'compile_family',
                           'compile_variants', 'try_local_repairs', 'try_compiler',
                           'compiler_experiments'})
INSTRUCTIONS = (
    'Automatic assignment is enabled. Work on the single function the app assigns. The app returns your next '
    'brief after a verified match or an accepted release. Use release_and_continue with the current claim token '
    'and optional findings when you release. If a release is declined, keep working on the current function. '
    'Recover your current assignment with get_claims if you lose its brief. Follow the app’s handoff or '
    'end-of-session message whenever older workflow guidance tells you to claim another function.'
)


def configured(options):
    return options.get('automatic_assignment') is True and not (
        options.get('focus_id') or options.get('focus_function') or options.get('task'))


def bind(net, agent, session):
    sessions = net.__dict__.setdefault('_automatic_assignment', {})
    if configured(session.options):
        sessions[agent.id] = session
    else:
        sessions.pop(agent.id, None)


def enabled(net, agent, slug=None):
    session = getattr(net, '_automatic_assignment', {}).get(getattr(agent, 'id', None))
    return session is not None and (slug is None or session.slug == slug)


def serialize(net, agent, slug, name):
    # Two tool calls for the same claim can arrive in one model turn. Keep their
    # judge/release and handoff ordered, before either takes a candidate lock.
    if name in HANDOFF_TOOLS and enabled(net, agent, slug):
        budget = session_budget.current(net, agent, slug)
        if budget:
            return budget.assignment_lock
    return nullcontext()


def held(net, agent, slug):
    row = net.project_store(slug).one(
        'SELECT c.function_id, c.claimed_at FROM claims c JOIN functions f ON f.id=c.function_id '
        'JOIN projects p ON p.id=f.project_id WHERE c.agent_id=? AND p.slug=? ORDER BY c.claimed_at LIMIT 1',
        agent.id, slug)
    return tuple(row) if row else None


def available(net, agent, slug, budget, *, completed=0, response=''):
    session = net._automatic_assignment[agent.id]
    from .agents import PER_SESSION
    return (not session.stop.is_set() and session.state == 'working'
            and budget.assignments + completed < session.options.get('per_session', PER_SESSION)
            and budget.calls + 1 < budget.tool_limit and budget.chars + len(response) < budget.char_limit)


def claim(net, agent, slug):
    from .tools import _claim
    return _claim(net, agent, slug, '', '', count=1)


def after_tool(net, agent, slug, budget, before, name, handshake, text):
    if handshake and name == 'project_info':
        if net._automatic_assignment[agent.id].stop.is_set():
            return text + '\nThe worker was stopped.'
    else:
        if before is None:
            return text
        current = held(net, agent, slug)
        if current == before:
            return text
        budget.assignments += 1
        if current:
            return text  # A release with a prerequisite already supplied its brief.
        if not available(net, agent, slug, budget, response=text):
            budget.rotate = True
            return text + '\nThis session is complete. The app will choose work for the next session.'
    try:
        packet = claim(net, agent, slug)
    except (NetworkError, ReadPending) as error:
        # The previous judgement/release is already committed. Retrying that
        # tool would duplicate work, so let the runner retry the assignment.
        budget.rotate = True
        return text + f'\nAssignment deferred: {error}. The app will retry in the next session.'
    if packet.startswith('Work availability:'):
        budget.waiting = True
        return text + '\n\n' + packet.removesuffix(' Explicit named claims remain available.')
    return text + '\n\nAutomatic assignment:\n' + packet
