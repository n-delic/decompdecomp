"""Use a homemade body injected into the fingerprint pack; never commit SDK bytes."""
import hashlib
import json

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.app.sdk_assembly import discover_known
from decompnet.app.sdk_names import name_functions
from decompnet.app.source_assembly import detail, verified
from decompnet.consoles.n64 import N64
from decompnet.consoles.n64 import sdk_assembly as pack
from decompnet.rom import Picked, split

BODY=bytes.fromhex('03e0000800000000')  # homemade return stub, not a real pack signature


@pytest.fixture
def toy_pack(monkeypatch):
    digest=hashlib.sha256(BODY).hexdigest()
    monkeypatch.setattr(pack,'KNOWN',{digest:('sdkToy',8,'test fixture','toy.s')})
    monkeypatch.setattr(pack,'SIZES',(8,))
    monkeypatch.setattr(N64,'sdk_assembly_sizes',(8,))


def import_toy(tmp_path,monkeypatch,duplicate=False,body=BODY):
    net=RomNetwork(tmp_path/'db',tmp_path)
    def fake_split(workdir,*args,**kwargs):
        functions=[]
        for index in range(2 if duplicate else 1):
            addr=0x80001000+index*len(body)
            lines=[split.AsmLine(index*len(body)+i,addr+i,int.from_bytes(body[i:i+4],'big'),'nop','') for i in range(0,len(body),4)]
            functions.append(split.RomFunction('func_'+f'{addr:08X}',addr,index*len(body),'code',lines,workdir/'toy.s'))
        return split.SplitResult(functions,[],[split.Segment('code',0,2*len(body),0x80001000)],{},0,'','','')
    monkeypatch.setattr(N64,'split_game',fake_split)
    monkeypatch.setattr(N64,'debug_info',lambda *a:None)
    picked=Picked('n64',body*2,'toy.z64',{'title':'Toy','size':2*len(body),'game_code':'TEST','region':'US','compiler_guess':'KMC','sha1':'toy'},True)
    slug=net.import_split(N64,picked,toolchain='gcc-2.7.2-kmc',flags=['-O2'],log=lambda s:None)
    return net,slug


def test_fresh_import_classifies_and_names_before_agents_without_sdk_files(tmp_path,monkeypatch,toy_pack):
    net,slug=import_toy(tmp_path,monkeypatch)
    fn=net._function('sdkToy',slug);proj=net.project_row(slug)
    assert fn['status']=='asm' and fn['matched_source'] is None
    assert detail(proj,fn)['verified']
    assert net.symbols(proj['id']).resolve('sdkToy')==0x80001000
    assert net.function_detail(slug,'sdkToy',include_hints=False)['sdk']['names']==['sdkToy']
    assert net.claim_row(net.local_agent('worker')[0],slug) is None
    from decompnet.app.source_assembly import breakdown
    assert breakdown(net,proj)['verified_sdk_assembly']==1
    assert breakdown(net,proj)['exact_c']==0


def test_duplicate_entries_keep_unique_symbols_and_sdk_display_names(tmp_path,monkeypatch,toy_pack):
    net,slug=import_toy(tmp_path,monkeypatch,True)
    rows=net.project_store(net.project_row(slug)['id']).all('SELECT name,status FROM functions')
    assert len({r['name'] for r in rows})==2
    assert all(r['status']=='asm' and r['name'].startswith('func_') for r in rows)
    assert all(net.function_detail(slug,r['name'],include_hints=False)['display_name']=='sdkToy' for r in rows)


def test_backfill_verifies_without_signatures_and_preserves_claims_and_operator_choice(tmp_path,monkeypatch,toy_pack):
    net,slug=import_toy(tmp_path,monkeypatch)
    proj=net.project_row(slug);store=net.project_store(proj['id'])
    with store.tx() as db:db.execute("UPDATE functions SET status='open',meta='{}'")
    fn=net._function('sdkToy',slug);agent,_=net.local_agent('other')
    assert net.permuter._hold(agent,fn,count_turn=False,seconds=60)
    assert discover_known(net,proj)==0
    net.permuter._let_go(agent,fn)
    with store.tx() as db:db.execute('UPDATE functions SET meta=?',(json.dumps({'reopened':True}),))
    assert discover_known(net,proj)==0
    with store.tx() as db:db.execute("UPDATE functions SET meta='{}'")
    name_functions(net,slug)
    assert net._function('sdkToy',slug)['status']=='asm'
    fn=net._function('sdkToy',slug)
    changed=dict(fn);changed['target']=BODY[:-1]+b'\1'
    assert not verified(proj,changed)
    with store.tx() as db:db.execute("UPDATE functions SET status='matched'")
    assert discover_known(net,proj)==0


