"""decomprepair is a dependency (RetroReversing/decomp-repair, which tests the library itself); this checks how the
app uses it."""


def test_app_names_are_the_library_modules():
    """The app keeps its old names for the shared readers; both names must be one module (patches reach both)."""
    from decompnet.arch import mips
    from decompnet.forks import csource
    from decompnet.rom import symbols
    from decomprepair import csource as c
    from decomprepair import mips as m
    from decomprepair import symbols as s
    assert (csource, mips, symbols) == (c, m, s)


def test_moved_mips_readers_keep_their_app_names():
    """The MIPS evidence readers (0.2.0) and the permuter's rewrites (0.3.0) moved to decomprepair; the app's names
    are the same modules."""
    import importlib

    for name in ("mips_arguments", "mips_literals", "mips_parameters", "mips_stack", "mips_local_storage",
                 "mips_pointer_lifetime", "mips_returns", "mips_return_paths", "mips_regions", "mips_hazards",
                 "mips_search", "mips_source_recovery", "mips_templates", "mips_callback_flow", "mips_tail_dispatch",
                 "mips_references", "mips_runtime", "call_evidence", "mips_count", "mips_compare", "mips_status",
                 "mips_fcsr", "mips_status_return_spacing", "mips_debug_break", "permuter", "permuter_gen",
                 "permuter_capture", "permuter_dependencies", "permuter_formatting", "app.permuter_initialization",
                 "app.permuter_local"):
        app = f"decompnet.{name}" if name.startswith(("permuter", "app.")) else f"decompnet.arch.{name}"
        assert importlib.import_module(app) is importlib.import_module(f"decomprepair.{name.split('.')[-1]}")


def test_toolchains_name_the_intrinsics_header_the_app_way():
    """toolchains.toml says `decompnet.arch.mips_debug_break`; the compiler imports it by that name."""
    import importlib

    from decompnet import toolchains
    from decomprepair import mips_debug_break

    tc = toolchains.get("gcc-2.7.2-kmc")
    assert importlib.import_module(tc.intrinsics).header() == mips_debug_break.header()


def test_the_permuter_runs_the_library_generator():
    """app/permute.py starts permuter_gen.py by path: the library's copy, beside its helpers."""
    import decomprepair
    from decompnet.app import permute

    assert permute.GENERATOR.parent == __import__("pathlib").Path(decomprepair.__file__).resolve().parent
    assert (permute.GENERATOR.parent / "permuter_capture.py").is_file()
