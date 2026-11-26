"""Agents propose set-aside and boundary changes; the user approves each through the app's guarded code (#agent
proposals), and one reviewed step ends a function, keeps stray words out of any function and starts the next one.

The game is homemade: a function that returns, two words that are no MIPS instruction, then a second function's
prologue, all inside one entry (what an N64 session found and could not fix without scripting the database)."""
import json
import struct

import pytest
import rabbitizer

from decompnet.app import agent_proposals as proposals
from decompnet.app import boundary_investigations, tools
from decompnet.app import boundary_repairs as repair
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError
from decompnet.rom.split import AsmLine, RomFunction, function_asm

BASE = 0x80001000
FRAME, STEP, JR_RA, UNFRAME, NOP = 0x27bdffe8, 0x25080001, 0x03e00008, 0x27bd0018, 0
STRAY = [0x7C000000, 0x7FFFFFFF]  # no instruction: what an assembler or linker left between two functions
FIRST = [FRAME, STEP, JR_RA, UNFRAME]
SECOND = [FRAME, STEP, STEP, JR_RA, UNFRAME]
END, ENTRY = BASE + 16, BASE + 24  # where the first function's delay slot ends; the second prologue


def raw(*words):
    return struct.pack('>' + str(len(words)) + 'I', *words)


def _asm(name, vram, words):
    lines = [AsmLine(vram - BASE + 4 * i, vram + 4 * i, w, rabbitizer.Instruction(w, vram + 4 * i).getOpcodeName(),
                     rabbitizer.Instruction(w, vram + 4 * i).disassemble().split(None, 1)[-1]) for i, w in enumerate(words)]
    return function_asm(RomFunction(name, vram, vram - BASE, 'code', lines, None))


@pytest.fixture
def game(tmp_path, monkeypatch):
    """One entry `merged` holding FIRST, STRAY and SECOND, then a matched function after it."""
    words = FIRST + STRAY + SECOND
    image = raw(*words) + raw(JR_RA, NOP)
    (tmp_path / 'rom.z64').write_bytes(image)
    net = RomNetwork(tmp_path / 'db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True, 'gp': 0, 'rom': {'file': 'rom.z64'},
           'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']},
           'segments': [{'name': 'code', 'rom_start': 0, 'rom_end': len(image), 'vram': BASE}]}
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,root,arch,big_endian,config) VALUES(1,'game','Game',?,'mips',1,?)",
                   (str(tmp_path), json.dumps(cfg)))
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at) VALUES(1,1,'',0)")
        for name, offset, piece, status in [('merged', 0, words, 'open'), ('after', 4 * len(words), [JR_RA, NOP], 'matched')]:
            db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,asm,unit,status,matched_source) "
                       "VALUES(1,?,?,?,?,?,?,'[]',?,'code',?,?)",
                       (name, BASE + offset, offset, 4 * len(piece), len(piece), raw(*piece), _asm(name, BASE + offset, piece),
                        status, 'void after(void){}' if status == 'matched' else None))
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,?,?,?,'func')", (name, BASE + offset, 4 * len(piece)))
    monkeypatch.setattr(net, 'regression_test', lambda *args, **kwargs: [])
    agent, _ = net.local_agent('claude-opus-1')
    return net, agent


def _only(net):
    return proposals.listing(net, 'game')['items'][0]


def test_split_function_refuses_the_stray_words_and_points_to_one_reviewed_step(game):
    """The refusal reproduced: a cut at the second prologue leaves the stray words in the first function, a cut at
    the return leaves them at the start of the second; each answer names the proposal that does it in one step."""
    net, agent = game
    with pytest.raises(NetworkError) as refused:
        net.split_function(agent, 'game', 'merged', f'0x{ENTRY:08X}')
    said = str(refused.value)
    assert 'split_gap' in said and f'0x{END:08X}' in said and f'0x{ENTRY:08X}' in said and 'propose_change' in said
    assert net._function('merged', 'game')['size'] == 4 * 11  # nothing changed
    # The cut at the return is allowed; its tail then starts on the stray words, and cutting them off is refused too.
    assert net.split_function(agent, 'game', 'merged', f'0x{END:08X}', 'rest').startswith('✅')
    with pytest.raises(NetworkError, match='trim_start'):
        net.split_function(agent, 'game', 'rest', f'0x{ENTRY:08X}')