def test_full_bytes_endianness_size_and_no_relocation_requirement(tmp_path,monkeypatch,toy_pack):
    assert pack.identify(BODY)['name']=='sdkToy'
    assert pack.identify(BODY,False) is None
    assert pack.identify(BODY+b'\0') is None
    assert pack.identify(BODY[:-1]+b'\1') is None
    from decompnet.app.sdk_assembly import known_proof
    proj={'config':json.dumps({'kind':'rom','compiler':{'toolchain':'gcc-2.7.2-kmc'}})}
    assert known_proof(N64,proj,{'target':BODY,'relocs':'[[0,4,123]]'}) is None



def test_builtin_pack_metadata_has_complete_body_sizes():
    assert N64.sdk_assembly_sizes == pack.SIZES
    assert {entry[0] for entry in pack.KNOWN.values()} >= {
        'guMtxF2L', 'guMtxL2F', '__osPopThread',
        'osInvalDCache', 'osInvalICache', 'osWritebackDCache', 'bcopy', 'guScaleF',
    }
    assert {entry[1] for entry in pack.KNOWN.values()} | {size for size, _ in pack._MTX_CAT_ASM} == set(pack.SIZES)
    assert all(len(digest)==64 and int(digest,16)>=0 and row[1]%4==0 for digest,row in pack.KNOWN.items())


@pytest.mark.parametrize('duplicate', [False, True])
def test_exact_runtime_helper_is_set_aside_before_claims(tmp_path, monkeypatch, duplicate):
    from decompnet.consoles import n64

    body = bytes.fromhex('24020001') * 11 + BODY  # synthetic leaf body, no game data
    monkeypatch.setattr(n64, '_MUL64_LOW_FIRST_SHA256', hashlib.sha256(body).hexdigest())
    assert N64.known_assembly_name(body[:-1]) is None
    assert N64.known_assembly_name(body[:-1] + b'\1') is None
    net, slug = import_toy(tmp_path, monkeypatch, duplicate, body)
    proj = net.project_row(slug)
    rows = net.project_store(proj['id']).all('SELECT name,status,meta FROM functions ORDER BY vram')
    assert [row['status'] for row in rows] == ['asm'] * (2 if duplicate else 1)
    if duplicate:
        assert [row['name'] for row in rows] == ['func_80001000', 'func_80001034']
    else:
        assert rows[0]['name'] == 'mul64_low_first'
        assert net.symbols(proj['id']).resolve('mul64_low_first') == 0x80001000
        assert json.loads(rows[0]['meta'])['naming']['original_name'] == 'func_80001000'
    assert net.claim_row(net.local_agent('worker')[0], slug) is None


@pytest.mark.parametrize('single', [False, True])
def test_deep_permuter_sets_aside_sdk_in_existing_project(tmp_path, monkeypatch, toy_pack, single):
    from decompnet.app import deep_permuter
    from decompnet.app.deep_sweep import Sweeps
    from decompnet.app.support import Jobs
    from tests.test_deep_permuter import Log

    net, slug = import_toy(tmp_path, monkeypatch)
    proj = net.project_row(slug)
    store = net.project_store(proj['id'])
    with store.tx() as db:
        db.execute("UPDATE functions SET name='func_80001000',status='open',meta='{}',best_score=50,"
                   "best_source='void func_80001000(void) {}'")
    log = Log()

    def unexpected_search(*args, **kwargs):
        pytest.fail('An exact SDK body reached C candidate search.')

    monkeypatch.setattr(deep_permuter, '_run', unexpected_search)
    if single:
        result = deep_permuter.run(net, slug, 'func_80001000', 1, 10, log)
        assert result['reason'] == 'Identified and set aside as SDK library code'
    else:
        sweeps = Sweeps(net, Jobs())
        monkeypatch.setattr(sweeps, 'search_one', unexpected_search)
        result = sweeps.run(slug, 1, 10, log)
        assert result['functions_tested'] == 0
    assert result['tested'] == 0
    row = net._function('func_80001000', slug)
    assert row['status'] == 'asm' and detail(proj, row)['verified']
    assert net.function_detail(slug, row['name'], include_hints=False)['display_name'] == 'sdkToy'
    assert net.claim_row(net.local_agent('worker')[0], slug) is None


