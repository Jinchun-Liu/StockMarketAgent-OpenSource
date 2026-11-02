import json
import subprocess
import sys
from copy import deepcopy
from io import StringIO
from pathlib import Path

import pytest
from scripts import agent_guard
from scripts.agent_guard import (
    GuardInputError,
    _blocked_reason,
    _decode_payload,
    _protect,
    _sensitive,
)
from scripts.check_architecture import validate as validate_architecture
from scripts.check_delivery_registry import _validate_owner_authorization
from scripts.check_delivery_registry import validate as validate_delivery
from scripts.check_governance import LEGACY_FILES
from scripts.check_governance import validate as validate_governance
from scripts.check_interface_registry import validate as validate_interfaces
from scripts.check_metric_registry import validate as validate_metrics
from scripts.check_repository_policy import validate as validate_repository

ROOT = Path(__file__).resolve().parents[2]


def _guard_git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed local test repository and Git arguments.
        ["git", *arguments],  # noqa: S607 - local test Git, never a remote operation.
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip()


def _guard_commit(repository: Path) -> str:
    _guard_git(repository, "add", ".")
    _guard_git(repository, "commit", "--allow-empty", "-m", "synthetic guard fixture")
    return _guard_git(repository, "rev-parse", "HEAD")


@pytest.fixture
def verified_push_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    _guard_git(tmp_path, "init", "-b", "codex/qir/test-push")
    _guard_git(tmp_path, "config", "user.name", "Guard Test")
    _guard_git(tmp_path, "config", "user.email", "guard@example.invalid")
    repository_url = "https://github.com/synthetic-fixture/guard-tests.git"
    _guard_git(tmp_path, "remote", "add", "origin", repository_url)
    (tmp_path / "governance").mkdir()
    (tmp_path / "governance/standards.json").write_text(
        json.dumps({"project": {"repository_url": repository_url}})
    )
    (tmp_path / "source.py").write_text("print('synthetic fixture')\n")
    source_sha = _guard_commit(tmp_path)
    _guard_git(tmp_path, "switch", "-c", "integration/test-push")
    candidate_sha = _guard_commit(tmp_path)
    delivery = {
        "current_stage": {"id": "R2"},
        "owner_authorization": {
            "status": "active",
            "authorization_id": "synthetic-test-authority",
            "scope": {"lineage_anchor_sha": source_sha},
        },
        "private_preview": {
            "production_evidence": False,
            "real_evidence_authorization": {
                "status": "active",
                "preserves_authorization_anchor": "synthetic-test-authority",
                "stage": "R2",
                "environment": "private_authenticated_real_research_preview",
                "lineage_anchor_sha": source_sha,
                "allowed_git_refs": ["codex/**", "integration/**"],
                "actions": ["nonforce_integration_push"],
            },
        },
        "integration_candidate": {
            "branch": "integration/test-push",
            "source_branch": "codex/qir/test-push",
            "source_sha": source_sha,
            "status": "verified_in_synthetic_environment",
            "final_review_status": "approved",
            "final_review_target_sha": source_sha,
            "last_full_verification_sha": candidate_sha,
            "verification_evidence": {
                "result": "passed",
                "attempt_sha": candidate_sha,
                "full_gate_exit": 0,
            },
        },
    }
    (tmp_path / "governance/delivery.json").write_text(json.dumps(delivery))
    _guard_commit(tmp_path)
    monkeypatch.setattr("scripts.agent_guard.ROOT", tmp_path)
    return tmp_path, candidate_sha


def _candidate_command(candidate_sha: str) -> str:
    return f"git push origin {candidate_sha}:refs/heads/integration/test-push"


@pytest.mark.parametrize("include_tool_cwd", (False, True))
@pytest.mark.parametrize("options", ("-ld", "-ldO"))
def test_agent_guard_allows_only_common_directory_file_attributes(
    verified_push_repository: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    include_tool_cwd: bool,
    options: str,
) -> None:
    repository, _ = verified_push_repository
    metadata = repository / ".git/worktrees/broken-history/commondir"
    metadata.parent.mkdir(parents=True)
    # The file may be absent: ls must be able to diagnose that without reading contents.
    command = f"/bin/ls {options} -- {metadata}"
    payload = {"command": command}
    if include_tool_cwd:
        payload["workdir"] = str(repository)
    read = agent_guard._push_git_read

    def common_directory_only(
        path: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str] | None:
        assert arguments == ("rev-parse", "--path-format=absolute", "--git-common-dir")
        return read(path, *arguments)

    monkeypatch.setattr(agent_guard, "_push_git_read", common_directory_only)
    assert _blocked_reason(payload) is None


