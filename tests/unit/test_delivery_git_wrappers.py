import shutil
import subprocess
from pathlib import Path

import pytest
from scripts import integration_release

GIT = shutil.which("git") or "/usr/bin/git"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(  # noqa: S603 - test Git executable is resolved locally.
        (GIT, *arguments),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Integration Test")
    _git(root, "config", "user.email", "integration@example.invalid")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "chore: base")
    _git(root, "branch", "-M", "main")
    _git(root, "switch", "-c", "codex/qir/example")
    (root / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(root, "add", "feature.txt")
    _git(root, "commit", "-m", "feat: example")
    reviewed = _git(root, "rev-parse", "HEAD")
    _git(root, "switch", "-c", "integration/R1/example", "main")
    return root, reviewed


def test_merge_accepts_only_the_exact_reviewed_source_tip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, reviewed = _repository(tmp_path)
    monkeypatch.setattr(integration_release, "ROOT", root)
    before = _git(root, "rev-parse", "HEAD")
    _git(root, "config", "merge.ff", "false")

    merged = integration_release.merge("codex/qir/example", reviewed)

    assert merged == reviewed
    parents = _git(root, "rev-list", "--parents", "-n", "1", merged).split()
    assert parents == [reviewed, before]


def test_divergent_integration_preserves_both_reviewed_histories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, reviewed = _repository(tmp_path)
    monkeypatch.setattr(integration_release, "ROOT", root)
    (root / "candidate.txt").write_text("another integrated capability\n", encoding="utf-8")
    _git(root, "add", "candidate.txt")
    _git(root, "commit", "-m", "feat: candidate capability")
    before = _git(root, "rev-parse", "HEAD")

    merged = integration_release.merge("codex/qir/example", reviewed)

    assert _git(root, "rev-list", "--parents", "-n", "1", merged).split() == [
        merged,
        before,
        reviewed,
    ]
    assert (root / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    assert (root / "candidate.txt").read_text(encoding="utf-8") == "another integrated capability\n"


def test_merge_rejects_a_commit_other_than_the_source_tip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, _reviewed = _repository(tmp_path)
    monkeypatch.setattr(integration_release, "ROOT", root)
    wrong = _git(root, "rev-parse", "main")

    with pytest.raises(integration_release.IntegrationError, match="source tip"):
        integration_release.merge("codex/qir/example", wrong)


def test_merge_resolves_the_exact_branch_when_a_same_named_tag_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, reviewed = _repository(tmp_path)
    monkeypatch.setattr(integration_release, "ROOT", root)
    _git(root, "tag", "codex/qir/example", "main")

    merged = integration_release.merge("codex/qir/example", reviewed)

    assert _git(root, "rev-parse", "HEAD") == merged
    assert _git(root, "rev-parse", "refs/heads/codex/qir/example") == reviewed


def test_merge_has_no_push_tag_or_main_advance_path() -> None:
    source = Path(integration_release.__file__).read_text(encoding="utf-8")

    assert '"push"' not in source
    assert '"tag"' not in source
    assert '"main", "--ff-only"' not in source
