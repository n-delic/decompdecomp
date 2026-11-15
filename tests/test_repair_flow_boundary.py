"""Unsupported flow views must not kill the shared app repair-generation path."""
import pytest

from decompnet.app import draft_repairs


@pytest.mark.parametrize('source', [
    'int sample(int input) { int value = input; return value;',
    'int sample(int input)\n#ifdef PORT\n{\n#else\n{\n#endif\nint value = input; return value;\n}',
])
def test_repair_generation_survives_an_unclosed_simplified_view(source):
    # This is the same entry point used by the background worker. The library
    # declines unsupported liveness; other independent hypotheses may still run.
    proposals = draft_repairs.variants(source, 'sample', 'mips', limit=8)
    assert isinstance(proposals, list) and all(isinstance(text, str) for text in proposals)
    closed = 'int sample(int input) { int value; value = input; return value; }'
    assert isinstance(draft_repairs.variants(closed, 'sample', 'mips', limit=8), list)