@pytest.mark.parametrize(
    "template",
    (
        "ls -ld -- {path}",
        "/bin/ls -l -- {path}",
        "/bin/ls -ldL -- {path}",
        "/bin/ls -ld -- {path} {path}",
        "/bin/ls -ld -- {path} > /tmp/result",
        "/bin/ls -ld -- {path}; echo done",
        "bash -c '/bin/ls -ld -- {path}'",
        "/bin/cat -- {path}",
        "touch {path}",
        "cp /tmp/input {path}",
        "rm {path}",
        "/usr/bin/stat {path}",
    ),
)
def test_agent_guard_metadata_diagnostic_does_not_authorize_other_operations(
    verified_push_repository: tuple[Path, str], template: str
) -> None:
    repository, _ = verified_push_repository
    metadata = repository / ".git/worktrees/broken-history/commondir"
    assert _blocked_reason({"command": template.format(path=metadata)})


@pytest.mark.parametrize("relative", ("config", "worktrees/history/gitdir", "worktrees/history"))
def test_agent_guard_metadata_diagnostic_excludes_other_administrative_files(
    verified_push_repository: tuple[Path, str], relative: str
) -> None:
    repository, _ = verified_push_repository
    path = repository / ".git" / relative
    assert _blocked_reason({"command": f"/bin/ls -ld -- {path}"})


def test_agent_guard_metadata_diagnostic_rejects_foreign_repository_and_parent_symlink(
    verified_push_repository: tuple[Path, str], tmp_path: Path
) -> None:
    repository, _ = verified_push_repository
    foreign = repository.parent / f"{repository.name}-foreign"
    foreign.mkdir()
    foreign_metadata = foreign / ".git/worktrees/history/commondir"
    assert _blocked_reason({"command": f"/bin/ls -ld -- {foreign_metadata}"})
    metadata_parent = tmp_path / ".git/worktrees/history"
    metadata_parent.parent.mkdir(parents=True)
    metadata_parent.symlink_to(foreign, target_is_directory=True)
    assert _blocked_reason({"command": f"/bin/ls -ld -- {metadata_parent / 'commondir'}"})


def test_agent_guard_metadata_leaf_symlink_never_authorizes_content_access(
    verified_push_repository: tuple[Path, str],
) -> None:
    repository, _ = verified_push_repository
    metadata = repository / ".git/worktrees/history/commondir"
    metadata.parent.mkdir(parents=True)
    metadata.symlink_to(repository / "synthetic-nonsecret-target")
    assert _blocked_reason({"command": f"/bin/ls -ld -- {metadata}"}) is None
    assert _blocked_reason({"command": f"cat {metadata}"})


