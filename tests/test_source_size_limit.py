"""The compiler's source guard admits a big game function's machine draft, and still refuses an absurd one."""
import pytest

from decompnet.compiler import MAX_SOURCE_BYTES, SourceRejected, check_source


def test_a_large_functions_draft_is_accepted():
    check_source('void f(void) {\n' + '    x = 1;\n' * 20000 + '}\n')  # ~200 KB, as m2c writes a 9,000-instruction function


def test_an_absurd_source_is_still_refused():
    with pytest.raises(SourceRejected, match='source too large'):
        check_source('x;\n' * (MAX_SOURCE_BYTES // 3 + 1))
