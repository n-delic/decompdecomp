"""Independently linked images must never turn passthrough or stale code into C matches."""
import hashlib
from io import BytesIO

import pytest
from elftools.elf.elffile import ELFFile

from decompnet.arch.mips_image import linked_object
from decompnet.forks import linkedimages
from decompnet.forks.project import ForkError


def population(root):
    raw = bytes.fromhex('03e00008000000002402000103e00008')
    (root/'original.bin').write_bytes(raw)
    (root/'source.c').write_text('int f(void) { return 0; }')
    return dict(id='game', label='Game', image='original.bin', sha256=hashlib.sha256(raw).hexdigest(),
                base=0x80000000, functions=[dict(name='f', address=0x80000000, size=8, body=raw[:8],
                  source_checksums={'source.c': hashlib.sha256((root/'source.c').read_bytes()).hexdigest()}),
                  dict(name='g', address=0x80000008, size=8)])


def test_exports_original_reference_and_only_source_backed_candidates(tmp_path):
    pop = population(tmp_path)
    entries = linkedimages.export(tmp_path, [pop], object_writer=linked_object)
    assert len(entries) == 2
    assert 'base_path' in entries[0] and 'base_path' not in entries[1]
    assert not entries[0]['metadata'].get('source_path')  # special whole-program groups are read-only
    with (tmp_path/entries[0]['target_path']).open('rb') as f:
        elf = ELFFile(f)
        symbol = elf.get_section_by_name('.symtab').get_symbol(1)
        assert symbol.name == 'f' and symbol['st_value'] == 0x80000000 and symbol['st_size'] == 8
        assert elf.get_section_by_name('.text').data() == (tmp_path/'original.bin').read_bytes()[:8]
    assert not linkedimages.stale(tmp_path, entries[0], {})
    (tmp_path/'source.c').write_text('changed')
    assert linkedimages.stale(tmp_path, entries[0], {})
    (tmp_path/'source.c').unlink()
    assert linkedimages.stale(tmp_path, entries[0], {})


@pytest.mark.parametrize('bad', ['hash', 'overlap', 'body', 'provenance', 'source', 'escape', 'duplicate'])
def test_rejects_invalid_images_before_writing(tmp_path, bad):
    pop = population(tmp_path)
    fn = pop['functions'][0]
    if bad == 'hash': pop['sha256'] = '0'*64
    elif bad == 'overlap': pop['functions'][1]['address'] -= 4
    elif bad == 'body': fn['body'] = bytes(8)
    elif bad == 'provenance': fn['source_checksums'] = {}
    elif bad == 'source': (tmp_path/'source.c').write_text('changed')
    elif bad == 'escape': pop['image'] = '../outside'
    elif bad == 'duplicate': pop['functions'][1]['name'] = 'f'
    with pytest.raises(ForkError):
        linkedimages.export(tmp_path, [pop], object_writer=linked_object)
    assert not (tmp_path/'.decomp-wtf/linked-images').exists()


def test_images_with_shared_addresses_stay_distinct(tmp_path):
    pop = population(tmp_path)
    other = dict(pop, id='runtime', label='Runtime')
    entries = linkedimages.export(tmp_path, [pop, other], object_writer=linked_object)
    assert len({u['name'] for u in entries}) == 4
    assert len({u['target_path'] for u in entries}) == 4


def test_population_percent_is_byte_weighted(tmp_path):
    import json
    (tmp_path/'objdiff.json').write_text(json.dumps({'units': [
        {'name':'a'}, {'name':'b', 'metadata':{'progress_population':'Runtime'}}]}))
    rep = {'units':[{'name':'a','functions':[{'name':'big','size':100,'fuzzy_match_percent':100}]},
                    {'name':'b','functions':[{'name':'small','size':8,'fuzzy_match_percent':0}]}]}
    rows = linkedimages.populations(tmp_path, rep, set())
    assert rows[0]['matched_code'] == 100
    assert rows[1]['matched_code'] == 0 and rows[1]['total_functions'] == 1


def test_object_can_be_read_by_elf_parser():
    elf = ELFFile(BytesIO(linked_object('f', 0x80380000, bytes(8))))
    assert elf['e_machine'] == 'EM_MIPS'


def test_real_objdiff_counts_passthrough_and_invalidates_stale_c(tmp_path):
    import json

    from decompnet import toolchains
    from decompnet.forks import objdiff
    try:
        toolchains.require('objdiff-cli')
    except Exception as exc:
        pytest.skip(f'objdiff unavailable: {exc}')
    pop = population(tmp_path)
    entries = linkedimages.export(tmp_path, [pop], object_writer=linked_object)
    (tmp_path/'objdiff.json').write_text(json.dumps({'build_base':False, 'build_target':False, 'units':entries}))
    rep = objdiff.report(tmp_path, tmp_path/'report.json')
    assert rep['measures']['total_functions'] == 2
    assert rep['measures']['matched_functions'] == 1
    (tmp_path/'source.c').write_text('changed after the linked image was built')
    rep = objdiff.report(tmp_path, tmp_path/'report.json')
    assert rep['measures']['total_functions'] == 2
    assert rep['measures'].get('matched_functions', 0) == 0


def test_individual_diff_invalidates_stale_candidate(tmp_path, monkeypatch):
    import json

    from decompnet.forks import objdiff
    from decompnet.forks.project import ForkProject
    pop = population(tmp_path)
    entries = linkedimages.export(tmp_path, [pop], object_writer=linked_object)
    (tmp_path/'objdiff.json').write_text(json.dumps({'units':entries}))
    project = ForkProject(tmp_path)
    seen = []
    monkeypatch.setattr(objdiff, 'diff', lambda target, base, *a, **kw: seen.append(base))
    project.diff(project.unit(entries[0]['name']), 'f')
    assert seen[-1] is not None
    (tmp_path/'source.c').write_text('changed')
    project.diff(project.unit(entries[0]['name']), 'f')
    assert seen[-1] is None