def test_split_gap_ends_keeps_the_stray_words_out_and_starts_the_next_function_and_undoes(game):
    net, _ = game
    before = dict(net._function('merged', 'game'))
    plan = repair.preview(net, 'game', 'merged', 'split_gap', END, entry=ENTRY)
    assert (plan['end'], plan['entry'], plan['new_size']) == (END, ENTRY, 16)
    result = repair.apply(net, 'game', 'merged', 'split_gap', plan['revision'], plan['image'], END,
                          'Returns at 0x8000100C; two invalid words; prologue at 0x80001018', entry=ENTRY)
    head = net._function('merged', 'game')
    assert head['size'] == 16 and head['target'] == raw(*FIRST) and head['status'] == 'open'
    pad = net._function(f'pad_{END:08X}', 'game')
    assert pad['gone_mark'] is not None and pad['target'] == raw(*STRAY)  # original bytes kept, outside any function
    assert json.loads(pad['meta'])['boundary_classification'] == 'padding'
    second = net._function(f'func_{ENTRY:08X}', 'game')
    assert second['gone_mark'] is None and second['target'] == raw(*SECOND) and second['status'] == 'open'
    assert net.store.one('SELECT size FROM symbols WHERE name=?', f'func_{ENTRY:08X}')['size'] == 20
    assert not net.store.one('SELECT 1 FROM symbols WHERE name=?', f'pad_{END:08X}')
    history = repair.audit(net, 'game')['history']
    assert [(h['action'], h['entry']) for h in history] == [('split_gap', ENTRY)]
    repair.undo(net, 'game', result['key'])
    assert dict(net._function('merged', 'game')) == before
    assert net._function(f'func_{ENTRY:08X}', 'game')['gone_mark'] is not None
    assert not net.store.one('SELECT 1 FROM symbols WHERE name=?', f'func_{ENTRY:08X}')


@pytest.mark.parametrize('end,entry', [(END, END), (ENTRY, END), (BASE + 18, ENTRY), (END, BASE + 44)])
def test_split_gap_needs_an_entry_after_the_end_inside_the_function(game, end, entry):
    net, _ = game
    with pytest.raises(NetworkError):
        repair.preview(net, 'game', 'merged', 'split_gap', end, entry=entry)


def test_an_agent_proposes_the_split_and_only_your_approval_changes_it(game):
    net, agent = game
    said = tools.call(net, agent, 'game', 'propose_change',
                      {'function': 'merged', 'action': 'split_gap', 'end': f'0x{END:08X}', 'entry': f'0x{ENTRY:08X}',
                       'evidence': 'jr $ra at 0x80001008 returns; 0x80001010 and 0x80001014 are no instruction; '
                                   'addiu $sp at 0x80001018 opens a new frame'})
    assert said.startswith('Proposal saved'), said
    assert net._function('merged', 'game')['size'] == 44  # the agent changed nothing
    assert net.overview('game')['proposals'] == 1
    item = _only(net)
    assert item['status'] == 'pending' and not item['stale'] and item['agents'][0]['name'] == 'claude-opus-1'
    # A second agent with the same finding adds its evidence to the same proposal.
    other, _ = net.local_agent('codex-gpt-1')
    assert 'Added your evidence' in proposals.propose(net, other, 'game', 'merged', 'split_gap',
                                                      'The same two words between the return and the prologue',
                                                      f'0x{END:08X}', f'0x{ENTRY:08X}')
    assert len(_only(net)['agents']) == 2 and proposals.listing(net, 'game')['pending'] == 1
    proposals.approve(net, 'game', item['id'])
    assert net._function('merged', 'game')['size'] == 16 and net._function(f'func_{ENTRY:08X}', 'game')['size'] == 20
    assert _only(net)['status'] == 'approved' and _only(net)['undoable']
    assert 'agent proposal (claude-opus-1, codex-gpt-1)' in repair.audit(net, 'game')['history'][0]['evidence']
    with pytest.raises(NetworkError, match='already approved'):
        proposals.approve(net, 'game', item['id'])
    proposals.undo(net, 'game', item['id'])
    assert net._function('merged', 'game')['size'] == 44 and _only(net)['status'] == 'undone'


def test_a_set_aside_proposal_works_by_your_approval_and_is_undone_by_giving_it_back(game):
    net, agent = game
    proposals.propose(net, agent, 'game', 'merged', 'set_aside_sdk', 'A PI DMA driver loop: cache ops on every line, polls PI_STATUS')
    assert net._function('merged', 'game')['status'] == 'open'
    item = _only(net)
    proposals.approve(net, 'game', item['id'])
    fn = net._function('merged', 'game')
    meta = json.loads(fn['meta'])
    assert fn['status'] == 'asm' and 'library code' in meta['asm_code'] and 'PI DMA driver' in meta['asm_code']
    proposals.undo(net, 'game', item['id'])
    assert net._function('merged', 'game')['status'] == 'open'


def test_a_stale_proposal_says_what_changed_and_is_not_applied(game):
    net, agent = game
    proposals.propose(net, agent, 'game', 'merged', 'set_aside', 'Writes COP0 Status directly with mtc0, no C makes it')
    item = _only(net)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE name='merged'")
    assert 'matched now' in _only(net)['stale']
    with pytest.raises(NetworkError, match='matched now'):
        proposals.approve(net, 'game', item['id'])
    assert _only(net)['status'] == 'pending'
    proposals.dismiss(net, 'game', item['id'])
    assert _only(net)['status'] == 'dismissed' and proposals.listing(net, 'game')['pending'] == 0