def test_agent_guard_metadata_diagnostic_fails_closed_when_common_directory_unknown(
    verified_push_repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _ = verified_push_repository
    monkeypatch.setattr(agent_guard, "_push_git_read", lambda *_arguments: None)
    path = repository / ".git/worktrees/history/commondir"
    assert _blocked_reason({"command": f"/bin/ls -ld -- {path}"})


@pytest.fixture
def linked_main_repository(verified_push_repository: tuple[Path, str]) -> Path:
    repository, _ = verified_push_repository
    linked = repository.parent / f"{repository.name}-linked-main"
    _guard_git(repository, "worktree", "add", "-b", "main", str(linked))
    return linked


@pytest.mark.parametrize("load_from_main", (False, True))
def test_agent_guard_allows_registered_candidate_from_either_policy_location(
    verified_push_repository: tuple[Path, str],
    linked_main_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_from_main: bool,
) -> None:
    repository, candidate_sha = verified_push_repository
    monkeypatch.setattr(
        agent_guard, "ROOT", linked_main_repository if load_from_main else repository
    )
    payload = {
        "cwd": str(linked_main_repository),
        "tool_input": {"cmd": _candidate_command(candidate_sha), "workdir": str(repository)},
    }
    assert _blocked_reason(payload) is None
    # Sharing Git metadata does not authorize pushing from the main checkout.
    assert _blocked_reason(
        {"cmd": _candidate_command(candidate_sha), "workdir": str(linked_main_repository)}
    )


def test_agent_guard_rejects_unlinked_clone_with_matching_candidate_and_origin(
    verified_push_repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, candidate_sha = verified_push_repository
    clone = repository.parent / f"{repository.name}-clone"
    _guard_git(repository, "clone", "--no-hardlinks", str(repository), str(clone))
    delivery = json.loads((clone / "governance/delivery.json").read_text())
    candidate = delivery["integration_candidate"]
    _guard_git(clone, "branch", candidate["source_branch"], candidate["source_sha"])
    _guard_git(
        clone, "remote", "set-url", "origin", "https://github.com/synthetic-fixture/guard-tests.git"
    )
    payload = {"cmd": _candidate_command(candidate_sha), "workdir": str(clone)}
    monkeypatch.setattr(agent_guard, "ROOT", clone)
    assert _blocked_reason(payload) is None
    monkeypatch.setattr(agent_guard, "ROOT", repository)
    assert _blocked_reason(payload) == "destructive Git command"


@pytest.mark.parametrize("failure", ("common_directory", "registration", "unregistered"))
def test_agent_guard_requires_successful_linked_repository_registration(
    verified_push_repository: tuple[Path, str],
    linked_main_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    repository, candidate_sha = verified_push_repository
    monkeypatch.setattr(agent_guard, "ROOT", linked_main_repository)
    read = agent_guard._push_git_read

    def incomplete_metadata(path: Path, *arguments: str) -> subprocess.CompletedProcess[str] | None:
        if failure == "common_directory" and "--git-common-dir" in arguments:
            return None
        if arguments[:2] == ("worktree", "list"):
            if failure == "registration":
                return None
            if failure == "unregistered":
                return subprocess.CompletedProcess(arguments, 0, "", "")
        return read(path, *arguments)

    monkeypatch.setattr(agent_guard, "_push_git_read", incomplete_metadata)
    assert _blocked_reason({"cmd": _candidate_command(candidate_sha), "workdir": str(repository)})


def test_agent_guard_rejects_candidate_subdirectory_and_relative_or_conflicting_cwd(
    verified_push_repository: tuple[Path, str], linked_main_repository: Path
) -> None:
    repository, candidate_sha = verified_push_repository
    for command in (_candidate_command(candidate_sha), "git commit -m checked"):
        for directory in (".", None, 5):
            assert _blocked_reason({"cmd": command, "workdir": directory})
        assert _blocked_reason(
            {"cmd": command, "workdir": str(repository), "cwd": str(linked_main_repository)}
        )
    assert _blocked_reason(
        {"cmd": _candidate_command(candidate_sha), "workdir": str(repository / "governance")}
    )


@pytest.mark.parametrize("verb", ("commit", "merge", "rebase", "cherry-pick"))
@pytest.mark.parametrize("load_from_main", (False, True))
def test_agent_guard_checks_mutation_branch_in_actual_tool_directory(
    verified_push_repository: tuple[Path, str],
    linked_main_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    load_from_main: bool,
) -> None:
    repository, _ = verified_push_repository
    monkeypatch.setattr(
        agent_guard, "ROOT", linked_main_repository if load_from_main else repository
    )
    command = f"git {verb} checked"
    assert (
        _blocked_reason({"tool_input": {"cmd": command, "workdir": str(linked_main_repository)}})
        == "destructive Git command"
    )
    assert (
        _blocked_reason(
            {
                "cwd": str(linked_main_repository),
                "tool_input": {"cmd": command, "workdir": str(repository)},
            }
        )
        is None
    )


@pytest.mark.parametrize("absolute", (False, True))
def test_agent_guard_resolves_git_c_relative_to_actual_tool_directory(
    verified_push_repository: tuple[Path, str],
    linked_main_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    absolute: bool,
) -> None:
    repository, _ = verified_push_repository
    monkeypatch.setattr(agent_guard, "ROOT", linked_main_repository)
    for destination, allowed in ((linked_main_repository, False), (repository, True)):
        operand = str(destination) if absolute else destination.name
        payload = {
            "cwd": str(linked_main_repository),
            "tool_input": {
                "cmd": f"git -C {operand} commit -m checked",
                "workdir": str(repository.parent),
            },
        }
        assert (_blocked_reason(payload) is None) is allowed


@pytest.mark.parametrize(
    "command_template",
    (
        "cd {main} && git commit -m checked",
        "cd {main}; git merge checked",
        "bash -lc 'cd {main} && git rebase checked'",
        "bash -o pipefail -c 'git -C {main} commit -m checked'",
        "zsh -o errexit -c 'git -C {main} commit -m checked'",
        "env -C {main} git cherry-pick checked",
        "echo $(git -C {main} commit -m nested)",
        "x=$(git -C {main} commit -m nested)",
        "`git -C {main} commit -m nested`",
        "(git -C {main} commit -m nested)",
        'echo "$(git -C {main} commit -m nested)"',
        'echo "`git -C {main} commit -m nested`"',
        'result="$(git -C {main} commit -m nested)"',
    ),
)
def test_agent_guard_rejects_shell_directory_switch_before_mutation(
    verified_push_repository: tuple[Path, str],
    linked_main_repository: Path,
    command_template: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _ = verified_push_repository
    payload = {
        "cmd": command_template.format(main=linked_main_repository),
        "workdir": str(repository),
    }
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))
    assert _protect() == 2


@pytest.mark.parametrize("operation", ("probe", "push", "denied_push"))
def test_agent_guard_reports_only_safe_loading_provenance(
    verified_push_repository: tuple[Path, str],
    linked_main_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    repository, candidate_sha = verified_push_repository
    monkeypatch.setattr(agent_guard, "ROOT", linked_main_repository)
    commands = {
        "probe": "git rev-parse --show-toplevel",
        "push": _candidate_command(candidate_sha),
        "denied_push": "git push https://synthetic-marker@example.invalid/repository.git main",
    }
    command = commands[operation]
    payload = {
        "cwd": str(linked_main_repository),
        "tool_input": {"cmd": command, "workdir": str(repository)},
        "ignored_payload": "synthetic-raw-input-marker",
    }
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))
    denied = operation == "denied_push"
    assert _protect() == (2 if denied else 0)
    captured = capsys.readouterr()
    if denied:
        assert captured.out == ""
        context = json.loads(captured.err.splitlines()[0])
    else:
        assert captured.err == ""
        hook_output = json.loads(captured.out)["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "PreToolUse"
        context = json.loads(hook_output["additionalContext"])
    assert context == {
        "guard_script": str(Path(agent_guard.__file__).resolve()),
        "command_workdir": str(repository),
        "git_repository": str(repository),
        "decision": "deny" if denied else "allow",
    }
    for forbidden in (command, "synthetic-marker", "synthetic-raw-input-marker", "https://"):
        assert forbidden not in captured.out + captured.err


def test_agent_guard_keeps_ordinary_checks_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({"cmd": "git status --short"})))
    assert _protect() == 0
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_agent_guard_binds_explicit_git_directory_when_host_omits_tool_workdir(
    verified_push_repository: tuple[Path, str],
    linked_main_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository, candidate_sha = verified_push_repository
    monkeypatch.setattr(agent_guard, "ROOT", linked_main_repository)
    command = _candidate_command(candidate_sha).replace("git ", f"git -C {repository} ", 1)
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "exec_command",
        "cwd": str(linked_main_repository),
        "tool_input": {"command": command},
    }
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))
    assert _protect() == 0
    context = json.loads(
        json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    )
    assert context["command_workdir"] is None
    assert context["git_repository"] == str(repository)
    # A session cwd does not establish the actual exec tool directory.
    for implicit in (_candidate_command(candidate_sha), "git commit -m unknown"):
        payload["tool_input"] = {"command": implicit}
        assert _blocked_reason(payload)
    for path, allowed in ((repository, True), (linked_main_repository, False)):
        payload["tool_input"] = {"command": f"git -C {path} commit -m checked"}
        assert (_blocked_reason(payload) is None) is allowed
    (repository / "source.py").write_text("print('unreviewed')\n")
    payload["tool_input"] = {"command": command}
    assert _blocked_reason(payload)


