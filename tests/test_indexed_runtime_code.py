import yaml

from decompnet.consoles.n64 import code, indexed_runtime_code, loader_code


def test_archive_programs_append_without_changing_cartridge_and_scope_shared_slots(tmp_path, monkeypatch):
    original = bytes(0x1000)
    (tmp_path/'game.z64').write_bytes(original)
    config = tmp_path/'game.yaml'
    config.write_text(yaml.safe_dump(dict(options={'target_path':'game.z64'},segments=[
        dict(name='main',type='code',start=0,vram=0x80000400,subsegments=[[0,'c','main']]),[len(original)]])))
    rows = [dict(index=i,compressed=True,vram=0x80100000,ram_end=0x80110000) for i in (1,2)]
    monkeypatch.setattr(indexed_runtime_code.indexed_runtime_archive,'members',lambda *_:rows)
    monkeypatch.setattr(indexed_runtime_code.indexed_runtime_archive,'unpack',lambda *_:bytes(0x2000))
    monkeypatch.setattr(code,'_looks_like_code',lambda _:True)
    def bodies(image, segments, banks, taken, log, **kwargs):
        start,_,vram=banks[0]
        return [(start+i*16,start+i*16+8,vram+i*16) for i in range(4)]
    monkeypatch.setattr(loader_code,'complete',bodies)
    result = indexed_runtime_code.add(config,tmp_path,'game.z64',original,[(0,0x100,0x80000400)],lambda *_:None)
    assert result
    assert (tmp_path/result).read_bytes()[:len(original)] == original
    cfg=yaml.safe_load(config.read_text())
    programs=[s for s in cfg['segments'] if isinstance(s,dict) and s.get('name','').startswith('archive_program_')]
    assert len(programs)==2
    assert programs[0]['exclusive_ram_id']==programs[1]['exclusive_ram_id']
    assert programs[0]['start']>=len(original)+16


def test_invalid_member_leaves_config_and_image_unchanged(tmp_path,monkeypatch):
    original=bytes(0x1000)
    config=tmp_path/'game.yaml'
    config.write_text('unchanged')
    monkeypatch.setattr(indexed_runtime_code.indexed_runtime_archive,'members',lambda *_:[
        dict(index=1,compressed=True,vram=0x80100000,ram_end=0x80110000)])
    monkeypatch.setattr(indexed_runtime_code.indexed_runtime_archive,'unpack',lambda *_:b'bad')
    assert indexed_runtime_code.add(config,tmp_path,'game.z64',original,[(0,0x100,0x80000400)],lambda *_:None) is None
    assert config.read_text()=='unchanged'
    assert not list(tmp_path.glob('*.archive.z64'))


def test_archive_reconstruction_rejects_changed_code_and_cartridge(monkeypatch):
    original = bytes(32)
    rows = [dict(index=1,compressed=True,vram=0x80100000,ram_end=0x80101000)]
    monkeypatch.setattr(indexed_runtime_code,'program_rows',lambda _:rows)
    monkeypatch.setattr(indexed_runtime_code.indexed_runtime_archive,'unpack',lambda *_:b'ABCD')
    expanded = original+bytes(16)+b'ABCD'+bytes(12)
    assert indexed_runtime_code.verify_analysis(original,expanded)
    assert not indexed_runtime_code.verify_analysis(original,b'X'+expanded[1:])
    assert not indexed_runtime_code.verify_analysis(original,expanded[:48]+b'X'+expanded[49:])
    assert not indexed_runtime_code.verify_analysis(original,expanded[:-1])