def test_exact_runtime_backfill_requires_the_linked_image_and_library_setting(monkeypatch, tmp_path):
    from decompnet.app.sdk_assembly import choose_c, known_proof
    from decompnet.consoles.n64 import sdk_linked

    image = hashlib.sha1(BODY * 2).hexdigest()
    monkeypatch.setattr(sdk_linked, '_KNOWN', {image: {hashlib.sha256(BODY).hexdigest(): ('runtimeToy', 8)}})
    monkeypatch.setattr(sdk_linked, 'SIZES', (8,))
    monkeypatch.setattr(N64, 'sdk_linked_sizes', (8,))
    net, slug = import_toy(tmp_path, monkeypatch)
    proj = net.project_row(slug)
    fn = net._function('runtimeToy', slug)
    assert fn['status'] == 'asm'
    assert detail(proj, fn)['from_c'] and detail(proj, fn)['verified']
    assert known_proof(N64, proj, dict(fn, relocs='[[0,4,2147487744]]'))
    wrong_image = net.cfg(proj)
    wrong_image['rom']['analysis_sha1'] = '0' * 40
    assert known_proof(N64, dict(proj, config=json.dumps(wrong_image)), fn) is None
    assert choose_c(net, slug, False) == 1
    assert discover_known(net, net.project_row(slug)) == 0
    assert net._function('runtimeToy', slug)['status'] == 'open'
    choose_c(net, slug, True)
    assert discover_known(net, net.project_row(slug)) == 1


def test_sdk_matrix_rebuild_variant_checks_full_reference_free_body(monkeypatch):
    from decompnet.app.sdk_assembly import known_proof

    body = b'\x01' * 240
    key = hashlib.sha1(body + b'[]').hexdigest()
    monkeypatch.setattr(pack, '_MTX_CAT_ASM', {(240, key): 'fixture'})
    proj = {'config': json.dumps({'kind': 'rom', 'compiler': {'toolchain': 'gcc-2.7.2-kmc'}})}
    fn = {'vram': 0x80001000, 'size': len(body), 'target': body, 'relocs': '[]'}
    assert known_proof(N64, proj, fn)['name'] == 'guMtxCatF'
    assert known_proof(N64, proj, dict(fn, target=b'\0' + body[1:])) is None
    assert known_proof(N64, proj, dict(fn, relocs='[[0,4,2147487744]]')) is None


def test_linked_sdk_assembly_survives_the_library_c_opt_out(monkeypatch):
    from decompnet.app.sdk_assembly import known_proof
    from decompnet.consoles.n64 import sdk_linked

    image = '1' * 40
    digest = hashlib.sha256(BODY).hexdigest()
    monkeypatch.setattr(sdk_linked, '_LIBULTRA', {image: {digest: ('__osSchedulerToy', 8, False, 'os/exceptasm.s')}})
    monkeypatch.setattr(sdk_linked, 'SIZES', (8,))
    monkeypatch.setattr(N64, 'sdk_linked_sizes', (8,))
    cfg = {'kind': 'rom', 'rom': {'analysis_sha1': image}, 'sdk_set_aside_c': False,
           'compiler': {'toolchain': 'gcc-2.7.2-kmc'}}
    fn = {'vram': 0x80001000, 'size': 8, 'target': BODY, 'relocs': '[[0,4,2147487744]]'}
    proof = known_proof(N64, {'config': json.dumps(cfg)}, fn)
    assert proof['name'] == '__osSchedulerToy' and not proof['from_c']
    cfg['rom']['analysis_sha1'] = '2' * 40
    assert known_proof(N64, {'config': json.dumps(cfg)}, fn) is None