@pytest.mark.parametrize(
    "arguments",
    (
        "-f origin {sha}:refs/heads/integration/test-push",
        "--force-with-lease origin {sha}:refs/heads/integration/test-push",
        "origin +{sha}:refs/heads/integration/test-push",
        "origin {sha}:refs/heads/main",
        "origin {sha}:refs/tags/release",
        "origin :refs/heads/integration/test-push",
        "--delete origin integration/test-push",
        "--mirror origin",
        "origin {sha}:refs/heads/integration/test-push; true",
    ),
)
def test_agent_guard_preserves_dangerous_push_denials_with_explicit_git_directory(
    verified_push_repository: tuple[Path, str],
    linked_main_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: str,
) -> None:
    repository, sha = verified_push_repository
    monkeypatch.setattr(agent_guard, "ROOT", linked_main_repository)
    command = f"git -C {repository} push {arguments.format(sha=sha)}"
    assert _blocked_reason({"cwd": str(linked_main_repository), "tool_input": {"cmd": command}})


def test_agent_guard_does_not_execute_literal_search_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_guard, "_current_branch", lambda _: "main")
    assert _blocked_reason({"cmd": "rg -n 'git commit' tests"}) is None
    assert _blocked_reason({"cmd": "echo 'git commit -m literal'"}) is None
    assert _blocked_reason({"cmd": "echo '$(git commit -m literal)'"}) is None
    assert _blocked_reason({"cmd": "bash -lc 'git commit -m executed'"})
    assert _blocked_reason({"cmd": "rg -n 'text' tests; git commit -m executed"})


def test_agent_guard_allows_verified_exact_push_after_status_commit(
    verified_push_repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, candidate_sha = verified_push_repository
    assert _guard_git(repository, "rev-parse", "HEAD") != candidate_sha
    payload = {
        "cwd": str(repository.parent),
        "tool_input": {"cmd": _candidate_command(candidate_sha), "workdir": str(repository)},
    }
    assert _blocked_reason(payload) is None
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))
    assert _protect() == 0


