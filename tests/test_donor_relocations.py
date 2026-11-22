"""The fast prefix check preserves matching across different relocation layouts."""

import json
from types import SimpleNamespace

import pytest

from decompnet.app import donor_relocations
from decompnet.app.twins import Job, Print, _key
from decompnet.elfobj import Reloc, mask_relocs
from tests.test_similar_matches import pair as pair_fixture

pair = pytest.fixture(pair_fixture.__wrapped__)


@pytest.mark.parametrize('big', [False, True])
def test_prefix_filter_preserves_relocated_hits_and_rejects_other_code(big, monkeypatch):
    order = 'big' if big else 'little'
    layout = [(0, 4), (12, 5)]  # MIPS jump and HI16
    donor_code = (0x0C000040).to_bytes(4, order) + b'original' + bytes(20)
    target_code = (0x0C001234).to_bytes(4, order) + donor_code[4:]
    masked = mask_relocs(donor_code, [Reloc(o, kind, '', 0) for o, kind in layout], 'mips', big)
    donor = Print(_key(masked, layout), [], 32, prefix=masked[:8].hex(), layout=layout)
    rows = [
        {'id': 1, 'size': 32, 'target': target_code, 'relocs': '[]'},
        {'id': 2, 'size': 32, 'target': target_code, 'relocs': json.dumps([(*pair, 0) for pair in layout])},
        {'id': 3, 'size': 32, 'target': target_code[:8] + b'x' + target_code[9:], 'relocs': '[]'},
    ]
    rows += [{'id': i, 'size': 32, 'target': bytes(32), 'relocs': '[]'} for i in range(4, 104)]
    store = SimpleNamespace(all=lambda *args: rows)
    net = SimpleNamespace(project_store=lambda pid: store)
    proj = {'id': 1, 'arch': 'mips', 'big_endian': big}
    calls = []

    def mask(code, *args):
        calls.append(code)
        return mask_relocs(code, *args)

    monkeypatch.setattr(donor_relocations, 'mask_relocs', mask)
    assert donor_relocations.candidates(net, proj, [donor], Job()) == {donor.key: [1]}
    assert len(calls) == 3  # one prepared mask and two plausible candidates
    donor.prefix = ''
    assert donor_relocations.candidates(net, proj, [donor], Job()) == {donor.key: [1]}


def test_short_layout_is_eligible_only_in_explicit_search():
    code = bytes.fromhex('0c00000000000000')
    layout = [(0, 4)]
    donor = Print(_key(code, layout), ['callee'], 8, prefix=code.hex(), layout=layout)
    rows = [{'id': 1, 'size': 8, 'target': code, 'relocs': '[]'}]
    net = SimpleNamespace(project_store=lambda pid: SimpleNamespace(all=lambda *args: rows))
    proj = {'id': 1, 'arch': 'mips', 'big_endian': True}
    job = Job()
    assert not donor_relocations.candidates(net, proj, [donor], job)
    job.cross_exhaustive = True
    assert donor_relocations.candidates(net, proj, [donor], job) == {donor.key: [1]}


def test_overlay_names_use_loader_relocations_before_compile():
    donor = Print('key', ['external', 'external'], 12, layout=[(0, 5), (4, 6)])
    expected = SimpleNamespace(relocs=[Reloc(4, 6, 'loader_ref', 0), Reloc(0, 5, 'loader_ref', 0)])
    console = SimpleNamespace(export_overlay=lambda _: {})
    net = SimpleNamespace(console=lambda _: console, _rom_target=lambda row: row, _target_obj=lambda *_: expected)
    row = {'name': 'destination'}
    aliases = donor_relocations.overlay_names(net, {}, row, donor, 'donor_entry')
    assert aliases == {'donor_entry': 'destination', 'external': 'loader_ref'}
    expected.relocs[1] = Reloc(0, 4, 'loader_ref', 0)
    assert donor_relocations.overlay_names(net, {}, row, donor, 'donor_entry') is None


def test_short_verified_c_uses_the_destination_name(pair, monkeypatch):
    code = bytes.fromhex('03e0000824020007')
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET target=?,size=8,n_insns=2", (code,))
        db.execute("UPDATE functions SET matched_source='int old(void) { return 7; }' WHERE id=1")

    def judge(agent, proj, donor, target, source, **kwargs):
        assert target['name'] == 'new'
        assert source == 'int new(void) { return 7; }'
        return 'match', ''

    monkeypatch.setattr(pair.match_library, 'reuse', lambda *args: 0)
    monkeypatch.setattr(pair.twins, '_attempt_twin', judge)
    assert pair.twins.across_games('destination', Job()) == 0
    job = Job()
    job.cross_exhaustive = True
    assert pair.twins.across_games('destination', job) == 1
    assert pair.twins.reuse_names('destination', job) == 0
    assert pair._function('new', 'destination')['name'] == 'new'


def test_imported_similarity_prefers_agreeing_operands_when_opcodes_tie():
    from struct import pack

    from decompnet import similarity
    from decompnet.app.imported_similarity import Index

    def code(register, field):
        return pack('>8I', 0x27BDFFE8, 0xAFBF0014, 0x0C000000 | field, 0,
                    0x24020004 | register << 21, 0x8FBF0014, 0x03E00008, 0x27BD0018)

    wanted = code(4, 0x9000)
    entries = [
        ({'id': 1}, Print('close', ['callee'], 32, layout=[(8, 4)]), {'target': code(4, 0x1000)}),
        *[({'id': i}, Print('other', ['callee'], 32, layout=[(8, 4)]), {'target': code(i + 4, 0x9000)})
          for i in (2, 3, 4)],
    ]
    ops = similarity.mips_mnemonics(wanted)
    assert all(similarity.mips_mnemonics(entry['target']) == ops for _, _, entry in entries)
    index = Index(entries, True, '', Job().stop)
    picked = index.shortlist(wanted, ops, relocs=[Reloc(8, 4, 'callee', 0)])
    assert [entry[0]['id'] for entry in picked] == [4, 3, 2, 1]


def test_imported_similarity_accepts_a_larger_body_with_agreeing_operands():
    from struct import pack

    from decompnet import similarity
    from decompnet.app.imported_similarity import Index

    body = [0x24820001, 0xAC620000, 0x24820002, 0xAC620004, 0x24820003, 0xAC620008,
            0x24820004, 0xAC62000C, 0x24820005, 0xAC620010, 0x03E00008, 0x00000000]
    target = pack('>12I', *body)
    donor_code = pack('>17I', *body[:4], *([0x00851025] * 5), *body[4:])
    target_ops = similarity.mips_mnemonics(target)
    donor_ops = similarity.mips_mnemonics(donor_code)
    assert similarity.score(target_ops, donor_ops) < .84
    fingerprint = Print('larger', [], len(donor_code), layout=[])
    index = Index([({'id': 1}, fingerprint, {'target': donor_code})], True, '', Job().stop)
    assert index.shortlist(target, target_ops)[0][0]['id'] == 1
