"""Changes an agent proposes and the user approves with one click: setting a function aside as hand-written assembly
or SDK code, and a boundary change (the actions of Review function boundaries, boundary_repairs).

Why: in an N64 session agents found driver code that should be set aside and an entry that swallowed two functions
(a return, two stray words that are no instruction, then the next prologue), and could not act on it. A single agent's
`not_c` waits for agents of other models, split_function refused the stray words, suggest_boundary needs an
investigation the user starts, and the coordinator ended up scripting the database. Here the agent records what it
found with its evidence (`propose_change`) and nothing changes; the user sees the pending proposals under "Waiting for
you" and approves or dismisses each one. Approving runs the app's own guarded code, the same a person's click runs:
Network.set_aside_by_you, or a fresh boundary_repairs.preview and apply (revision and image checks, the matches that
refer into the changed extent reverified), so it can be undone the same way ("Give it back to the agents", Repair
history), and from the proposal itself.

A proposal remembers the bytes, extent and status of the functions it is about. When they changed (a match landed, a
boundary moved, someone set it aside), it is stale: approving says so, and the user dismisses it or asks again.

Set aside works in every mode and console (the functions table is the same). Boundary changes are mapped MIPS ROM
projects only, as Review function boundaries is (boundary_repairs._supported says so to the agent); a project-mode
game's extents are its build's (ROADMAP.md, "Agents propose changes").
"""
from __future__ import annotations

import hashlib
import json
import time

from ..network import NetworkError, model_of
from . import boundary_repairs

PREFIX = 'agent-proposal:'
SET_ASIDE = {'set_aside': False, 'set_aside_sdk': True}  # action: Network.set_aside_by_you's `library`
BOUNDARY = ('delay_slot', 'join_previous', 'adjust', 'padding', 'data', 'keep', 'trim_start', 'split_end', 'split_gap')
ACTIONS = (*SET_ASIDE, *BOUNDARY)
WITH_END = ('adjust', 'trim_start', 'split_end', 'split_gap')
LABELS = {'set_aside': 'Set aside: hand-written assembly', 'set_aside_sdk': 'Set aside: SDK or library code',
          'delay_slot': 'Include the missing delay slot', 'join_previous': 'Join with the preceding function',
          'adjust': 'Move the end', 'padding': 'Archive as padding', 'data': 'Archive as data',
          'keep': 'Keep as a separate entry', 'trim_start': 'Start later: archive the words before the entry',
          'split_end': 'Split off the next function', 'split_gap': 'End here, keep stray words out, start the next function'}
EVIDENCE_MIN, EVIDENCE_MAX = 20, 4000
KEEP_DECIDED = 50  # decided proposals listed, newest first


def job_kind(slug: str, pid: str) -> str:
    return f'Approve agent proposal: {slug}:{pid}'


def _address(text, what):
    if text in (None, ''):
        return None
    try:
        value = int(str(text).strip(), 16)
    except ValueError:
        raise NetworkError(f'`{what}` must be a hex address like 0x80001234.') from None
    if value % 4:
        raise NetworkError(f'`{what}` must be an aligned instruction address.')
    return value


def _row_print(row) -> dict:
    target = bytes(row['target'] or b'')
    return {'id': row['id'], 'name': row['name'], 'vram': row['vram'], 'size': row['size'], 'status': row['status'],
            'gone': row['gone_mark'] is not None, 'target': hashlib.sha256(target).hexdigest()}


def _function(net, slug, name):
    try:
        return net._function(name, slug)
    except NetworkError:
        return None


def _key(proj, pid):
    return f'{PREFIX}{proj["id"]}:{pid}'


def _items(net, proj):
    store = net.project_store(proj['id'])
    return [(r['key'], json.loads(r['value'])) for r in
            store.all('SELECT key,value FROM settings WHERE key LIKE ?', f'{PREFIX}{proj["id"]}:%')]


def _save(net, proj, key, item):
    with net.project_store(proj['id']).tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(item)))


