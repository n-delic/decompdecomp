"""SDK identity uses homemade PowerPC objects; no SDK or game bytes are test fixtures."""

import base64
import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from decompnet import consoles
from decompnet.app import sdk_library, sdk_names
from decompnet.app.engine import RomNetwork
from decompnet.app.sdk_identity import identify
from decompnet.app.twins import object_prints
from decompnet.consoles.gc_wii import sdk

WORDS = [0x9421FFF0, 0x7C0802A6, 0x90010014, 0x38600007, 0x38800009,
         0x48000001, 0x80010014, 0x7C0803A6, 0x38210010, 0x4E800020]


def elf_object(words=WORDS, *, name='func_80001000', reloc_kind=10, symbol_size=None):
    """A relocatable big-endian PPC function with one external call, assembled by hand."""
    code = struct.pack('>'+'I'*len(words), *words)
    strings = b'\0'+name.encode()+b'\0func_80002000\0'
    symbols = bytes(16) + struct.pack('>IIIBBH', 1, 0, len(code) if symbol_size is None else symbol_size, 0x12, 0, 1)
    symbols += struct.pack('>IIIBBH', len(name)+2, 0, 0, 0x12, 0, 0)
    names = b'\0.text\0.symtab\0.strtab\0.shstrtab\0.rela.text\0'
    reloc = struct.pack('>IIi', 20, (2 << 8) | reloc_kind, 0)
    data = bytearray(52)
    sections = [bytes(40)]
    for label, typ, flags, raw, link, info, align, entsize in [
            ('.text',1,6,code,0,0,4,0), ('.symtab',2,0,symbols,3,1,4,16),
            ('.strtab',3,0,strings,0,0,1,0), ('.shstrtab',3,0,names,0,0,1,0),
            ('.rela.text',4,0,reloc,2,1,4,12)]:
        data.extend(bytes((-len(data)) % align))
        offset = len(data); data.extend(raw)
        sections.append(struct.pack('>10I', names.index(label.encode()), typ, flags, 0, offset, len(raw), link, info, align, entsize))
    data.extend(bytes((-len(data)) % 4)); section_offset = len(data)
    data.extend(b''.join(sections))
    ident = b'\x7fELF\x01\x02\x01'+bytes(9)
    data[:52] = ident + struct.pack('>HHIIIIIHHHHHH', 1, 20, 1, 0, 0, section_offset, 0, 52, 0, 0, 40, 6, 4)
    return bytes(data)


def pattern(name='SDKExample', words=WORDS):
    masks = [0xFFFFFFFF]*len(words); masks[5] = 0xFC000003
    data = b''.join(struct.pack('>II', word & mask, mask) for word, mask in zip(words,masks))
    return {'symbol':0, 'hash':hashlib.sha1(data).hexdigest(), 'signature':base64.b64encode(data).decode(),
            'symbols':[{'name':name,'kind':'Function','size':len(words)*4}, {'name':'SDKHelper','kind':'Function','size':0}],
            'relocations':[{'offset':20,'kind':'PpcRel24','symbol':1,'addend':0}]}


@pytest.fixture
def corpus(tmp_path):
    folder = tmp_path/'pack'/'signatures'; folder.mkdir(parents=True)
    (folder/'example.yml').write_text(yaml.safe_dump([pattern()]))
    sdk.patterns.cache_clear()
    return folder.parent


def test_relocated_whole_function_matches_and_references_remain_leads(corpus):
    patterns = sdk.patterns(corpus/'signatures','test')
    words = WORDS.copy(); words[5] = 0x48012345
    result = sdk.identify_object(elf_object(words), patterns)['func_80001000']
    assert result[0]['name'] == 'SDKExample'
    assert result[0]['references'] == [{'target':'func_80002000','name':'SDKHelper','offset':20}]
    assert 'func_80002000' not in sdk.identify_object(elf_object(words), patterns)


@pytest.mark.parametrize('change', ['instruction','short','long','truncated','relocation','ambiguous','tiny'])
def test_incomplete_or_ambiguous_functions_never_establish_identity(corpus, change):
    patterns = sdk.patterns(corpus/'signatures','test')
    words = WORDS.copy(); options = {}
    if change == 'instruction':words[3] ^= 1
    if change == 'short':words.pop()
    if change == 'long':words.append(0x60000000)
    if change == 'truncated':options['symbol_size'] = 44
    if change == 'relocation':options['reloc_kind'] = 11
    if change == 'ambiguous':
        (corpus/'signatures'/'other.yml').write_text(yaml.safe_dump([pattern('DifferentMeaning')]))
        sdk.patterns.cache_clear(); patterns = sdk.patterns(corpus/'signatures','test')
    if change == 'tiny':words = [0x4E800020]
    assert sdk.identify_object(elf_object(words, **options), patterns) == {}


