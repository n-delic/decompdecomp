"""Library data definitions must not prevent otherwise exact source transfers."""

from types import SimpleNamespace

from decompnet.app import free_candidates as free
from decompnet.app import sdk_source_repairs as repair
from decompnet.app.engine import RomNetwork
from decompnet.app.twins import Job

from .test_rom import ROM, built, truth  # noqa: F401


def test_externalize_only_simple_public_file_scope_definitions(monkeypatch):
    names = ('global_value', 'pointer', 'array', 'private_value', 'local_value', 'a', 'b')
    class Symbol(dict):
        def __init__(self, name):
            super().__init__(st_info={'type': 'STT_OBJECT', 'bind': 'STB_GLOBAL'}, st_shndx=3)
            self.name = name
    table = SimpleNamespace(iter_symbols=lambda: iter(Symbol(name) for name in names))
    monkeypatch.setattr(repair, 'ELFFile', lambda _: SimpleNamespace(get_section_by_name=lambda _: table))
    source = '''int global_value;
int *pointer = &global_value;
int array[4];
static int private_value;
int a, b;
void helper(void) {
    int local_value;
    /* } */
}
'''
    got = repair.externalize(source, b'object')
    assert 'extern int global_value;' in got
    assert 'extern int *pointer;' in got
    assert 'extern int array[4];' in got
    assert 'static int private_value;' in got
    assert 'int a, b;' in got
    assert '    int local_value;' in got and 'extern int local_value' not in got
    assert repair.externalize(source, b'object') == got


def test_invalid_object_is_not_a_source_rewrite():
    assert repair.externalize('int value;', b'not ELF') == 'int value;'


def test_exhausted_sdk_repair_does_not_compile():
    job = Job(); free.budget(job, attempts=0)
    net = SimpleNamespace(_context=lambda *_: {'header': ''})
    assert repair.variant(net, {'id': 1}, {'unit': ''}, '', None, job) is None
    assert job.reuse_attempts == 0


def test_sdk_data_repair_still_needs_exact_real_rom_judge(built, tmp_path, monkeypatch):  # noqa: F811
    net = RomNetwork(tmp_path/'decomp.db', tmp_path)
    monkeypatch.setattr(net.twins, 'after_match', lambda *args: None)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    proj = net.project_row(slug)
    address = truth().by_name['rng_next'].address
    fn = net.store.one('SELECT * FROM functions WHERE project_id=? AND vram=?', proj['id'], address)
    source = ('int unrelated;\nint *sdk_pointer = &unrelated;\nunsigned int sdk_rng;\n'
              'unsigned int ' + fn['name'] + '(void) {\n'
              'sdk_rng = sdk_rng * 1103515245 + 12345;\nreturn (sdk_rng >> 16) & 0x7FFF;\n}')
    agent, _ = net.local_agent('autopilot')
    assert not net.try_quietly(agent, proj, fn, source)['matched']
    job = Job(); free.budget(job)
    proposal = repair.variant(net, proj, fn, source, net.twins.prints(proj)[fn['id']], job)
    assert proposal and 'extern unsigned int D_' in proposal
    assert job.reuse_attempts == 2
    assert repair.variant(net, proj, fn, source, net.twins.prints(proj)[fn['id']], job) == proposal
    assert job.reuse_attempts == 2  # cached probes do not compile again
    assert net.try_quietly(agent, proj, fn, proposal)['matched']


def test_sdk_repair_rejects_changed_instruction_fingerprint(monkeypatch):
    from decompnet.app import twins
    target = twins.Print('target', [], 32)
    net = SimpleNamespace(_context=lambda *_: {'header': ''},
                          _compile=lambda *_: SimpleNamespace(ok=True, obj=b'object'))
    monkeypatch.setattr(repair, 'externalize', lambda source, obj: 'extern int value;')
    monkeypatch.setattr(twins, 'object_prints', lambda obj: {'helper': twins.Print('different', [], 32)})
    job = Job(); free.budget(job)
    assert repair._variant(net, {'id': 1}, {'name': 'helper', 'unit': ''}, 'int value;', target, job) is None
    assert job.reuse_attempts == 2


def test_sdk_repair_stops_between_compiler_probes(monkeypatch):
    calls = []
    net = SimpleNamespace(_context=lambda *_: {'header': ''},
                          _compile=lambda *_: calls.append(1) or SimpleNamespace(ok=True, obj=b'object'))
    monkeypatch.setattr(repair, 'externalize', lambda source, obj: 'extern int value;')
    job = Job(); free.budget(job, attempts=1)
    assert repair._variant(net, {'id': 1}, {'unit': ''}, 'int value;', None, job) is None
    assert len(calls) == job.reuse_attempts == 1
