"""One bounded advisory investigation, using the normal provider/session lifecycle without matching tools."""
import json
import threading
import time

from ..network import NetworkError
from . import boundary_evidence, boundary_repairs

TASK = 'boundary'
TOOLS = frozenset({'project_info', 'get_boundary_evidence', 'suggest_boundary'})
ACTIONS = frozenset({'delay_slot', 'join_previous', 'adjust', 'padding', 'data', 'keep', 'unclear'})
PREFIX = 'boundary-suggestion:'
INSTRUCTIONS = ('You are a boundary investigator in decompdecomp, reviewing a game the user owns. '
                'Use only project_info(detail=false), get_boundary_evidence and suggest_boundary. '
                'Read original evidence, then submit one advisory recommendation and stop. '
                'Do not claim, compile, edit or apply layout changes. The user makes the boundary decision. '
                'The game code is private: never publish it. Evidence strength is not proof or a probability.')


def configure(net, slug, options, count):
    """Validate mapped evidence and required tools before spending a provider turn."""
    if options.get('task') != TASK: return options
    if count != 1 or options.get('focus_function'):
        raise ValueError('Choose one agent and one function for a boundary investigation.')
    name = options.get('boundary_function')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Choose a function to investigate.')
    from .tool_policy import disabled
    if disabled(net) & (TOOLS - {'project_info'}):
        raise ValueError('Enable get_boundary_evidence and suggest_boundary in agent tool settings before investigating.')
    boundary_evidence.read(net, slug, name)
    return {**options, 'boundary_function': name, 'boundary_until': time.time()+600, 'per_session': 1}


def bind(net, session, agent):
    if session.options.get('task') == TASK:
        net.__dict__.setdefault('_boundary_investigators', {})[agent.id] = session


def scope(net, agent):
    return getattr(net, '_boundary_investigators', {}).get(agent.id)


def guard(net, agent, slug, name, args):
    """Enforce the advisory tool scope even when a CLI requests a hidden matching tool."""
    session = scope(net, agent)
    if not session: return
    if session.stop.is_set() or time.time() >= session.options['boundary_until']:
        raise NetworkError('This boundary investigation has ended. Start another review if needed.')
    if slug != session.slug or name not in TOOLS:
        raise NetworkError('Boundary investigators may only read boundary evidence and submit an advisory suggestion.')
    if name != 'project_info' and args.get('function') != session.options['boundary_function']:
        raise NetworkError('Investigate only the selected function.')
    if name == 'project_info' and args.get('detail'):
        raise NetworkError('Use project_info(detail=false), then get_boundary_evidence for the selected function.')


def evidence(net, agent, slug, function):
    packet = boundary_evidence.read(net, slug, function)
    if session := scope(net, agent):
        session.boundary_evidence = packet
    return json.dumps(packet)


def _key(session):
    return PREFIX + session.id