@pytest.fixture(params=['gc','wii'])
def sdk_project(tmp_path, monkeypatch, corpus, request):
    root = tmp_path/'project'; root.mkdir()
    target = root/'target.o'; target.write_bytes(elf_object())
    net = RomNetwork(tmp_path/'app.db',tmp_path)
    cfg = {'kind':'fork','platform':request.param,'version':'demo'}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,big_endian,config) VALUES(?,?,?,?,?,?)',
                         ('demo','Demo',str(root),'ppc',1,json.dumps(cfg))).lastrowid
        fid = db.execute('INSERT INTO functions(project_id,name,unit,size,n_insns,meta) VALUES(?,?,?,?,?,?)',
                         (pid,'func_80001000','main/auto',40,10,json.dumps({'symbol':'func_80001000'}))).lastrowid
    monkeypatch.setattr(sdk.toolchains,'ensure',lambda *args,**kw:corpus)
    monkeypatch.setattr(sdk,'ForkProject',lambda _:SimpleNamespace(root=root,unit=lambda _:SimpleNamespace(target=target)))
    monkeypatch.setattr(net.twins,'prints',lambda _: {fid:object_prints(target.read_bytes())['func_80001000']})
    return net, pid, fid, target


def test_scanner_reaches_shared_identity_map_queue_and_library_selection(sdk_project, monkeypatch):
    net,pid,fid,target = sdk_project
    proj = net.project_row('demo')
    assert identify(net,proj,{}) == {}
    net.console(proj).scan_sdk(net,proj)
    found = identify(net,proj,{})[fid]
    assert found['names'] == ['SDKExample'] and found['corpora'] == [sdk.CORPUS]
    assert found['binary_evidence'][0]['method'] == 'direct'
    assert found['binary_evidence'][0]['target_sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert net.function_map('demo')[0]['sdk']['names'] == ['SDKExample']
    row = net.store.one('SELECT name,status FROM functions WHERE id=?',fid)
    assert row['name'] == 'func_80001000' and row['status'] != 'matched'
    state = sdk_library.state(net,'demo')
    assert state['libraries'][-1]['detected'] == 1 and state['libraries'][-1]['builtin']
    sdk_library.choose(net,'demo',[])
    assert identify(net,net.project_row('demo'),{}) == {}
    assert sdk_library.state(net,'demo')['libraries'][-1]['detected'] == 1
    sdk_library.choose(net,'demo',[sdk.CORPUS])
    assert identify(net,net.project_row('demo'),{})[fid]['names'] == ['SDKExample']
    # Cached identities are rejected immediately when an externally rebuilt target changes.
    target.write_bytes(elf_object(WORDS[:-1]))
    assert identify(net,net.project_row('demo'),{}) == {}
    net.console(proj).scan_sdk(net,proj)
    assert 'sdk_identity' not in json.loads(net.store.one('SELECT meta FROM functions WHERE id=?',fid)['meta'])


def test_native_identity_uses_guarded_rename_and_keeps_evidence(sdk_project, monkeypatch):
    net,pid,fid,target = sdk_project
    calls = []
    monkeypatch.setattr(net,'add_note',lambda *args:None)
    monkeypatch.setattr(net,'fork_slug',lambda _: 'demo')
    def rename(agent,slug,old,new,message):
        calls.append((old,new))
        with net.store.tx() as db:db.execute('UPDATE functions SET name=? WHERE id=?',(new,fid))
        return '✅ Renamed'
    monkeypatch.setattr(net,'rename_symbol',rename)
    assert sdk_names.name_functions(net,'demo') == 1
    assert calls == [('func_80001000','SDKExample')]
    row = net.store.one('SELECT * FROM functions WHERE id=?',fid)
    meta = json.loads(row['meta'])
    assert meta['naming']['binary_evidence'][0]['hash'] == pattern()['hash']
    assert row['status'] != 'matched'
    sdk_library.choose(net,'demo',[])
    assert net.sdk_functions(net.project_row('demo')) == {}


def test_named_functions_keep_their_name_but_gain_sdk_evidence(sdk_project, monkeypatch):
    net,pid,fid,target = sdk_project
    with net.store.tx() as db:db.execute('UPDATE functions SET name=? WHERE id=?',('deliberateName',fid))
    monkeypatch.setattr(net,'rename_symbol',lambda *args:pytest.fail('must preserve deliberate names'))
    assert sdk_names.name_functions(net,'demo') == 0
    assert net.sdk_functions(net.project_row('demo'))[fid]['names'] == ['SDKExample']


def test_cancelled_or_disabled_scans_never_install_the_pack(sdk_project, monkeypatch):
    net,pid,fid,target = sdk_project
    monkeypatch.setattr(sdk.toolchains,'ensure',lambda *args,**kw:pytest.fail('no installation'))
    proj = net.project_row('demo')
    net.console(proj).scan_sdk(net,proj,SimpleNamespace(stop=SimpleNamespace(is_set=lambda:True)))
    sdk_library.choose(net,'demo',[])
    net.console(proj).scan_sdk(net,net.project_row('demo'))


def test_other_consoles_use_local_archives_without_a_native_scan(tmp_path):
    assert not consoles.get('n64').sdk_corpus
    assert consoles.get('n64').scan_sdk(None,None) is None


def test_pinned_corpus_parses_when_installed():
    from decompnet import toolchains
    if not toolchains.installed(sdk.PACK):
        pytest.skip('needs the public dtk-signatures pack')
    corpus = sdk.patterns(toolchains.path(sdk.PACK)/'signatures',toolchains.get(sdk.PACK).source)
    assert sum(len(rows) for rows in corpus.values()) > 100


def test_repeated_target_bodies_do_not_get_an_sdk_identity(sdk_project, monkeypatch):
    net,pid,fid,target = sdk_project
    second = target.with_name('second.o');second.write_bytes(elf_object(name='func_80003000'))
    with net.store.tx() as db:
        db.execute('INSERT INTO functions(project_id,name,unit,size,n_insns,meta) VALUES(?,?,?,?,?,?)',
                   (pid,'func_80003000','rel/auto',40,10,json.dumps({'symbol':'func_80003000'})))
    monkeypatch.setattr(sdk,'ForkProject',lambda _:SimpleNamespace(root=target.parent,
                        unit=lambda unit:SimpleNamespace(target=second if unit=='rel/auto' else target)))
    proj = net.project_row('demo')
    net.console(proj).scan_sdk(net,proj)
    assert not net.store.one("SELECT 1 FROM functions WHERE json_extract(meta,'$.sdk_identity') IS NOT NULL")


@pytest.mark.parametrize("extra_name", [None, "SDKExample"])
def test_conflicting_local_archive_requires_review(sdk_project, extra_name):
    net,pid,fid,target = sdk_project
    proj = net.project_row('demo');net.console(proj).scan_sdk(net,proj)
    fingerprint = object_prints(target.read_bytes())['func_80001000'].key
    with net.store.tx() as db:
        db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                   ('Local SDK','ppc',1,'DifferentMeaning',fingerprint,40))
        if extra_name:
            db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                       ('Other SDK','ppc',1,extra_name,fingerprint,40))
    assert identify(net,net.project_row('demo'),{}) == {}