def test_scheduler_split_requires_exact_image_and_combined_body(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from decompnet.consoles.n64 import sdk_linked

    start = 0x80001000
    words = [0x24020001] * 139  # synthetic words, no game data
    words[94] = 0x42000018
    words[96:99] = [0x0c000040, 0, 0]
    code = b''.join(word.to_bytes(4, 'big') for word in words)
    image = '1' * 40
    monkeypatch.setattr(sdk_linked, '_LINKED_IMAGE', image)
    monkeypatch.setattr(sdk_linked, '_MERGED_SCHEDULER', hashlib.sha256(code).hexdigest())
    lines = [split.AsmLine(i * 4, start + i * 4, word, '', '') for i, word in enumerate(words)]
    fn = split.RomFunction('func_80001000', start, 0, 'code', lines, tmp_path / 'toy.s')
    result = SimpleNamespace(functions=[fn])
    sdk_linked.split_scheduler(result, '2' * 40)
    assert result.functions == [fn]
    original = lines[-1].word
    lines[-1].word ^= 1
    sdk_linked.split_scheduler(result, image)
    assert result.functions == [fn]
    lines[-1].word = original
    sdk_linked.split_scheduler(result, image)
    assert [(f.vram, f.size) for f in result.functions] == [
        (start, 380), (start + 380, 12), (start + 396, 160),
    ]
    assert result.functions[0].lines[-1].word == 0x42000018
    assert result.functions[1].lines[-1].word == 0  # keep the cleanup call's delay slot


def test_builtin_register_helpers_are_scoped_to_their_linked_image():
    from decompnet.consoles.n64 import sdk_linked

    names = {'__osDisableInt', '__osRestoreInt', '__osGetCause', 'osGetCount',
             '__osGetSR', '__osSetCompare', '__osSetSR', '__osSetWatchLo'}
    entries = sdk_linked._LIBULTRA[sdk_linked._LINKED_IMAGE]
    assert names <= {name for name, _, _, _ in entries.values()}
    # The SDK's own libultra builds (2.0I to 2.0L) give some of these accessors' bodies too, so those are known in
    # every game; the linked-image entries stay for the bodies the archives don't have.
    for digest, (name, *_rest) in entries.items():
        if digest in pack.KNOWN:  # the same body, found in the SDK's archives too: the two sources agree on its name
            assert pack.KNOWN[digest][0] == name
    for name, size, from_c, _ in entries.values():
        if name in names:
            assert size in N64.sdk_linked_sizes and not from_c


@pytest.mark.parametrize('stub', [[0x0c000040, 0x24040000], [0x24040000, 0x0c000040, 0]])
def test_scheduler_cleanup_preserves_delay_slot_and_explicit_padding_entry(monkeypatch, tmp_path, stub):
    from types import SimpleNamespace

    from decompnet.consoles.n64 import sdk_linked

    start = 0x80001000
    words = [0x24020001] * 94 + [0x42000018, *stub, 0]
    raw = b''.join(word.to_bytes(4, 'big') for word in words)
    image = '3' * 40
    monkeypatch.setitem(sdk_linked._CLEANUP_WITH_PADDING, image, hashlib.sha256(raw).hexdigest())
    lines = [split.AsmLine(i*4, start+i*4, word, '', '') for i, word in enumerate(words)]
    fn = split.RomFunction('dispatcher', start, 0, 'code', lines[:-1], tmp_path/'toy.s')
    end = 4*(len(words)-1)
    padding = split.RomFunction('padding', start+end, end, 'alignment', lines[-1:], tmp_path/'toy.s')
    result = SimpleNamespace(functions=[fn, padding], named={'entry':padding.vram})
    sdk_linked.split_scheduler(result, image)
    assert result.functions == [fn, padding]
    result.named.clear()
    sdk_linked.split_scheduler(result, '2' * 40)
    assert result.functions == [fn, padding]
    sdk_linked.split_scheduler(result, image)
    assert [(f.rom, f.size) for f in result.functions] == [(0,380),(380,len(stub)*4)]
    assert [line.word for f in result.functions for line in f.lines] == words[:-1]


# The cartridge's entry stub: BSS clear with `addi`, then a jump to main with $sp set in the delay slot. Another
# game's copy differs in its addresses only.
ENTRY_STUB = bytes.fromhex('3C08800D25087050' '3C090004252906F0' 'AD000000AD000004' '21080008' '2129FFF8' '1520FFFB' '00000000'
                           '3C0A8000254A0460' '3C1D8010' '01400008' '27BD2CA0')


def test_entry_stub_is_told_by_its_shape_not_its_addresses():
    other_game = bytearray(ENTRY_STUB)
    other_game[2:4] = b'\x80\x2a'  # a different BSS address
    other_game[0x32:0x34] = b'\x80\x20'  # a different stack top
    for code in (ENTRY_STUB, ENTRY_STUB + bytes(36), bytes(other_game)):
        assert pack.identify(code)['name'] == 'entrypoint'
    swapped = bytearray(ENTRY_STUB)
    swapped[0x18:0x1c] = bytes.fromhex('25080008')  # addiu where the boot code has addi
    assert pack.identify(bytes(swapped)) is None
    assert pack.identify(ENTRY_STUB[:-8]) is None
    assert pack.identify(ENTRY_STUB, big_endian=False) is None


def test_deep_search_labels_the_entry_stub_as_sdk_code(monkeypatch, tmp_path):
    from decompnet.app import deep_permuter
    from decompnet.app.source_assembly import detail

    net = RomNetwork(tmp_path / 'db', tmp_path)
    body = ENTRY_STUB
    def fake_split(workdir, *args, **kwargs):
        lines = [split.AsmLine(i, 0x80000400 + i, int.from_bytes(body[i:i + 4], 'big'), 'nop', '') for i in range(0, len(body), 4)]
        fn = split.RomFunction('func_80000400', 0x80000400, 0, 'code', lines, workdir / 'toy.s')
        return split.SplitResult([fn], [], [split.Segment('code', 0, len(body), 0x80000400)], {}, 0, '', '', '')
    monkeypatch.setattr(N64, 'split_game', fake_split)
    monkeypatch.setattr(N64, 'debug_info', lambda *a: None)
    # (a body of this size is in the table now, so an import names it; a project imported before is what's tested)
    monkeypatch.setattr(N64, 'sdk_assembly_sizes', tuple(size for size in pack.SIZES if size != len(body)))
    info = {'title': 'Toy', 'size': len(body), 'game_code': 'TEST', 'region': 'US', 'compiler_guess': 'KMC', 'sha1': 'toy',
            'entrypoint': 0x80000400}
    slug = net.import_split(N64, Picked('n64', body, 'toy.z64', info, True), toolchain='gcc-2.7.2-kmc', flags=['-O2'], log=lambda s: None)
    proj = net.project_row(slug)
    assert not (detail(proj, net._function('func_80000400', slug)) or {}).get('verified')  # import only set it aside as assembly
    with net.project_store(proj['id']).tx() as db:  # as in a project imported before that rule: still open
        db.execute("UPDATE functions SET status='open', meta=NULL WHERE name='func_80000400'")

    class Log:
        def __call__(self, line): pass
        def check_cancel(self): pass
    monkeypatch.setattr(deep_permuter, '_run', lambda *a, **k: pytest.fail('searched the boot stub'))
    result = deep_permuter.run(net, slug, 'func_80000400', 1, 10, Log())
    assert result['reason'] == 'Identified and set aside as SDK library code'
    row = net._function('func_80000400', slug)
    assert row['status'] == 'asm' and detail(proj, row)['kind'] == 'sdk'
    assert net.function_detail(slug, row['name'], include_hints=False)['display_name'] == 'entrypoint'


def test_other_libultra_builds_of_the_cache_routines_are_in_the_table():
    # osInvalICache and its siblings were open for agents in games whose libultra assembled them differently
    # (the digest table held one build's bytes). No SDK bytes here: the table, its sizes and a body that isn't one.
    from decompnet.consoles.n64 import sdk_assembly as table
    names = {}
    for digest, (name, size, variant, reference) in table.KNOWN.items():
        assert len(digest) == 64 and size % 4 == 0 and size in table.SIZES and reference.endswith('.s')
        names.setdefault(name, []).append(size)
    for name in ('osInvalICache', 'osInvalDCache', 'osWritebackDCache', 'osWritebackDCacheAll'):
        assert name in names
    assert len(table.KNOWN['713e7b78373df6fbf3d030b5237e9c1e2148c9443cbfdf938f2e8ffdf8e2d326'][0]) and 40 in table.SIZES
    assert table.identify(bytes(116)) is None and table.identify(bytes(40)) is None


def test_sdk_archive_fingerprints_are_unambiguous_and_cover_every_build():
    """The table read from the SDK's libultra archives (2.0I to 2.0L): one name per body, and the routines every
    port replaces (cache, coprocessor 0 and TLB accessors, the interrupt mask pair, the MGU matrix helpers)."""
    names = {row[0] for row in pack.KNOWN.values()}
    assert names >= {'osGetCount', '__osGetSR', '__osSetSR', '__osDisableInt', '__osRestoreInt', '__osProbeTLB',
                     'osMapTLB', 'osUnmapTLBAll', 'guMtxIdent', 'guTranslate', 'guScale', 'guNormalize', 'sqrtf',
                     'bzero', 'bcmp', 'osDelay'}
    assert all(not name.startswith('_b') or name.startswith('__') for name in names)  # _bcopy/_bzero are bcopy/bzero
    assert all(row[1] in pack.SIZES and row[3].endswith('.s') for row in pack.KNOWN.values())
