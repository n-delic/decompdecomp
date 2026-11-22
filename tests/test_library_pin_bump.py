"""tools/bump_library.py and the workflow that uses it: the decomprepair pin follows the library's releases by itself."""
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bump_library", ROOT / "tools" / "bump_library.py")
bump = importlib.util.module_from_spec(spec)
sys.modules["bump_library"] = bump
spec.loader.exec_module(bump)

LS_REMOTE = "\n".join(f"abc{i}\trefs/tags/{t}" for i, t in enumerate(
    ["v0.5.9", "v0.5.10", "v0.5.100", "v1.0.10-rr", "v0.5.409^{}", "v0.5.99", "latest"]))


def test_the_pin_in_this_repository_is_found_and_a_newer_tag_rewrites_only_it():
    text = (ROOT / "pyproject.toml").read_text()
    current = bump.pinned(text)
    assert re.fullmatch(r"\d+\.\d+\.\d+", current)
    new = bump.rewrite(text, "9.9.9")
    assert bump.pinned(new) == "9.9.9"
    assert new.replace("9.9.9", current) == text  # nothing else changed (the assets pin and all other lines stay)


def test_tags_compare_as_numbers_and_other_tags_are_ignored():
    assert bump.newest(LS_REMOTE) == "0.5.100"
    assert bump.newest("") is None
    assert bump.key("0.5.10") > bump.key("0.5.9")


def test_summary_strips_the_release_prefix(monkeypatch):
    class Done:
        returncode, stdout = 0, "Release decomprepair 0.5.2: ternary_clamp_split, a clamp\n\nbody"
    monkeypatch.setattr(bump.subprocess, "run", lambda *a, **k: Done)
    assert bump.summary("0.5.2") == "ternary_clamp_split, a clamp"
    monkeypatch.setattr(bump.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert bump.summary("0.5.2") == ""


def test_main_reports_a_current_pin_and_rewrites_a_stale_one(monkeypatch, tmp_path, capsys):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    current = bump.pinned((ROOT / "pyproject.toml").read_text())
    major, minor, patch = bump.key(current)
    fake = lambda tags: type("Done", (), {"stdout": tags, "returncode": 0})  # noqa: E731
    monkeypatch.setattr(bump.subprocess, "run", lambda *a, **k: fake(f"x\trefs/tags/v{current}") if a[0][0] == "git" else fake(""))
    assert bump.main(["--check"]) == 0 and "newest release" in capsys.readouterr().out
    assert "version=\n" in out.read_text()
    monkeypatch.setattr(bump.subprocess, "run",
                        lambda *a, **k: fake(f"x\trefs/tags/v{major}.{minor}.{patch + 3}") if a[0][0] == "git" else fake(""))
    assert bump.main(["--check"]) == 0 and "is newer than the pin" in capsys.readouterr().out
    assert (ROOT / "pyproject.toml").read_text().count(f"v{major}.{minor}.{patch + 3}.tar.gz") == 0  # --check writes nothing


@pytest.fixture
def workflow():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "library-pin.yml").read_text())


def test_the_workflow_runs_on_a_schedule_by_hand_and_merges_through_its_own_token(workflow):
    triggers = workflow.get(True) or workflow["on"]  # YAML reads a bare `on` as true
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    assert workflow["permissions"] == {"contents": "write", "pull-requests": "write", "issues": "write"}
    text = (ROOT / ".github" / "workflows" / "library-pin.yml").read_text()
    assert "secrets." not in text  # needs no secret
    assert "gh pr merge" in text and "tests/test_library_family_wiring.py" in text and "git tag" not in text


def test_the_workflow_fails_loudly_when_a_family_is_not_wired_but_not_when_smoke_tests_pass(workflow):
    steps = workflow["jobs"]["pin"]["steps"]
    wiring = next(s for s in steps if s.get("id") == "wiring")
    assert wiring["continue-on-error"] is True  # the pin still lands: wiring needs the new tag installed
    loud = next(s for s in steps if "Fail loudly" in s.get("name", ""))
    assert "steps.wiring.outcome == 'failure'" in loud["if"] and "exit 1" in loud["run"]
    assert "GITHUB_STEP_SUMMARY" in loud["run"] and "|| echo" in loud["run"]  # issues are off in this repository
    smoke = next(s for s in steps if "Smoke" in s.get("name", ""))
    assert "continue-on-error" not in smoke  # a smoke failure stops the pin
    order = [s.get("name") or s.get("id") or s.get("run", "")[:20] for s in steps]
    assert order.index(smoke["name"]) < order.index("Open and merge the pin pull request")


TAGS = "\n".join(f"{'a' * 40}\trefs/tags/v0.5.{n}" for n in (640, 641, 642))


def test_a_tag_whose_archive_is_not_downloadable_yet_is_skipped_not_pinned():
    """v0.5.642 was listed by git ls-remote seconds before codeload served its archive; uv lock then failed on a 404."""
    asked = []
    ready = lambda version: asked.append(version) or version != "0.5.642"
    assert bump.pick(TAGS, "0.5.639", ready) == "0.5.641" and asked == ["0.5.642", "0.5.641"]
    assert bump.pick(TAGS, "0.5.641", lambda version: False) is None
    assert bump.pick(TAGS, "0.5.642", ready) is None


def test_downloadable_waits_for_the_archive_and_gives_up_after_its_attempts():
    answers = iter([404, 404, 200])
    slept = []
    assert bump.downloadable("0.5.642", attempts=5, wait=7, fetch=lambda url: next(answers), sleep=slept.append)
    assert slept == [7, 7]
    slept.clear()
    assert not bump.downloadable("0.5.642", attempts=3, wait=7, fetch=lambda url: 404, sleep=slept.append)
    assert slept == [7, 7]
    urls = []
    bump.downloadable("0.5.642", attempts=1, fetch=lambda url: urls.append(url) or 200)
    assert urls == ["https://github.com/RetroReversing/decomp-repair/archive/refs/tags/v0.5.642.tar.gz"]