def propose(net, agent, slug, function, action, evidence, end='', entry=''):
    """The agent tool: record a proposal with its evidence; nothing in the project changes."""
    if action not in ACTIONS:
        raise NetworkError('Choose an action: ' + ', '.join(ACTIONS) + '.')
    evidence = ' '.join(str(evidence or '').split())
    if not EVIDENCE_MIN <= len(evidence) <= EVIDENCE_MAX:
        raise NetworkError(f'Give the evidence the user reviews ({EVIDENCE_MIN}–{EVIDENCE_MAX} characters): the '
                           'instructions and addresses that show it, and what you could not rule out.')
    if net._link(slug) is not None:
        raise NetworkError("In a team room the host's project decides this: tell the host in a note (add_note).")
    fn = net._function(function, slug)
    proj = net._project(slug)
    if fn['gone_mark'] is not None:
        raise NetworkError(f'{fn["name"]} is archived; there is nothing to change.')
    end_at, entry_at = _address(end, 'end'), _address(entry, 'entry')
    if action in SET_ASIDE:
        allowed = ('open', 'matched') if SET_ASIDE[action] else ('open',)
        if fn['status'] not in allowed:
            raise NetworkError(f'{fn["name"]} is {fn["status"]}: only an open function can be set aside'
                               + (' (a matched one only as SDK or library code: set_aside_sdk)' if fn['status'] == 'matched' else '') + '.')
        prints, extent = [_row_print(fn)], None
    else:
        if action in WITH_END and end_at is None:
            raise NetworkError(f'{action} needs `end`: ' + ('the new exclusive end' if action == 'adjust' else
                               "where this function's last instruction ends" if action == 'split_gap' else
                               'the real entry inside the function') + ', a hex address.')
        if action == 'split_gap' and entry_at is None:
            raise NetworkError('split_gap needs `entry`: where the next function starts, after the stray words.')
        # The same read-only preview an investigation uses: it says now whether the change is possible (MIPS ROM
        # projects only, mapped bytes, no overlap, nothing matched) without needing the agent's claim released.
        plan = boundary_repairs.preview(net, slug, fn['name'], action, end_at, entry=entry_at, investigation=True)
        store = net.project_store(proj['id'])
        prints = [_row_print(store.one('SELECT * FROM functions WHERE id=?', piece['id'])) for piece in plan['affected']]
        extent = {k: plan[k] for k in ('owner', 'start', 'end', 'old_end', 'entry', 'new_size', 'old_size')}
    said = {'model': model_of(agent.name), 'evidence': evidence, 't': time.time()}
    for key, item in _items(net, proj):
        if (item['status'] == 'pending' and item['function'] == fn['name'] and item['action'] == action
                and item.get('end') == end_at and item.get('entry') == entry_at and not _stale(net, slug, item)):
            item['agents'][agent.name] = said
            _save(net, proj, key, item)
            return (f'Added your evidence to the pending proposal for {fn["name"]} ({len(item["agents"])} agents). '
                    'Nothing changed: the user approves or dismisses it. Release the function and work on another.')
    pid = format(time.time_ns(), 'x')
    item = {'id': pid, 'function': fn['name'], 'action': action, 'end': end_at, 'entry': entry_at, 'extent': extent,
            'prints': prints, 'agents': {agent.name: said}, 't': time.time(), 'status': 'pending'}
    _save(net, proj, _key(proj, pid), item)
    with net.project_store(proj['id']).tx() as db:
        net._event(db, proj['id'], agent.id, 'proposal', f'{fn["name"]}: {LABELS[action]} (waiting for you)')
    return (f'Proposal saved: {LABELS[action]} for {fn["name"]}. Nothing changed; the user reviews your evidence and '
            'approves or dismisses it under "Waiting for you". Release the function and work on another.')


def _stale(net, slug, item) -> str:
    """Why a proposal no longer describes the project, or ''."""
    for saved in item['prints']:
        fn = _function(net, slug, saved['name'])
        if fn is None or fn['id'] != saved['id']:
            return f'{saved["name"]} no longer exists as it was (renamed or removed). Dismiss this proposal.'
        now = _row_print(fn)
        if now['gone']:
            return f'{saved["name"]} was archived since the agent proposed this. Dismiss this proposal.'
        if now['status'] != saved['status']:
            return (f'{saved["name"]} is {now["status"]} now (it was {saved["status"]} when the agent proposed this). '
                    'Dismiss this proposal, or ask an agent to look again.')
        if (now['vram'], now['size'], now['target']) != (saved['vram'], saved['size'], saved['target']):
            return (f'{saved["name"]} changed since the agent proposed this: its start, size or bytes differ. '
                    'Dismiss this proposal, or ask an agent to look again.')
    return ''