@pytest.mark.parametrize(
    "command_template",
    (
        "git push origin HEAD:refs/heads/integration/test-push",
        "git push other {sha}:refs/heads/integration/test-push",
        "git push origin {sha}:refs/heads/main",
        "git push origin {sha}:refs/tags/test",
        "git push origin {sha}:refs/heads/integration/other",
        "git push -f origin {sha}:refs/heads/integration/test-push",
        "git push --force-with-lease origin {sha}:refs/heads/integration/test-push",
        "git push origin +{sha}:refs/heads/integration/test-push",
        "git push --mirror origin",
        "git push --delete origin integration/test-push",
        "git push origin :refs/heads/integration/test-push",
        "git push --tags origin",
        "git push --follow-tags origin {sha}:refs/heads/integration/test-push",
        "git -c push.followTags=true push origin {sha}:refs/heads/integration/test-push",
        "git -C relative push origin {sha}:refs/heads/integration/test-push",
        "git --git-dir={root} push origin {sha}:refs/heads/integration/test-push",
        "git push origin {sha}:refs/heads/integration/test-push >/tmp/result",
        "git push origin {sha}:refs/heads/integration/test-push; true",
        "cd {root} && git push origin {sha}:refs/heads/integration/test-push",
        "bash -lc 'git push origin {sha}:refs/heads/integration/test-push'",
    ),
)
def test_agent_guard_keeps_other_push_forms_blocked(
    verified_push_repository: tuple[Path, str], command_template: str
) -> None:
    repository, candidate_sha = verified_push_repository
    command = command_template.format(sha=candidate_sha, root=repository)
    assert _blocked_reason({"tool_input": {"cmd": command, "workdir": str(repository)}})


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("current_stage", "id"), "R7"),
        (("owner_authorization", "status"), "revoked"),
        (("private_preview", "production_evidence"), True),
        (("private_preview", "real_evidence_authorization", "status"), "revoked"),
        (("private_preview", "real_evidence_authorization", "actions"), []),
        (
            ("private_preview", "real_evidence_authorization", "actions"),
            "nonforce_integration_push",
        ),
        (("private_preview", "real_evidence_authorization", "allowed_git_refs"), []),
        (("private_preview", "real_evidence_authorization", "environment"), "production"),
        (("private_preview", "real_evidence_authorization", "lineage_anchor_sha"), "a" * 40),
        (("integration_candidate", "status"), "pending_final_verification"),
        (("integration_candidate", "final_review_status"), "pending"),
        (("integration_candidate", "final_review_target_sha"), "b" * 40),
        (("integration_candidate", "last_full_verification_sha"), "c" * 40),
        (("integration_candidate", "verification_evidence", "result"), "failed"),
        (("integration_candidate", "verification_evidence", "attempt_sha"), "d" * 40),
        (("integration_candidate", "verification_evidence", "full_gate_exit"), 2),
        (("integration_candidate", "verification_evidence", "full_gate_exit"), False),
    ),
)
def test_agent_guard_requires_matching_review_gate_and_authority(
    verified_push_repository: tuple[Path, str], path: tuple[str, ...], value: object
) -> None:
    repository, candidate_sha = verified_push_repository
    file = repository / "governance/delivery.json"
    delivery = json.loads(file.read_text())
    current = delivery
    for name in path[:-1]:
        current = current[name]
    current[path[-1]] = value
    file.write_text(json.dumps(delivery))
    _guard_commit(repository)
    assert _blocked_reason({"cmd": _candidate_command(candidate_sha), "workdir": str(repository)})


@pytest.mark.parametrize("setting", ("push.followTags", "remote.origin.mirror"))
@pytest.mark.parametrize("value", ("true", "invalid"))
def test_agent_guard_rejects_implicit_push_expansion(
    verified_push_repository: tuple[Path, str], setting: str, value: str
) -> None:
    repository, candidate_sha = verified_push_repository
    _guard_git(repository, "config", setting, value)
    assert _blocked_reason({"cmd": _candidate_command(candidate_sha), "workdir": str(repository)})


def test_agent_guard_rejects_wrong_or_conflicting_workdir(
    verified_push_repository: tuple[Path, str],
) -> None:
    repository, candidate_sha = verified_push_repository
    command = _candidate_command(candidate_sha)
    assert _blocked_reason({"cmd": command, "workdir": str(repository.parent)})
    assert _blocked_reason(
        {"tool_input": {"cmd": command, "workdir": str(repository), "cwd": str(repository.parent)}}
    )


@pytest.mark.parametrize("committed", (False, True))
def test_agent_guard_rejects_unverified_worktree_changes(
    verified_push_repository: tuple[Path, str], committed: bool
) -> None:
    repository, candidate_sha = verified_push_repository
    (repository / "source.py").write_text("print('changed fixture')\n")
    if committed:
        _guard_commit(repository)
    assert _blocked_reason({"cmd": _candidate_command(candidate_sha), "workdir": str(repository)})


def test_agent_guard_rejects_source_tip_advanced_after_review(
    verified_push_repository: tuple[Path, str],
) -> None:
    repository, candidate_sha = verified_push_repository
    _guard_git(repository, "branch", "-f", "codex/qir/test-push", "HEAD")
    assert _blocked_reason({"cmd": _candidate_command(candidate_sha), "workdir": str(repository)})


def test_agent_guard_rejects_unknown_or_short_candidate(
    verified_push_repository: tuple[Path, str],
) -> None:
    repository, candidate_sha = verified_push_repository
    for sha in (candidate_sha[:8], "e" * 40):
        assert _blocked_reason({"cmd": _candidate_command(sha), "workdir": str(repository)})
    file = repository / "governance/delivery.json"
    file.write_text("{invalid")
    _guard_commit(repository)
    assert _blocked_reason({"cmd": _candidate_command(candidate_sha), "workdir": str(repository)})


