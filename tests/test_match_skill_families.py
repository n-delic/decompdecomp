"""families.py (the match-function skill): a searchable one-line-per-family list read from the installed library."""
import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"
spec = importlib.util.spec_from_file_location("skill_families", SCRIPTS / "families.py")
families = importlib.util.module_from_spec(spec)
sys.modules["skill_families"] = families
spec.loader.exec_module(families)


def library(tmp_path):
    (tmp_path / "one_repairs.py").write_text('"""Single-call one-shot scopes. Second sentence is dropped."""\n'
                                             "FAMILIES = ('call_one_shot_scope', 'call_one_shot_scope_shift')\n"
                                             "def variants(source, name):\n    return []\n")
    (tmp_path / "two_repairs.py").write_text('"""Counts a mask."""\nFAMILY = "mask_operand_order"\nALL = (FAMILY, "other")\n'
                                             "FAMILIES = ALL + (FAMILY,)\n")
    (tmp_path / "bare_repairs.py").write_text('"""Labels its own children."""\ndef variants(a):\n    return []\n')
    (tmp_path / "helper.py").write_text('"""Shared reader."""\nX = 1\n')
    (tmp_path / "other_module.py").write_text('"""Uses another module."""\nFAMILIES = one_repairs.FAMILIES + ("tail",)\n')
    (tmp_path / "broken.py").write_text("def (:\n")
    return tmp_path


def test_each_family_is_one_line_with_its_module_and_first_sentence(tmp_path):
    rows = families.catalogue(library(tmp_path))
    assert ("call_one_shot_scope_shift", "one_repairs", "Single-call one-shot scopes.") in rows
    assert [r[0] for r in rows if r[1] == "two_repairs"] == ["mask_operand_order", "other"]  # constants resolved, deduped
    assert ("bare_repairs", "bare_repairs", "Labels its own children.") in rows  # no constant: the module is the family
    assert not any(r[1] == "helper" for r in rows) and not any(r[1] == "broken" for r in rows)
    assert any(r[1] == "helper" for r in families.catalogue(tmp_path, include_helpers=True))
    assert not any(r[1] == "other_module" for r in rows)  # a family list that needs the library's own code is not guessed


def test_every_query_word_must_match_and_the_output_is_one_line_per_family(tmp_path, capsys):
    families.main(["shot", "scope", "--library", str(library(tmp_path))])
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["call_one_shot_scope  one_repairs  Single-call one-shot scopes.",
                   "call_one_shot_scope_shift  one_repairs  Single-call one-shot scopes."]
    families.main(["mask", "nothing", "--library", str(tmp_path)])
    assert capsys.readouterr().out == ""


def test_the_installed_library_is_listed_and_its_wired_families_are_found(capsys):
    families.main(["call_one_shot_scope"])
    assert "call_scope_repairs" in capsys.readouterr().out
