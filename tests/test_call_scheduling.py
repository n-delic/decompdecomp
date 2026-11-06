"""Synthetic sources only: bounds, sequencing guards, and coupled scheduling hypotheses."""
import pytest

from decompnet.call_scheduling import MAX_VARIANTS, variants

SOURCE = """typedef struct { int count; } Item;
void send(Item *, float, float);
void f(Item *p)
{
    p->count++;
    send(p, 9.25f, 2.5f);
}
"""


def test_corrected_argument_order_and_inline_return_are_tried_together():
    candidates = variants(SOURCE, "f")
    assert len(candidates) == 4
    assert any("send(p, 2.5f, decomp_arg_" in c and "return 9.25f;" in c for c in candidates)
    assert all(c.count("p->count++") == 1 and c.count("void f(") == 1 for c in candidates)
    assert all(c.index("typedef struct") < c.index("static inline") < c.index("void f(") for c in candidates)
    assert variants(SOURCE, "f") == candidates
    assert all(not variants(c, "f") for c in candidates)


@pytest.mark.parametrize("replacement", [
    "send(p, p->count, 2.5f);", "send(p, other(), 2.5f);", "send(p++, 9.25f, 2.5f);",
    "send(p, (9.25f), 2.5f);", "return send(p, 9.25f, 2.5f);",
])
def test_ambiguous_or_effectful_arguments_are_not_moved(replacement):
    assert not variants(SOURCE.replace("send(p, 9.25f, 2.5f);", replacement), "f")


@pytest.mark.parametrize("change", [
    lambda s: s.replace("Item *p)\n{", "Item *p, int n)\n{"),
    lambda s: s.replace("p->count++;", "#if ENABLE\n    p->count++;\n#endif"),
    lambda s: s.replace("p->count++;", "Item *p;\n    p->count++;"),
    lambda s: s.replace("p->count++;", "if (p) p->count++;"),
    lambda s: s.replace("p->count++;", "/*\n    p->count++;"),
    lambda s: s.replace("p->count++;", "p->count++;\n    unrelated();"),
])
def test_unsupported_contexts_are_left_untouched(change):
    assert not variants(change(SOURCE), "f")


@pytest.mark.parametrize("name", ["p", "count", "send", "Item"])
def test_macro_identifiers_are_not_extracted(name):
    assert not variants(SOURCE, "f", f"#define {name} replacement\n")


def test_comments_strings_and_other_functions_are_not_search_sites():
    decoy = '/*\nvoid f(Item *p) {\n    p->count++;\n    send(p, 9.25f, 2.5f);\n}\n*/\n'
    candidates = variants(decoy + SOURCE, "f")
    assert len(candidates) == 4
    assert all(c.startswith(decoy) for c in candidates)
    assert not variants(SOURCE.replace("void f(", "void other("), "f")


def test_probe_budget_and_names_are_bounded():
    repeated = SOURCE.replace("    p->count++;\n    send(p, 9.25f, 2.5f);",
                              "    p->count++;\n    send(p, 9.25f, 2.5f);\n" * 20)
    candidates = variants(repeated, "f", "int decomp_arg_0;")
    assert len(candidates) == MAX_VARIANTS
    assert len(set(candidates)) == MAX_VARIANTS
    assert "decomp_arg_0_(" in candidates[0]
