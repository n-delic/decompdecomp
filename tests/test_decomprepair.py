"""The shared rewrite dependency preserves the app's imports and isolated generator launch."""
import importlib
from pathlib import Path

from decompnet.app import permute


def test_legacy_imports_are_the_shared_library_modules():
    for app, library in [('permuter', 'permuter'), ('permuter_gen', 'permuter_gen'),
                         ('app.permuter_local', 'permuter_local')]:
        assert importlib.import_module('decompnet.' + app) is importlib.import_module('decomprepair.' + library)


def test_generator_runs_beside_its_guards_in_the_tool_environment():
    from decomprepair import permuter_gen
    assert permute.GENERATOR == Path(permuter_gen.__file__).resolve()
    for helper in ('permuter_capture.py', 'permuter_dependencies.py', 'permuter_formatting.py'):
        assert permute.GENERATOR.with_name(helper).is_file()


def test_the_pin_is_one_archive_everywhere_and_the_installer_checks_it():
    """pyproject.toml pins a commit's archive (a tag can move), uv.lock holds its SHA-256, and install.sh, whose
    `uv tool install` doesn't read uv.lock, downloads that archive, checks that hash and installs that file."""
    import re
    import tomllib

    root = Path(__file__).resolve().parents[1]
    deps = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    url = next(d for d in deps if d.startswith("decomprepair")).split("@", 1)[1].strip()
    assert re.fullmatch(r"https://github\.com/RetroReversing/decomp-repair/archive/[0-9a-f]{40}\.tar\.gz", url)
    lock = tomllib.loads((root / "uv.lock").read_text())
    package = next(p for p in lock["package"] if p["name"] == "decomprepair")
    assert package["source"] == {"url": url}
    sha = package["sdist"]["hash"].removeprefix("sha256:")
    script = (root / "site" / "public" / "install.sh").read_text()
    assert f'DECOMPREPAIR_VERSION="{package["version"]}"' in script
    assert f'DECOMPREPAIR_URL="{url}"' in script and f'DECOMPREPAIR_SHA256="{sha}"' in script
    assert '--overrides "$REPAIR_DIR/overrides.txt"' in script


def test_the_generator_leaves_out_decomp_permuters_unguarded_passes():
    """Five of decomp-permuter's passes are off for every game: each can write C that reads what was never set or
    drops an effect (the library's supported_methods says how), and the guards can't prove them safe yet."""
    from decomprepair.permuter_gen import supported_methods

    names = ["perm_add_self_assignment", "perm_remove_var", "perm_refer_to_var", "perm_alias_array",
             "perm_remove_ast", "perm_reorder_stmts", "perm_commutative"]
    methods = [(type(n, (), {"__name__": n}), 1) for n in names]  # (only a method's name is read)
    assert [m.__name__ for m, _ in supported_methods(methods)] == ["perm_reorder_stmts", "perm_commutative"]


def test_local_hypotheses_reach_a_definition_with_an_attribute_in_front():
    """`static inline __attribute__((always_inline))` or MSVC's `__declspec(...)` before a definition took its
    parentheses for an expression, and the function got no local hypotheses."""
    from decompnet.app import permuter_local

    for head in ("static inline __attribute__((always_inline)) void", "__declspec(noinline) void"):
        source = f"{head} f(Item *p)\n{{\n    p->y = 0;\n    p->x = 0;\n}}\n"
        assert any("p->x = 0;\n    p->y = 0;" in c for _, c in permuter_local.variants(source, "f")), head