def test_agent_guard_rejects_git_read_timeout(
    verified_push_repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, candidate_sha = verified_push_repository

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("git", 2)

    monkeypatch.setattr("scripts.agent_guard.subprocess.run", timeout)
    assert _blocked_reason({"cmd": _candidate_command(candidate_sha), "workdir": str(repository)})


@pytest.mark.parametrize(
    "setting",
    (
        "remote.origin.url",
        "remote.origin.pushurl",
        "url.https://github.com/unapproved/other.git.pushInsteadOf",
        "url.https://github.com/unapproved/other.git.insteadOf",
    ),
)
def test_agent_guard_rejects_origin_redirects(
    verified_push_repository: tuple[Path, str], setting: str
) -> None:
    repository, candidate_sha = verified_push_repository
    value = (
        "https://github.com/synthetic-fixture/guard-tests.git"
        if setting.startswith("url.")
        else "https://github.com/unapproved/other.git"
    )
    _guard_git(repository, "config", setting, value)
    assert _blocked_reason({"cmd": _candidate_command(candidate_sha), "workdir": str(repository)})


def test_agent_guard_rejects_multiple_push_destinations(
    verified_push_repository: tuple[Path, str],
) -> None:
    repository, candidate_sha = verified_push_repository
    for _ in range(2):
        _guard_git(
            repository,
            "config",
            "--add",
            "remote.origin.pushurl",
            "https://github.com/synthetic-fixture/guard-tests.git",
        )
    assert _blocked_reason({"cmd": _candidate_command(candidate_sha), "workdir": str(repository)})


def test_agent_guard_rejects_unreviewed_destination_registration(
    verified_push_repository: tuple[Path, str],
) -> None:
    repository, candidate_sha = verified_push_repository
    url = "https://github.com/unapproved/other.git"
    (repository / "governance/standards.json").write_text(
        json.dumps({"project": {"repository_url": url}})
    )
    _guard_git(repository, "config", "remote.origin.url", url)
    _guard_commit(repository)
    assert _blocked_reason({"cmd": _candidate_command(candidate_sha), "workdir": str(repository)})


def test_product_first_governance_and_delivery_are_self_consistent() -> None:
    assert validate_governance(ROOT) == []
    assert validate_delivery(ROOT) == []
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Stale Workspace Refusal" in agents
    assert "## Agent May Stop" in agents
    assert "## Independent Model Review Authorization" in agents
    assert "gpt-5.6-sol" in agents
    assert "不需要每次另行请求外发确认" in agents
    assert "## Current Project Context and Owner Authorization" in agents
    assert "owner_authorization" in agents
    assert "## Verification Tiers" in agents


def test_delivery_distinguishes_synthetic_completion_from_production_readiness() -> None:
    delivery = json.loads((ROOT / "governance/delivery.json").read_text(encoding="utf-8"))

    assert delivery["current_stage"]["id"] == "R2"
    assert delivery["current_stage"]["status"] in {
        "in_progress",
        "completed_in_synthetic_environment",
    }
    if delivery["current_stage"]["status"] == "completed_in_synthetic_environment":
        assert delivery["active_work"] == []
    else:
        assert delivery["active_work"]
    assert delivery["stage_sequence"] == ["R0", "R0P", "R1", "R2", "R3", "R4", "R5", "R6"]
    assert delivery["r1_exit_gates"]["blocking"] == []
    assert set(delivery["r1_exit_gates"]["satisfied"]) == {
        "FULL_STACK_SYNTHETIC_E2E_VERIFIED",
        "PIT_CORRECTION_CONFLICT_IDEMPOTENCY_RECOVERY_VERIFIED",
        "EVIDENCE_DRILLDOWN_VERIFIED",
        "FRESH_MAKE_VERIFY_VERIFIED",
    }
    assert "AUTHORIZED_SOURCE_NOT_VERIFIED" not in delivery["r1_exit_gates"]
    assert {item["id"] for item in delivery["production_release_gates"]["gates"]} == {
        "PRODUCTION_SOURCE_AUTHORIZATION_NOT_VERIFIED",
        "PRODUCTION_SHADOW_TRIAL_NOT_COMPLETED",
        "PRODUCTION_LIVE_E2E_NOT_VERIFIED",
        "PRODUCTION_MODEL_RUNTIME_NOT_QUALIFIED",
    }
    assert all(
        item["status"] == "blocking" for item in delivery["production_release_gates"]["gates"]
    )
    candidate = delivery["integration_candidate"]
    assert candidate["branch"].startswith("integration/")
    assert candidate["status"] in {
        "pending_final_verification",
        "verified_in_synthetic_environment",
    }
    assert validate_delivery(ROOT) == []
    assert delivery["private_preview"]["production_evidence"] is False
    assert next(item for item in delivery["milestones"] if item["id"] == "R1") == {
        "id": "R1",
        "status": "completed_in_synthetic_environment",
    }
    assert next(item for item in delivery["milestones"] if item["id"] == "R2") == {
        "id": "R2",
        "status": "completed_in_synthetic_environment",
    }
    serialized = json.dumps(delivery, ensure_ascii=False)
    assert "R2_CORE" not in serialized
    assert "R2A" not in serialized
    assert "R2B" not in serialized
    assert delivery["product_checkpoints"]["COMMERCIAL_READINESS"] is False
    assert delivery["product_checkpoints"]["R2_SYNTHETIC"] == ("completed_in_synthetic_environment")
    assert delivery["product_checkpoints"]["R2_SEARCH_GLOBAL_EVENTS_RESEARCH_REPORT"] == (
        "completed_in_synthetic_environment"
    )
    assert delivery["private_preview"]["main"] == "unchanged"


def test_owner_authorization_is_closed_and_scope_limited() -> None:
    delivery = json.loads((ROOT / "governance/delivery.json").read_text(encoding="utf-8"))
    authorization = delivery["owner_authorization"]

    assert _validate_owner_authorization(authorization) == []
    assert authorization["inherits_to"] == [
        "new_project_conversations_loading_this_record",
        "spawned_agents_loading_this_record",
    ]
    assert authorization["host_approvals_inherited"] is False
    assert "credential_reuse_from_prior_conversations" in authorization["not_authorized"]
    assert "local_or_remote_main_update" in authorization["not_authorized"]


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    (
        (("scope", "allowed_git_refs"), ["codex/**", "integration/**", "main"], "Git refs"),
        (
            ("scope", "lineage_anchor_sha"),
            "0000000000000000000000000000000000000000",
            "recorded lineage anchor",
        ),
        (("preauthorized_actions",), ["push_remote_main"], "closed allowlist"),
        (("not_authorized",), [], "required denial"),
        (("conditions",), ["always"], "required safety boundary"),
        (("stop_conditions",), ["never"], "required safety boundary"),
        (("host_approvals_inherited",), True, "cannot be inherited"),
        (("secret_policy",), "reuse_pasted_keys", "literal credentials"),
        (("platform_boundary",), "repository_overrides_platform", "platform boundaries"),
    ),
)
def test_owner_authorization_rejects_scope_expansion(
    path: tuple[str, ...], value: object, expected: str
) -> None:
    delivery = json.loads((ROOT / "governance/delivery.json").read_text(encoding="utf-8"))
    authorization = deepcopy(delivery["owner_authorization"])
    target = authorization
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    assert any(expected in error for error in _validate_owner_authorization(authorization))


