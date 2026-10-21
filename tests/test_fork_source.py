"""What compile_and_diff takes in a fork: one definition, plus the lines placed for it. Agents sent a
`static inline` helper with the function twice and only learned from the refusal that edit_file is where it goes;
the guide and the prompt now say so up front, and the refusal says exactly that."""

import pytest

from decompnet.app import tools
from decompnet.app.forkengine import _around
from decompnet.network import NetworkError


def test_a_helper_inside_the_compile_source_is_refused_with_where_it_goes():
    source = "static inline int ClampSpeed(int x) { return x; }\nint f(int x) { return ClampSpeed(x); }\n"
    span = (source.index("int f"), len(source))
    with pytest.raises(NetworkError) as e:
        _around(source, span, "", "src/auto/unit.cpp")
    said = " ".join(str(e.value).split())
    assert "ClampSpeed" in said
    assert ("Helper functions, structs and the prototypes you want shared go in through edit_file (a header, or the "
            "top of src/auto/unit.cpp), not inside compile_and_diff's source") in said
    assert "Nothing was compiled" in said


def test_prototypes_and_includes_still_go_with_the_definition():
    source = '#include "x.h"\nint g(int);\nint f(int x) { return g(x); }\n'
    span = (source.index("int f"), len(source))
    includes, before, after = _around(source, span, "", "src/a.c")
    assert includes == ['#include "x.h"'] and before == ["int g(int);"] and after == []


def test_the_guide_and_prompt_say_helpers_go_in_with_edit_file():
    guide = " ".join(tools.guide_text("fork").split())
    assert "never inside the compile_and_diff source" in guide and "`edit_file`" in guide
    prompt = " ".join(tools.FORK_PROMPT.split())
    assert "never inside compile_and_diff's source" in prompt
