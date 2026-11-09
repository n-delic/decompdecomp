"""Preflight skips only fixed, plain C arity errors, never guesses about ambiguous C."""
import pytest

from decompnet.app.repair_preflight import invalid_call


def test_known_prototype_rejects_generated_missing_argument():
    source = 'extern int send(int a, int b); int f(int x) { return send(x); }'
    assert 'expects 2' in invalid_call(source, 'f')
    assert not invalid_call(source.replace('send(x);', 'send(x, 7);'), 'f')
    assert invalid_call('int f(void) { return send(); }', 'f', 'extern int send(int x);')


@pytest.mark.parametrize('prototype,call', [
    ('int send();', 'send()'), ('int send(int x); int send(int x,...);', 'send(1,2)'),
    ('#include \"library.h\"\nint send(int x);', 'send()'), ('int send(int x, ...);', 'send(1, 2, 3)'),
    ('int send(word x);', 'send()'), ('int send(int x=1);', 'send()'),
    ('int send(int x); int send(int x, int y);', 'send()'),
    ('#define X 1,2\nint send(int x,int y);', 'send(X)'),
    ('#if FLAG\nint send(int x);\n#endif', 'send()'),
])
def test_ambiguous_declarations_stay_with_compiler(prototype, call):
    assert not invalid_call(prototype+'\nint f(void) { return '+call+'; }', 'f')


def test_nested_calls_strings_comments_and_void():
    header = 'void send(int x, char *s); void finish(void);'
    assert not invalid_call('int f(void) { send(other(1, 2), "a,b"); finish(); /* send() */ }', 'f', header)
    assert invalid_call('int f(void) { finish(1); }', 'f', header)
    assert not invalid_call('int f(void) { int (*send)(); return send(); }', 'f', header)


def test_draft_generation_removes_invalid_arity_before_bounded_selection():
    from decompnet.app.draft_repairs import variants
    source = 'int send(int x,int y); int f(int x) { return send(x, 7); }'
    assert all('return send(x);' not in candidate for candidate in variants(source, 'f', 'mips'))


def test_cached_header_keeps_candidate_declarations_and_shadows_independent():
    header = 'int send(int);'
    assert invalid_call('int f(void){return send();}', 'f', header)
    assert not invalid_call('int send(); int f(void){return send();}', 'f', header)
    assert not invalid_call('int f(int (*send)()){return send();}', 'f', header)
    assert invalid_call('int f(void){return send();}', 'f', header)
    assert not invalid_call('int f(void){return send();}', 'f', 'int send(void);')


def test_header_fragments_keep_the_combined_scan():
    assert invalid_call('int); int f(void){return send();}', 'f', 'int send(')
    assert not invalid_call('*/ int f(void){return send();}', 'f', '/* int send(int);')
    assert not invalid_call('#define ARG 1,2\nint f(void){return send(ARG);}', 'f', 'int send(int,int);')


def test_header_cache_is_bounded_and_large_headers_are_not_retained():
    from decompnet.app import repair_preflight
    repair_preflight._header.cache_clear()
    for i in range(8):
        assert invalid_call('int f(void){return send();}', 'f', f'int send(int value{i});')
    assert repair_preflight._header.cache_info().currsize == 4
    before = repair_preflight._header.cache_info()
    assert invalid_call('int f(void){return send();}', 'f', ' '*repair_preflight.MAX_CACHED_HEADER_CHARS+'int send(int);')
    assert repair_preflight._header.cache_info() == before
