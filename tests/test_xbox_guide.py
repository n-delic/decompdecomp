"""What the Xbox guide tells agents about link-time code generation, checked against what an Xbox game's helpers ran
into."""

from decompnet.app import tools


def _xbox() -> str:
    return " ".join(tools.guide_text("xbox", "xbox").split())


def test_float_constants_try_the_literal_and_the_extern():
    """The guide said "write 0.5f, not extern const float D_..."; an agent following it scored 59.1% where externs
    gave 65.8%."""
    text = _xbox()
    assert "try both the literal" in text and "On equal scores prefer the literal" in text
    assert "`1.0f - 1.3f`" in text
    assert "not `extern const float" not in text


def test_unresolved_data_isnt_called_harmless_and_volatile_in_the_harness_is_forbidden():
    text = _xbox()
    assert "harmless, the code is the same" not in text
    assert "Define it, with its real type, in the unit or a harness file" in text
    assert "`volatile` in a driver or stand-in, to steer the compiler, is forbidden" in text
    assert "only as a documented exception" in text


def test_pool_constants_stay_out_of_the_harness_and_address_escapes_are_allowed():
    """An Xbox racing game: agents defined .rdata floats in a driver, then with a volatile seed so LTCG wouldn't fold
    them (97.6%; the literals gave 99%), and the guide's volatile ban contradicted the escape arrays the project's
    rules and the agents' shared knowledge rely on."""
    text = _xbox()
    assert "Never define a pool constant in harness/" in text
    assert "(`t / 60.0f`, `t * (1.0f / 60.0f)`)" in text
    assert ("An address escape (`void *volatile esc[] = { &g_x };`) for a global the game writes or takes the "
            "address of elsewhere is faithful") in text


def test_adding_a_helper_or_call_site_says_to_check_the_convention_again():
    text = _xbox()
    assert "Adding a helper function or a call site changes conventions under LTCG" in text
    assert "a single call site" in text and "check the convention evidence again" in text


def test_the_xbox_up_front_material_is_lean_and_x86_only_material_stays_on_x86():
    """An Xbox LTCG helper's first prompt was 19 KB, with GameCube material and repetition in the Xbox guide."""
    assert len(tools.guide_text("xbox", "xbox").encode()) < 8000
    assert len(tools.up_front("xbox", "fork", "").encode()) < 14500  # (the fork guide every console shares grew a KB)
    assert "Struct_00011570" in tools.guide_text("cpp", "xbox")
    assert "Struct_00011570" not in tools.guide_text("cpp", "gc")
    text = _xbox()
    for kept in ("HarnessEntry", "standins_for", "`// FUNCTION: 0x<address>", "<name>.nogl.cpp", "HARNESS-LINK",
                 "Two objects alike", "LTCG register convention"):
        assert kept in text, kept