def test_contradictory_named_reference_rejects_a_generic_wrapper(corpus):
    data = elf_object().replace(b'func_80002000', b'OtherHelper__')
    assert sdk.identify_object(data,sdk.patterns(corpus/'signatures','test')) == {}


def test_sdk_details_show_direct_evidence_and_reference_uncertainty():
    import shutil
    import subprocess

    node = shutil.which('node')
    if not node:
        pytest.skip('needs Node for GUI helper tests')
    source = (Path(__file__).parents[1]/'src/decompnet/app/static/js/function.js').read_text()
    helper = source[source.index('function sdkEvidenceText('):source.index('async function refreshFn(')]
    script = "const assert = require('node:assert/strict');\n"+helper+'''
assert.equal(sdkEvidenceText(null), '');
const text = sdkEvidenceText({names:['SDKExample'], evidence:'whole function', binary_evidence:[{
  source:'dtk v1.8.4',hash:'abc',unit:'module/auto',symbol:'original',target_sha256:'123',
  references:[{target:'unknown',name:'SDKHelper'}]}]});
assert.match(text,/not a verified C match/);
assert.match(text,/pattern abc/);
assert.match(text,/module\\/auto/);
assert.match(text,/not independently identified/);
'''
    subprocess.run([node,'-e',script],check=True,capture_output=True)


def test_folder_indexing_never_starts_native_scans_for_idle_saved_projects(sdk_project, monkeypatch):
    net,pid,fid,target = sdk_project
    with net.store.tx() as db:
        db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                   ('Local SDK','ppc',1,'Example','key',40))
    calls = []
    monkeypatch.setattr(sdk_names,'name_functions',lambda net,slug:calls.append(slug))
    net.project_activity = SimpleNamespace(active=lambda _:False)
    sdk_names.name_projects(net)
    assert not calls
    net.project_activity.active = lambda _:True
    sdk_names.name_projects(net)
    assert calls == ['demo']


def test_weak_masked_patterns_are_rejected_even_when_the_function_size_matches(corpus):
    row = pattern();data=bytes(80)
    row['signature']=base64.b64encode(data).decode();row['hash']=hashlib.sha1(data).hexdigest()
    (corpus/'signatures'/'example.yml').write_text(yaml.safe_dump([row]))
    assert sdk.patterns(corpus/'signatures','weak') == {}


def test_projects_without_sdk_evidence_never_open_target_objects(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path/'app.db',tmp_path)
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                   ('demo','Demo',str(tmp_path),json.dumps({'kind':'rom','platform':'n64'})))
    monkeypatch.setattr(net.twins,'prints',lambda _:pytest.fail('no corpus or binary evidence'))
    assert identify(net,net.project_row('demo'),{}) == {}
    monkeypatch.setattr(net,'fork_slug',lambda _:pytest.fail('do not synchronize a project without SDK evidence'))
    assert sdk_names.name_functions(net,'demo') == 0
