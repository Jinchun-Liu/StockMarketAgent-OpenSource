import argparse
import json
import subprocess
from pathlib import Path

import pytest
from scripts import lightweight_agent


def _task(path: Path) -> None:
    path.write_text(
        """User Capability: run the feature
Runnable Path: CLI
Acceptance Evidence: focused test
Non-goals: release
""",
        encoding="utf-8",
    )


def _repository_policy(root: Path) -> None:
    governance = root / "governance"
    governance.mkdir(parents=True)
    (governance / "standards.json").write_text(
        json.dumps(
            {
                "protected_paths": {
                    "exceptions": [".env.example"],
                    "exact": [".env"],
                    "prefixes": [".git/", "data/private/", "data/raw/"],
                    "name_prefixes": [".env."],
                    "suffixes": [".pem", ".key", ".gguf"],
                }
            }
        ),
        encoding="utf-8",
    )


def test_build_command_is_one_ephemeral_codex_path() -> None:
    command = lightweight_agent.build_command(
        binary="/opt/codex",
        model="test-model",
        sandbox="workspace-write",
    )

    assert command[:3] == ("/opt/codex", "exec", "--ephemeral")
    assert command.count("exec") == 1
    assert command[-1] == "-"
    assert "workspace-write" in command
    assert "sandbox_workspace_write.network_access=false" in command

    review = lightweight_agent.build_command(
        binary="/opt/codex",
        model="test-model",
        sandbox="read-only",
    )
    assert "read-only" in review
    assert "sandbox_workspace_write.network_access=false" not in review


def test_task_text_requires_the_four_product_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _repository_policy(root)
    monkeypatch.setattr(lightweight_agent, "ROOT", root)
    prompt = root / "task.txt"
    prompt.write_text("User Capability: incomplete\n", encoding="utf-8")

    with pytest.raises(lightweight_agent.DeliveryError, match="Runnable Path"):
        lightweight_agent._task_text(str(prompt))


def test_task_text_allows_runtime_tasks_and_environment_example(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _repository_policy(root)
    monkeypatch.setattr(lightweight_agent, "ROOT", root)
    runtime_prompt = root / ".runtime/tasks/task.txt"
    runtime_prompt.parent.mkdir(parents=True)
    _task(runtime_prompt)
    example = root / ".env.example"
    _task(example)

    assert "User Capability" in lightweight_agent._task_text(str(runtime_prompt))
    assert "User Capability" in lightweight_agent._task_text(str(example))


@pytest.mark.parametrize("relative", (".env", "data/private/task.txt", "model.gguf"))
def test_task_text_rejects_protected_repository_paths(
    relative: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _repository_policy(root)
    monkeypatch.setattr(lightweight_agent, "ROOT", root)
    prompt = root / relative
    prompt.parent.mkdir(parents=True, exist_ok=True)
    _task(prompt)

    with pytest.raises(lightweight_agent.DeliveryError, match="protected"):
        lightweight_agent._task_text(str(prompt))


def test_task_text_rejects_files_outside_the_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _repository_policy(root)
    monkeypatch.setattr(lightweight_agent, "ROOT", root)
    prompt = tmp_path / "outside-task.txt"
    _task(prompt)

    with pytest.raises(lightweight_agent.DeliveryError, match="inside the repository"):
        lightweight_agent._task_text(str(prompt))


def test_review_binds_approval_to_clean_final_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = "a" * 40
    base = "b" * 40
    prompt = tmp_path / "task.txt"
    _task(prompt)

    def fake_git(*arguments: str, check: bool = True) -> str:
        if arguments == ("branch", "--show-current"):
            return "codex/qir/example"
        if arguments == ("status", "--porcelain"):
            return ""
        if arguments == ("rev-parse", f"{target}^{{commit}}"):
            return target
        if arguments == ("rev-parse", f"{base}^{{commit}}"):
            return base
        if arguments == ("rev-parse", "HEAD"):
            return target
        raise AssertionError(arguments)

    monkeypatch.setattr(lightweight_agent, "_git", fake_git)
    monkeypatch.setattr(lightweight_agent, "_git_succeeds", lambda *_args: True)
    monkeypatch.setattr(lightweight_agent, "_task_text", lambda _path: "Task")
    monkeypatch.setattr(
        lightweight_agent,
        "_codex",
        lambda *_args: subprocess.CompletedProcess(
            args=("codex",),
            returncode=0,
            stdout="APPROVED\n",
            stderr="",
        ),
    )

    lightweight_agent.review_agent(
        argparse.Namespace(
            base_ref=base,
            target_commit=target,
            prompt_file=str(prompt),
            model="test-model",
            codex_binary="codex",
        )
    )


def test_review_fails_closed_on_requested_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = "a" * 40
    base = "b" * 40
    prompt = tmp_path / "task.txt"
    _task(prompt)

    def fake_git(*arguments: str, check: bool = True) -> str:
        if arguments == ("branch", "--show-current"):
            return "codex/qir/example"
        if arguments == ("status", "--porcelain"):
            return ""
        if arguments == ("rev-parse", f"{target}^{{commit}}"):
            return target
        if arguments == ("rev-parse", f"{base}^{{commit}}"):
            return base
        if arguments == ("rev-parse", "HEAD"):
            return target
        raise AssertionError(arguments)

    monkeypatch.setattr(lightweight_agent, "_git", fake_git)
    monkeypatch.setattr(lightweight_agent, "_git_succeeds", lambda *_args: True)
    monkeypatch.setattr(lightweight_agent, "_task_text", lambda _path: "Task")
    monkeypatch.setattr(
        lightweight_agent,
        "_codex",
        lambda *_args: subprocess.CompletedProcess(
            args=("codex",),
            returncode=0,
            stdout="CHANGES_REQUESTED\nFix the regression.\n",
            stderr="",
        ),
    )

    with pytest.raises(lightweight_agent.DeliveryError, match="requested changes"):
        lightweight_agent.review_agent(
            argparse.Namespace(
                base_ref=base,
                target_commit=target,
                prompt_file=str(prompt),
                model="test-model",
                codex_binary="codex",
            )
        )