def report(net, agent, slug, function, action, supporting, counter, uncertainty, citations, end='', confidence='tentative'):
    """Save a recommendation against the original evidence; this never applies a layout edit."""
    session = scope(net, agent)
    if not session:
        raise NetworkError('Start a boundary investigation from Review function boundaries first.')
    guard(net, agent, slug, 'suggest_boundary', {'function': function})
    packet = getattr(session, 'boundary_evidence', None)
    if not packet:
        raise NetworkError('Read get_boundary_evidence before suggesting a decision.')
    current = boundary_evidence.read(net, slug, function)
    if any(packet[k] != current[k] for k in ('revision', 'image')):
        raise NetworkError('The original image or project changed. Read fresh boundary evidence before reporting.')
    if action not in ACTIONS or confidence not in {'strong', 'tentative', 'unclear'}:
        raise NetworkError('Choose a known repair or unclear, with strong, tentative or unclear evidence.')
    if any(not isinstance(v, str) or not v.strip() or len(v) > 4000 for v in (supporting, counter, uncertainty, citations)):
        raise NetworkError('Provide supporting evidence, counter-evidence, uncertainty and cited hex addresses (up to 4000 characters each).')
    known = {line['address'] for line in packet['assembly']} | {e['from'] for e in packet['edges']}
    try:
        addresses = list(dict.fromkeys(int(value.strip(), 16) for value in citations.split(',')))
    except ValueError:
        raise NetworkError('Cite comma-separated hex instruction addresses from the evidence.') from None
    if not addresses or not set(addresses) <= known:
        raise NetworkError('Cite instruction addresses present in the supplied evidence.')
    try: proposed_end = int(end, 16) if action == 'adjust' else None
    except (TypeError, ValueError):
        raise NetworkError('Supply the proposed exclusive end as a hex address.') from None
    # A read-only preview tests layout feasibility, not ownership. Claims are enforced
    # again when the user previews/applies; investigation must never steal a claim.
    if action != 'unclear':
        boundary_repairs.preview(net, slug, function, action, proposed_end, investigation=True)
    item = {'function': function, 'action': action, 'end': proposed_end, 'confidence': confidence,
            'supporting': supporting, 'counter': counter, 'uncertainty': uncertainty,
            'citations': addresses, 'agent': session.label, 'model': session.model,
            'time': time.time(), 'revision': packet['revision'], 'image': packet['image']}
    with net.project_store(slug).tx() as db:
        if db.execute('SELECT 1 FROM settings WHERE key=?', (_key(session),)).fetchone():
            raise NetworkError('This investigation already saved its recommendation. Start another investigation for a second opinion.')
        db.execute('INSERT INTO settings VALUES(?,?)', (_key(session), json.dumps({**item, 'slug': slug})))
    return 'Advisory boundary recommendation saved for user review. No boundary or matching status changed. End this investigation now.'


def suggestions(net, slug, packet):
    """Keep advice attributable and visible after restart, with changed inputs labelled stale."""
    items = []
    for row in net.project_store(slug).all('SELECT value FROM settings WHERE key LIKE ?', PREFIX+'%'):
        item = json.loads(row['value'])
        if item['slug'] == slug and item['function'] == packet['function']:
            items.append({**item, 'stale': any(item[k] != packet[k] for k in ('revision', 'image'))})
    return sorted(items, key=lambda item: item['time'], reverse=True)[:10]


def reported(net, session):
    return bool(net.project_store(session.slug).one('SELECT 1 FROM settings WHERE key=?', _key(session)))


def run(manager, session):
    """The ten-minute deadline includes provider waits and survives app restarts."""
    finished = threading.Event()
    def completion():
        if reported(manager.net, session): return 'Boundary recommendation saved; review it in Review function boundaries.'
        if time.time() >= session.options['boundary_until']: return 'Boundary investigation time limit reached. No layout change was applied.'
        return None
    if why := completion():
        manager._finish(session, why); return
    def watch():
        while not finished.wait(.5) and not session.stop.is_set():
            if why := completion():
                manager.stop(session.id, by='itself')
                manager._finish(session, why)
                return
    threading.Thread(target=watch, daemon=True, name='boundary-investigation-deadline').start()
    try: return manager._run_work(session)
    finally: finished.set()


def prompt(session):
    return (f"Investigate only the original function boundary for {session.options['boundary_function']}. "
            "First call project_info(detail=false) to check the connection, then get_boundary_evidence. "
            "Compare missing delay slots, joining the preceding entry, adjusting the exclusive end, keeping an intentional entry, "
            "and non-code classification. Use original instructions, direct callers/branches, mapping boundaries and symbols. "
            "Small functions and shared tails can be valid. Adjacency, generated symbols, absent callers, successful safety checks "
            "or model agreement do not prove ownership. The edge scan is bounded and indirect references are unresolved. "
            "Submit suggest_boundary once with an action, exact exclusive end for adjust, evidence strength, supporting evidence, "
            "counter-evidence, remaining uncertainty and comma-separated hex instruction citations from the supplied packet. "
            "If ambiguous, choose unclear and explain what evidence is missing. Do not claim, compile, edit or apply changes. "
            "After the report, end your response; the app will stop this one-shot investigation.")
