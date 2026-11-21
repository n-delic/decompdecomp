"""The fast prefix check preserves matching across different relocation layouts."""

import json
from types import SimpleNamespace

import pytest

from decompnet.app import donor_relocations
from decompnet.app.twins import Job, Print, _key
from decompnet.elfobj import Reloc, mask_relocs


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
