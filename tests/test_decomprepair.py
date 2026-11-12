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
