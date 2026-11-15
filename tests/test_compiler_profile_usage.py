from types import SimpleNamespace

from decompnet.app.engine.compilers import CompilerMixin


def test_usage_counts_effective_profiles_in_project_with_range_precedence():
    cfg = {"compiler": {"toolchain": "gcc", "flags": ["-O1", "-g3"]}, "compiler_ranges": [
        {"start": 10, "end": 20, "toolchain": "gcc", "flags": ["-g3", "-O1"]},
        {"start": 15, "end": 30, "toolchain": "other", "flags": []},
    ]}
    calls = []

    def rows(sql, project):
        calls.append((sql, project))
        return [{"vram": vram, "status": status} for vram, status in
                [(None, "open"), (0, "matched"), (10, "matched"), (16, "open"), (20, "matched"), (30, "open")]]

    engine = SimpleNamespace(cfg=lambda proj: cfg, store=SimpleNamespace(all=rows))
    engine.project_store = lambda _: engine.store
    result = CompilerMixin.compiler_profile_usage(engine, {"id": 7})
    assert result == [
        {"toolchain": "gcc", "flags": ["-O1", "-g3"], "functions": 3, "matched": 1},
        {"toolchain": "gcc", "flags": ["-g3", "-O1"], "functions": 2, "matched": 1},
        {"toolchain": "other", "flags": [], "functions": 1, "matched": 1},
    ]
    assert len(calls) == 1 and calls[0][1] == 7
    assert "WHERE project_id=?" in calls[0][0]
    cfg["compiler"]["flags"] = ["-O2"]
    assert CompilerMixin.compiler_profile_usage(engine, {"id": 7})[0]["flags"] == ["-O2"]


def test_profile_usage_indexes_legacy_overrides_once_instead_of_scanning_per_function():
    class Overrides(dict):
        scans=0
        def values(self):
            self.scans+=1
            return super().values()
    overrides=Overrides({str(n):dict(function=f'f{n}',vram=100,toolchain='gcc',flags=[f'-O{n%3}']) for n in range(100)})
    cfg=dict(compiler=dict(toolchain='gcc',flags=['-O2']),function_compilers=overrides)
    rows=[dict(name=f'f{n}',vram=100,rom_offset=None,status='open') for n in range(100)]
    engine=SimpleNamespace(cfg=lambda _:cfg,store=SimpleNamespace(all=lambda *args:rows))
    engine.project_store = lambda _: engine.store
    usage=CompilerMixin.compiler_profile_usage(engine,{'id':1})
    assert overrides.scans==1
    assert sorted(p['functions'] for p in usage)==[33,33,34]