def test_architecture_interfaces_metrics_and_repository_policy_remain_hard_gates() -> None:
    violations, errors = validate_architecture(ROOT)
    assert violations == []
    assert errors == []
    assert validate_interfaces(ROOT) == []
    assert validate_metrics(ROOT) == []
    assert validate_repository(ROOT) == []


def test_delivery_keeps_one_executor_and_the_required_safeguards() -> None:
    delivery = json.loads((ROOT / "governance/delivery.json").read_text(encoding="utf-8"))
    ordinary = delivery["ordinary_delivery"]

    assert ordinary["executor_paths"] == ["scripts/lightweight_agent.py"]
    assert ordinary["required_definition"] == [
        "user_capability",
        "runnable_path",
        "acceptance_evidence",
        "non_goals",
    ]
    assert len(ordinary["steps"]) == 5
    assert set(delivery["retained_safeguards"]) == {
        "no_real_trading",
        "production_source_rights_fail_closed",
        "non_production_label_until_all_production_gates_pass",
        "evidence_id",
        "point_in_time_and_bitemporal",
        "idempotency",
        "stable_types_and_errors",
        "independent_final_head_review",
        "main_ff_only",
        "revert_or_forward_fix",
    }


def test_retired_governance_files_have_no_active_copy() -> None:
    assert all(not (ROOT / relative).exists() for relative in LEGACY_FILES)


def test_hooks_ci_make_and_precommit_use_the_product_first_path() -> None:
    for relative in (".codex/hooks.json", ".claude/settings.json"):
        hooks = json.loads((ROOT / relative).read_text(encoding="utf-8"))["hooks"]
        assert "Stop" not in hooks
        assert "PreToolUse" in hooks

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    precommit = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "remote-traceability" not in makefile
    assert "remote-governance" not in makefile
    assert "refs/ci/verified" not in ci
    assert "contents: write" not in ci
    assert "pass_filenames: false" not in precommit
    assert "repo: local" not in precommit
    assert all(
        f"- id: {hook}" in precommit
        for hook in ("ruff", "ruff-format", "check-merge-conflict", "detect-private-key")
    )


@pytest.mark.parametrize(
    "path",
    (
        ".env",
        "./.env",
        ".git/config",
        "data/private/account.json",
        "data/raw/source.json",
        "secrets/token.txt",
        "artifacts/models/model.gguf",
        "../outside/.env.local",
        "/outside/data/private/account.json",
        "/outside/key.pem",
    ),
)
def test_agent_guard_recognizes_relative_and_absolute_sensitive_paths(path: str) -> None:
    assert _sensitive(path)


def test_agent_guard_keeps_the_explicit_environment_example_exception() -> None:
    assert not _sensitive(".env.example")
    assert not _sensitive("./.env.example")


@pytest.mark.parametrize(
    "command",
    (
        "cat .env",
        "python tool.py --input data/private/account.json",
        "cp data/raw/source.json /tmp/source.json",
        "sed -n 1p .git/config",
        "python loader.py --weights=artifacts/models/model.gguf",
        "openssl rsa -in /outside/key.pem",
        "bash -lc 'cat .env'",
        "sh -c \"bash -lc 'cp data/raw/source.json /tmp/source.json'\"",
    ),
)
def test_agent_guard_checks_sensitive_paths_inside_shell_commands(command: str) -> None:
    assert _blocked_reason({"command": command}) is not None