def test_a_boundary_proposal_is_stale_once_the_extent_moved(game):
    net, agent = game
    proposals.propose(net, agent, 'game', 'merged', 'split_gap', 'A return, two stray words, then a prologue',
                      f'0x{END:08X}', f'0x{ENTRY:08X}')
    item = _only(net)
    net.split_rom_by_you('game', 'merged', f'0x{END:08X}')
    assert 'changed since' in _only(net)['stale']
    with pytest.raises(NetworkError, match='changed since'):
        proposals.approve(net, 'game', item['id'])


def test_proposals_refuse_what_could_not_be_approved(game):
    net, agent = game
    with pytest.raises(NetworkError, match='evidence'):
        proposals.propose(net, agent, 'game', 'merged', 'set_aside', 'short')
    with pytest.raises(NetworkError, match='Choose an action'):
        proposals.propose(net, agent, 'game', 'merged', 'approve', 'Approve it myself, please, right now')
    with pytest.raises(NetworkError, match='only an open function'):
        proposals.propose(net, agent, 'game', 'after', 'set_aside', 'It is the hand-written return of the driver')
    with pytest.raises(NetworkError, match='entry'):
        proposals.propose(net, agent, 'game', 'merged', 'split_gap', 'A return, two stray words, then a prologue', f'0x{END:08X}')
    with pytest.raises(NetworkError, match='matched'):
        proposals.propose(net, agent, 'game', 'after', 'delay_slot', 'The return needs its delay slot here')
    assert proposals.listing(net, 'game')['items'] == []


def test_agents_get_the_proposal_tool_in_both_modes_and_none_that_approves(game):
    assert 'propose_change' in tools.tool_names(False) and 'propose_change' in tools.tool_names(True)
    assert not any(name.startswith(('approve', 'apply_proposal')) for name in tools.HANDLERS)
    from decompnet.agents.api import api_tool_schemas
    schema = next(t['function'] for t in api_tool_schemas() if t['function']['name'] == 'propose_change')
    assert schema['parameters']['required'] == ['function', 'action', 'evidence']


def test_suggest_boundary_outside_an_investigation_points_to_a_proposal(game):
    net, agent = game
    with pytest.raises(NetworkError, match='propose_change'):
        boundary_investigations.report(net, agent, 'game', 'merged', 'split_gap', 's', 'c', 'u', '0x80001008',
                                       f'0x{END:08X}', 'strong', f'0x{ENTRY:08X}')


def test_the_app_lists_previews_and_approves_a_proposal_through_its_routes(game, tmp_path):
    """The drawer's requests: the list with its count, the split_gap preview (end and entry), dismiss and approve as
    a job that follows to the end."""
    import time

    from starlette.testclient import TestClient

    from decompnet.app.server import build

    from .test_app_server import PORT, SECRET, WINDOW
    net, agent = game
    proposals.propose(net, agent, 'game', 'merged', 'split_gap', 'A return, two stray words, then a prologue',
                      f'0x{END:08X}', f'0x{ENTRY:08X}')
    proposals.propose(net, agent, 'game', 'merged', 'set_aside', 'Writes COP0 Status directly with mtc0')
    home = tmp_path / 'home'
    (home / '.decomp').mkdir(parents=True)
    mcp, _, security, _ = build(net, home / '.decomp', PORT, SECRET)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host='127.0.0.1'),
                        base_url=f'http://127.0.0.1:{PORT}', headers={**WINDOW, 'X-Decomp': '1'})
    listed = client.get('/api/agent-proposals?slug=game').json()
    assert listed['pending'] == 2 and all(item['job'] is None for item in listed['items'])
    gap = next(i for i in listed['items'] if i['action'] == 'split_gap')
    aside = next(i for i in listed['items'] if i['action'] == 'set_aside')
    plan = client.post('/api/boundary-repairs', json={'slug': 'game', 'action': 'preview', 'function': 'merged',
                                                       'repair': 'split_gap', 'end': END, 'entry': ENTRY}).json()
    assert (plan['end'], plan['entry']) == (END, ENTRY)
    assert client.post('/api/agent-proposals', json={'slug': 'game', 'action': 'dismiss', 'id': aside['id']}).json()
    job = client.post('/api/agent-proposals', json={'slug': 'game', 'action': 'approve', 'id': gap['id']}).json()['job']
    for _ in range(200):
        state = client.get(f'/api/job?id={job}').json()
        if state['state'] != 'running':
            break
        time.sleep(.05)
    assert state['state'] == 'done', state
    assert net._function('merged', 'game')['size'] == 16
    assert client.get('/api/agent-proposals?slug=game').json()['pending'] == 0
