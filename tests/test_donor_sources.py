
import pytest

from decompnet.app import donor_sources as ds
from decompnet.network import NetworkError


def test_literal_build_hints_do_not_execute_make_or_mix_assembly_flags(tmp_path):
    (tmp_path/'sdk').mkdir();(tmp_path/'include').mkdir()
    marker=tmp_path/'executed'
    (tmp_path/'Makefile').write_text(f'''IINC += -I sdk -I $(ROOT)/ignored -I ../outside
COMMON_DEFINES := -DWORD_BITS=32
C_DEFINES := -D_LANGUAGE_C
AS_DEFINES := -D_LANGUAGE_ASSEMBLY
ifeq ($(VERSION),other)
C_DEFINES += -DOTHER=1
endif
BAD := $(shell touch {marker})
''')
    roots,macros=ds.build_hints(tmp_path)
    assert roots[0]==tmp_path/'sdk'
    assert macros=={'WORD_BITS':'32','_LANGUAGE_C':'1'}
    assert not marker.exists()


def test_conditional_includes_are_left_for_the_destination_preprocessor(tmp_path):
    path=tmp_path/'entry.c'
    path.write_text('#ifdef ASSEMBLY\n#include "missing.h"\n#endif\n#include "types.h"\nint f(Word x){return x;}')
    (tmp_path/'types.h').write_text('typedef int Word;')
    reader=ds.DonorReader({'root':str(tmp_path)})
    text=reader.flatten(path)
    assert '#ifdef ASSEMBLY\n#error donor_header_missing' in text
    assert 'typedef int Word;' in text
    path.write_text('#include "../outside.h"')
    with pytest.raises(NetworkError,match='within'):reader=ds.DonorReader({'root':str(tmp_path)});reader.flatten(path)


def test_reader_rejects_symlink_escapes_and_unbounded_context(tmp_path):
    outside=tmp_path.parent/'donor-outside.h';outside.write_text('typedef int Secret;')
    (tmp_path/'escape.h').symlink_to(outside)
    (tmp_path/'entry.c').write_text('#include "escape.h"\nint f(void){return 0;}')
    reader=ds.DonorReader({'root':str(tmp_path)})
    with pytest.raises(NetworkError,match='leaves'):reader.flatten(tmp_path/'entry.c')
    (tmp_path/'large.c').write_text(' '* (ds.MAX_HEADER+1))
    with pytest.raises(NetworkError,match='2 MB'):reader.flatten(tmp_path/'large.c')


def test_compact_retains_only_required_declarations_and_no_foreign_include():
    text='typedef unsigned short Small; typedef struct {Small x;} Rec; typedef int Unused; int unrelated(void){return 5;} void f(Rec *r){r->x=3;}'
    source,header=ds.compact(text,'f')
    assert source.lstrip().startswith('void f(')
    assert 'typedef unsigned short Small;' in header and 'Rec;' in header
    assert 'Unused' not in header and 'unrelated' not in header
    assert '#include' not in source+header
    with pytest.raises(NetworkError,match='initialized'):
        ds.compact('int global=2; int f(void){return global;}','f')


def test_same_name_preprocessing_and_context_compile_with_pinned_compiler(tmp_path):
    from types import SimpleNamespace

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs installed pinned compiler')
    settings={'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    net=SimpleNamespace(cfg=lambda p:settings)
    proj={'root':str(tmp_path)}
    fn={'vram':0,'name':'f'}
    body='int f(Small x){return (x + OFFSET) * 2;}'
    context='#if 0\n#error missing conditional header\n#endif\n#define OFFSET 3\ntypedef unsigned short Small;'
    source,header=ds.preprocess(net,proj,fn,body,context)
    assert 'OFFSET' not in source and len(header)<100
    cfg=compiler_config(tmp_path,settings)
    candidate=compile_source(cfg,header,source)
    control=compile_source(cfg,'','int f(unsigned short x){return (x+3)*2;}')
    assert candidate.ok,candidate.log
    assert control.ok,control.log
    assert extract_function(candidate.obj,'f').code==extract_function(control.obj,'f').code