def listing(net, slug) -> dict:
    """{'pending': n, 'items': [...]} for the drawer: every pending proposal and the newest decided ones."""
    proj = net._project(slug)
    items = []
    for _, item in _items(net, proj):
        public = {k: item.get(k) for k in ('id', 'function', 'action', 'end', 'entry', 'extent', 't', 'status',
                                           'decided', 'result', 'error')}
        public['label'] = LABELS.get(item['action'], item['action'])
        public['kind'] = 'set_aside' if item['action'] in SET_ASIDE else 'boundary'
        public['agents'] = [{'name': name, **said} for name, said in sorted(item['agents'].items(), key=lambda kv: kv[1]['t'])]
        public['stale'] = _stale(net, slug, item) if item['status'] == 'pending' else ''
        public['undoable'] = item['status'] == 'approved' and bool(item.get('result'))
        items.append(public)
    pending = [i for i in items if i['status'] == 'pending']
    decided = sorted((i for i in items if i['status'] != 'pending'), key=lambda i: i.get('decided') or i['t'], reverse=True)
    return {'pending': len(pending), 'items': sorted(pending, key=lambda i: i['t'], reverse=True) + decided[:KEEP_DECIDED]}


def pending_count(net, proj) -> int:
    return sum(item['status'] == 'pending' for _, item in _items(net, proj))


def _find(net, slug, pid):
    proj = net._project(slug)
    key = _key(proj, str(pid))
    row = net.project_store(proj['id']).one('SELECT value FROM settings WHERE key=?', key)
    if row is None:
        raise NetworkError('That proposal is no longer listed. Refresh the list.')
    return proj, key, json.loads(row['value'])


def _why(item) -> str:
    names = ', '.join(item['agents'])
    text = ' / '.join(said['evidence'] for said in item['agents'].values())
    return f'agent proposal ({names}), approved by you: {text}'


def approve(net, slug, pid, log=print) -> dict:
    """Apply a pending proposal through the app's own guarded code. Raises with what to do when it is stale."""
    proj, key, item = _find(net, slug, pid)
    if item['status'] != 'pending':
        raise NetworkError(f'This proposal was already {item["status"]}.')
    if why := _stale(net, slug, item):
        raise NetworkError(why)
    action = item['action']
    if action in SET_ASIDE:
        log(f'Setting {item["function"]} aside…')
        net.set_aside_by_you(item['function'], slug, _why(item)[:1500], library=SET_ASIDE[action])
        result = {'function': item['function']}
    else:
        plan = boundary_repairs.preview(net, slug, item['function'], action, item['end'], entry=item['entry'])
        extent = {k: plan[k] for k in item['extent']}
        if extent != item['extent']:
            raise NetworkError('The layout around this function changed since the agent proposed it. Dismiss this '
                               'proposal, or review the function in Review function boundaries.')
        result = boundary_repairs.apply(net, slug, item['function'], action, plan['revision'], plan['image'],
                                        item['end'], _why(item), log, entry=item['entry'])
    item.update(status='approved', decided=time.time(), result=result)
    _save(net, proj, key, item)
    log('Approved. It can be undone from this list' + (' or Repair history.' if action not in SET_ASIDE else
                                                       ' or the function page (Give it back to the agents).'))
    return {'approved': item['id'], 'result': result}


def dismiss(net, slug, pid) -> dict:
    proj, key, item = _find(net, slug, pid)
    if item['status'] != 'pending':
        raise NetworkError(f'This proposal was already {item["status"]}.')
    item.update(status='dismissed', decided=time.time())
    _save(net, proj, key, item)
    return {'dismissed': item['id']}


def undo(net, slug, pid, log=print) -> dict:
    """Take back an approved proposal the way a person's own decision is taken back."""
    proj, key, item = _find(net, slug, pid)
    if item['status'] != 'approved' or not item.get('result'):
        raise NetworkError('Only an approved proposal can be undone.')
    if item['action'] in SET_ASIDE:
        fn = _function(net, slug, item['function'])
        if fn is None or fn['status'] != 'asm':
            raise NetworkError(f'{item["function"]} is no longer set aside; there is nothing to undo.')
        net.reopen(item['function'], slug)
    else:
        boundary_repairs.undo(net, slug, item['result']['key'], log)
    item.update(status='undone', decided=time.time())
    _save(net, proj, key, item)
    log('Undone.')
    return {'undone': item['id']}