@pytest.mark.parametrize(
    "command",
    (
        "git reset --hard HEAD^",
        "git clean -fd",
        "git clean -fdx",
        "git push -f origin main",
        "git push --force-with-lease origin main",
        "git push origin feature",
        "git tag release-candidate",
        "git branch -D obsolete",
        "git checkout -- tracked.txt",
        "git restore tracked.txt",
        "git commit --no-verify -m unsafe",
        "git -C /tmp/repo reset --hard HEAD",
        "bash -lc 'git -C /tmp/repo push --force origin main'",
        "git worktree remove .",
    ),
)
def test_agent_guard_blocks_destructive_git_forms(command: str) -> None:
    assert _blocked_reason({"tool_input": {"cmd": command}}) == "destructive Git command"


def test_agent_guard_allows_non_destructive_git_and_fails_closed_on_bad_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.agent_guard._current_branch", lambda _repository: "feature/test")
    assert _blocked_reason({"command": "git status --short"}) is None
    assert _blocked_reason({"command": "git branch -d merged"}) is None
    assert _blocked_reason({"command": "git commit -m safe", "workdir": str(ROOT)}) is None
    assert _blocked_reason({"command": "git tag --list"}) is None
    assert _blocked_reason({"command": "git worktree prune"}) is None
    for raw in ("", "{", "[]", "{}"):
        with pytest.raises(GuardInputError):
            _decode_payload(raw)
        monkeypatch.setattr(sys, "stdin", StringIO(raw))
        assert _protect() == 2


def test_agent_guard_blocks_advancing_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.agent_guard._current_branch", lambda _repository: "main")
    assert _blocked_reason({"command": "git commit -m release", "workdir": str(ROOT)}) == (
        "destructive Git command"
    )
    assert (
        _blocked_reason({"command": "git -C /tmp/other-repo merge release"})
        == "destructive Git command"
    )


@pytest.mark.parametrize(
    ("candidate_status", "preview_status", "reviewed"),
    [
        ("pending_final_verification", "deployed_retest_blocked", False),
        ("pending_final_verification", "deployed_canary_incomplete", True),
        ("verified_in_synthetic_environment", "deployed_and_canary_verified", True),
    ],
)
def test_delivery_accepts_distinct_candidate_and_running_versions(
    monkeypatch: pytest.MonkeyPatch, candidate_status: str, preview_status: str, reviewed: bool
) -> None:
    delivery = json.loads((ROOT / "governance/delivery.json").read_text())
    delivery["integration_candidate"].update(
        status=candidate_status,
        source_sha="a" * 40,
        last_full_verification_sha="b" * 40 if reviewed else None,
        final_review_target_sha="a" * 40 if reviewed else None,
        final_review_status="approved" if reviewed else "pending",
    )
    delivery["private_preview"].update(status=preview_status, product_sha="c" * 40)
    monkeypatch.setattr("scripts.check_delivery_registry._load", lambda _root: delivery)
    assert validate_delivery(ROOT) == []


@pytest.mark.parametrize(
    ("section", "field", "value", "expected"),
    [
        ("integration_candidate", "source_sha", None, "source_sha"),
        ("integration_candidate", "last_full_verification_sha", "bad", "verification SHA"),
        ("integration_candidate", "final_review_target_sha", "d" * 40, "review"),
        ("integration_candidate", "final_review_target_sha", None, "review"),
        ("integration_candidate", "final_review_status", "pending", "review"),
        ("integration_candidate", "status", "ready", "status"),
        ("private_preview", "status", "unknown", "canary status"),
        ("private_preview", "product_sha", "bad", "ProductSha"),
        ("private_preview", "production_evidence", True, "production evidence"),
        ("private_preview", "main", "advanced", "main advancement"),
        ("private_preview", "formal_tag", "created", "formal tag"),
    ],
)
def test_delivery_lifecycle_keeps_identity_and_release_boundaries(
    monkeypatch: pytest.MonkeyPatch, section: str, field: str, value: object, expected: str
) -> None:
    delivery = json.loads((ROOT / "governance/delivery.json").read_text())
    delivery["integration_candidate"].update(
        status="pending_final_verification",
        source_sha="a" * 40,
        last_full_verification_sha="b" * 40,
        final_review_target_sha="a" * 40,
        final_review_status="approved",
    )
    delivery["private_preview"].update(status="deployed_canary_incomplete", product_sha="c" * 40)
    delivery[section][field] = value
    monkeypatch.setattr("scripts.check_delivery_registry._load", lambda _root: delivery)
    assert any(expected in error for error in validate_delivery(ROOT))


def test_verified_candidate_requires_a_completed_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = json.loads((ROOT / "governance/delivery.json").read_text())
    delivery["integration_candidate"].update(
        status="verified_in_synthetic_environment", last_full_verification_sha=None
    )
    monkeypatch.setattr("scripts.check_delivery_registry._load", lambda _root: delivery)
    assert "verified candidate requires an immutable verification SHA" in validate_delivery(ROOT)
