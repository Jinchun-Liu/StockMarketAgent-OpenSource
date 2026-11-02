#!/usr/bin/env python3
"""Small repository hook for sensitive paths and destructive commands."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_EXCEPTIONS = {
    ".env.example",
    "data/raw/.gitkeep",
    "data/private/.gitkeep",
    "artifacts/models/.gitkeep",
    "artifacts/checkpoints/.gitkeep",
}
SENSITIVE_PARTS = (
    ".git",
    "secrets",
    "data/private",
    "data/raw",
    "artifacts/models",
    "artifacts/checkpoints",
)
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".safetensors", ".gguf"}
COMMAND_KEYS = {"cmd", "command", "shell_command"}
PATH_KEYS = {"cwd", "file", "file_path", "notebook_path", "path", "paths", "workdir"}
PATCH_KEYS = {"input", "patch"}
SHELL_SEPARATORS = {";", "&&", "||", "|"}
PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
MAIN_MUTATIONS = {"cherry-pick", "commit", "merge", "rebase"}


class GuardInputError(ValueError):
    """The hook payload cannot be safely interpreted."""


def _decode_payload(raw: str) -> dict[str, object]:
    if not raw.strip():
        raise GuardInputError("hook input is empty")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuardInputError("hook input is not valid JSON") from exc
    if not isinstance(value, dict) or not value:
        raise GuardInputError("hook input must be a non-empty JSON object")
    return value


def _payload() -> dict[str, object]:
    return _decode_payload(sys.stdin.read())


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    return []


def _normalized_path(raw: str) -> tuple[bool, str]:
    candidate = raw.strip()
    while candidate.startswith("./"):
        candidate = candidate[2:]
    path = Path(candidate).expanduser()
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    try:
        return True, resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return False, resolved.as_posix()


def _contains_parts(path: str, protected: str) -> bool:
    path_parts = PurePosixPath(path).parts
    protected_parts = PurePosixPath(protected).parts
    width = len(protected_parts)
    return any(
        path_parts[index : index + width] == protected_parts
        for index in range(len(path_parts) - width + 1)
    )


def _sensitive(raw: str) -> bool:
    if not raw.strip():
        return False
    # A metadata symlink must not turn a protected spelling into a content-read exception.
    if ".git" in Path(raw.strip()).parts:
        return True
    inside, normalized = _normalized_path(raw)
    if inside and normalized in PROTECTED_EXCEPTIONS:
        return False
    name = PurePosixPath(normalized).name
    if name == ".env" or name.startswith(".env."):
        return name != ".env.example"
    if PurePosixPath(normalized).suffix in SENSITIVE_SUFFIXES:
        return True
    return any(_contains_parts(normalized, protected) for protected in SENSITIVE_PARTS)


def _shell_words(command: str, *, preserve_quotes: bool = False) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=not preserve_quotes, punctuation_chars=";&|<>()`")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError as exc:
        raise GuardInputError("shell command cannot be parsed safely") from exc


def _shell_tokenizations(command: str) -> list[list[str]]:
    pending = [command]
    seen: set[str] = set()
    tokenizations: list[list[str]] = []
    for _ in range(3):
        nested: list[str] = []
        for candidate in pending:
            if candidate in seen:
                continue
            seen.add(candidate)
            words = _shell_words(candidate)
            tokenizations.append(words)
            if "$(" in candidate or "`" in candidate:
                # Retain quote boundaries: double quotes allow substitution, single quotes do not.
                quoted_words = _shell_words(candidate, preserve_quotes=True)
                tokenizations.append(quoted_words)
                nested.extend(
                    word[1:-1]
                    for word in quoted_words
                    if word.startswith('"') and word.endswith('"') and ("$(" in word or "`" in word)
                )
            # Only shell -c arguments are executable strings; rg/echo arguments are data.
            for index, word in enumerate(words):
                if Path(word).name not in {"sh", "bash", "zsh", "dash", "ksh"}:
                    continue
                for position in range(index + 1, len(words) - 1):
                    option = words[position]
                    if option in SHELL_SEPARATORS:
                        break
                    if option.startswith("-") and not option.startswith("--") and "c" in option[1:]:
                        nested.append(words[position + 1])
                        break
        pending = nested
    return tokenizations


def _current_branch(repository: Path) -> str | None:
    try:
        result = subprocess.run(
            ("git", "branch", "--show-current"),  # noqa: S607 - fixed read-only Git query.
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    branch = result.stdout.strip()
    return branch if result.returncode == 0 and branch else None


def _unsafe_worktree_remove(arguments: list[str], repository: Path) -> bool:
    if any(argument in {"-f", "--force"} for argument in arguments):
        return True
    targets = [argument for argument in arguments if not argument.startswith("-")]
    if len(targets) != 1:
        return True
    raw_target = Path(targets[0]).expanduser()
    target = (
        raw_target.resolve() if raw_target.is_absolute() else (repository / raw_target).resolve()
    )
    if target == ROOT.resolve() or not target.is_dir():
        return True
    try:
        listed = subprocess.run(
            ("git", "worktree", "list", "--porcelain"),  # noqa: S607 - read-only Git query.
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        registered = {
            Path(line.removeprefix("worktree ")).resolve()
            for line in listed.stdout.splitlines()
            if line.startswith("worktree ")
        }
        if listed.returncode != 0 or target not in registered:
            return True
        result = subprocess.run(  # noqa: S603 - argv-based read-only Git query.
            (  # noqa: S607 - fixed read-only Git query.
                "git",
                "-C",
                str(target),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return result.returncode != 0 or bool(result.stdout.strip())


def _tag_mutates(arguments: list[str]) -> bool:
    if not arguments:
        return False
    return not any(
        argument in {"-l", "--list"}
        or argument.startswith(("--contains", "--merged", "--no-merged", "--points-at"))
        for argument in arguments
    )


def _command_repository(payload: dict[str, object], command: str) -> Path | None:
    """Resolve the tool cwd separately from the loaded policy's location."""
    repositories: list[Path] = []
    invalid = False

    def visit(value: object) -> None:
        nonlocal invalid
        if isinstance(value, dict):
            if any(value.get(key) == command for key in COMMAND_KEYS):
                directories = [value[key] for key in ("workdir", "cwd") if key in value]
                if not directories:
                    invalid = True
                for directory in directories:
                    if not isinstance(directory, str) or not Path(directory).is_absolute():
                        invalid = True
                    else:
                        repositories.append(Path(directory).resolve())
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(payload)
    if invalid or not repositories or len(set(repositories)) != 1:
        return None
    return repositories[0]


def _push_git_read(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # noqa: S603 - callers supply only fixed read-only Git operations.
            ("git", *arguments),  # noqa: S607 - repository Git metadata, no remote operation.
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _registered_push_repository(repository: Path) -> bool:
    if repository == ROOT.resolve():
        return True
    roots = [
        _push_git_read(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
        for path in (ROOT, repository)
    ]
    if any(
        result is None or result.returncode != 0 or not result.stdout.strip() for result in roots
    ):
        return False
    common_dirs = {Path(result.stdout.strip()).resolve() for result in roots if result is not None}
    if len(common_dirs) != 1:
        return False
    listed = _push_git_read(ROOT, "worktree", "list", "--porcelain", "-z")
    if listed is None or listed.returncode != 0:
        return False
    return repository in {
        Path(field.removeprefix("worktree ")).resolve()
        for field in listed.stdout.split("\0")
        if field.startswith("worktree ")
    }


def _git_worktree_attributes_allowed(command: str) -> bool:
    """Permit only no-follow file attributes needed to diagnose broken Git registration."""
    words = _shell_words(command)
    if (
        len(words) != 4
        or words[0] != "/bin/ls"
        or words[1] not in {"-ld", "-ldO"}
        or words[2] != "--"
    ):
        return False
    metadata = Path(words[3])
    if (
        not metadata.is_absolute()
        or str(metadata) != words[3]
        or metadata.name != "commondir"
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", metadata.parent.name) is None
    ):
        return False
    # Enumeration may be broken; rev-parse still identifies the loaded policy's own repository.
    result = _push_git_read(ROOT, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if result is None or result.returncode != 0 or len(result.stdout.strip().splitlines()) != 1:
        return False
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        return False
    try:
        return (
            metadata.parent.parent == common.resolve() / "worktrees"
            and metadata.parent.resolve() == metadata.parent
        )
    except (OSError, RuntimeError):
        return False


def _candidate_push_allowed(command: str, repository: Path | None) -> bool:
    words = _shell_words(command)
    if len(words) == 6 and words[:2] == ["git", "-C"] and words[3:5] == ["push", "origin"]:
        if repository is None or words[2] != str(repository) or not Path(words[2]).is_absolute():
            return False
        words = ["git", *words[3:]]
    if repository is None or len(words) != 4 or words[:3] != ["git", "push", "origin"]:
        return False
    sha, separator, destination = words[3].partition(":")
    if separator != ":" or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        return False
    if not _registered_push_repository(repository):
        return False
    try:
        delivery = _decode_payload((repository / "governance/delivery.json").read_text())
        standards = _decode_payload((repository / "governance/standards.json").read_text())
    except (OSError, UnicodeError, GuardInputError):
        return False
    project = standards.get("project")
    if not isinstance(project, dict):
        return False
    repository_url = project.get("repository_url")
    if (
        not isinstance(repository_url, str)
        or re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", repository_url)
        is None
    ):
        return False
    candidate = delivery.get("integration_candidate")
    preview = delivery.get("private_preview")
    owner = delivery.get("owner_authorization")
    stage = delivery.get("current_stage")
    if (
        not isinstance(candidate, dict)
        or not isinstance(preview, dict)
        or not isinstance(owner, dict)
        or not isinstance(stage, dict)
    ):
        return False
    authority = preview.get("real_evidence_authorization")
    verification = candidate.get("verification_evidence")
    owner_scope = owner.get("scope")
    if not isinstance(authority, dict) or not isinstance(verification, dict):
        return False
    if not isinstance(owner_scope, dict):
        return False
    branch = candidate.get("branch")
    source = candidate.get("source_sha")
    source_branch = candidate.get("source_branch")
    anchor = authority.get("lineage_anchor_sha")
    allowed_refs = authority.get("allowed_git_refs")
    actions = authority.get("actions")
    if (
        not isinstance(branch, str)
        or not branch.startswith("integration/")
        or destination != f"refs/heads/{branch}"
        or not isinstance(source_branch, str)
        or re.fullmatch(r"codex/[A-Za-z0-9._/-]+", source_branch) is None
        or not isinstance(source, str)
        or re.fullmatch(r"[0-9a-f]{40}", source) is None
        or not isinstance(anchor, str)
        or re.fullmatch(r"[0-9a-f]{40}", anchor) is None
        or not isinstance(allowed_refs, list)
        or not all(isinstance(item, str) for item in allowed_refs)
        or not isinstance(actions, list)
        or not all(isinstance(item, str) for item in actions)
    ):
        return False
    if (
        stage.get("id") != "R2"
        or owner.get("status") != "active"
        or not isinstance(owner.get("authorization_id"), str)
        or authority.get("preserves_authorization_anchor") != owner.get("authorization_id")
        or owner_scope.get("lineage_anchor_sha") != anchor
        or authority.get("status") != "active"
        or authority.get("stage") != "R2"
        or authority.get("environment") != "private_authenticated_real_research_preview"
        or "integration/**" not in allowed_refs
        or "nonforce_integration_push" not in actions
        or preview.get("production_evidence") is not False
        or candidate.get("status") != "verified_in_synthetic_environment"
        or candidate.get("final_review_status") != "approved"
        or candidate.get("final_review_target_sha") != source
        or candidate.get("last_full_verification_sha") != sha
        or verification.get("attempt_sha") != sha
        or verification.get("result") != "passed"
        or type(verification.get("full_gate_exit")) is not int
        or verification.get("full_gate_exit") != 0
    ):
        return False
    # Explicit refspecs must not inherit implicit mirror or tag publication.
    for setting in ("remote.origin.mirror", "push.followTags"):
        result = _push_git_read(repository, "config", "--type=bool", "--get", setting)
        if result is None or not (
            (result.returncode == 1 and not result.stdout)
            or (result.returncode == 0 and result.stdout.strip() == "false")
        ):
            return False
    expected_queries = (
        (("rev-parse", "--show-toplevel"), str(repository)),
        (("branch", "--show-current"), branch),
        (("status", "--porcelain", "--untracked-files=all"), ""),
        # Both raw origin and its single effective push destination must match.
        # Do not log URL output: a rejected local configuration could contain credentials.
        (("config", "--get-all", "remote.origin.url"), repository_url),
        (("remote", "get-url", "--push", "--all", "origin"), repository_url),
        (("rev-parse", "--verify", f"refs/heads/{source_branch}"), source),
        (("merge-base", "--is-ancestor", anchor, source), ""),
        (("merge-base", "--is-ancestor", source, sha), ""),
        (("merge-base", "--is-ancestor", sha, "HEAD"), ""),
    )
    for arguments, expected in expected_queries:
        result = _push_git_read(repository, *arguments)
        if result is None or result.returncode != 0 or result.stdout.strip() != expected:
            return False
    # Reviewed source, tested payload and current checkout may differ only in status docs.
    for base, target in ((source, sha), (sha, "HEAD")):
        result = _push_git_read(
            repository,
            "diff",
            "--quiet",
            base,
            target,
            "--",
            ".",
            ":(exclude)governance/delivery.json",
            ":(exclude)docs/engineering/tasks/**",
        )
        if result is None or result.returncode != 0:
            return False
    return True


def _git_is_destructive(command: str, command_repository: Path | None) -> bool:
    for words in _shell_tokenizations(command):
        for index, word in enumerate(words):
            if Path(word).name != "git":
                continue
            segment: list[str] = []
            for token in words[index + 1 :]:
                if token in SHELL_SEPARATORS:
                    break
                segment.append(token)
            if "--no-verify" in segment:
                return True
            cursor = 0
            repository = command_repository
            while cursor < len(segment):
                argument = segment[cursor]
                if argument == "-C" and cursor + 1 < len(segment):
                    raw_repository = Path(segment[cursor + 1]).expanduser()
                    if raw_repository.is_absolute():
                        repository = raw_repository.resolve()
                    elif repository is None:
                        return True
                    else:
                        repository = (repository / raw_repository).resolve()
                    cursor += 2
                    continue
                if argument in {"-c", "--git-dir", "--work-tree"}:
                    cursor += 2
                    continue
                if argument.startswith("-C") and len(argument) > 2:
                    raw_repository = Path(argument[2:]).expanduser()
                    if raw_repository.is_absolute():
                        repository = raw_repository.resolve()
                    elif repository is None:
                        return True
                    else:
                        repository = (repository / raw_repository).resolve()
                    cursor += 1
                    continue
                if argument.startswith(("--git-dir=", "--work-tree=")):
                    cursor += 1
                    continue
                if argument.startswith("-"):
                    cursor += 1
                    continue
                verb = argument
                arguments = segment[cursor + 1 :]
                if verb == "reset" and "--hard" in arguments:
                    return True
                if verb == "clean" and any(
                    argument == "--force"
                    or (argument.startswith("-") and "f" in argument.lstrip("-"))
                    for argument in arguments
                ):
                    return True
                if verb == "restore":
                    return True
                if verb == "checkout" and "--" in arguments:
                    return True
                if verb == "branch" and "-D" in arguments:
                    return True
                if verb == "push":
                    return not _candidate_push_allowed(command, repository)
                if verb == "tag" and _tag_mutates(arguments):
                    return True
                if verb in MAIN_MUTATIONS:
                    if (
                        repository is None
                        or index != 0
                        or any(part in SHELL_SEPARATORS or part == "cd" for part in words)
                        or _current_branch(repository) in {None, "main"}
                    ):
                        return True
                if (
                    verb == "worktree"
                    and arguments
                    and arguments[0] == "remove"
                    and (repository is None or _unsafe_worktree_remove(arguments[1:], repository))
                ):
                    return True
                break
    return False


def _command_paths(command: str) -> list[str]:
    candidates: list[str] = []
    for words in _shell_tokenizations(command):
        for word in words:
            candidate = word.strip("()[]{}:,")
            if "=" in candidate:
                candidate = candidate.split("=", maxsplit=1)[1]
            candidate = candidate.lstrip("@<>")
            if candidate and candidate not in SHELL_SEPARATORS:
                candidates.append(candidate)
    return candidates


def _guard_inputs(payload: dict[str, object]) -> tuple[list[str], list[str]]:
    commands: list[str] = []
    paths: list[str] = []

    def visit(value: object, key: str = "") -> None:
        lowered = key.lower()
        if lowered in COMMAND_KEYS:
            for text in _strings(value):
                if "*** Begin Patch" in text:
                    prefix = text.partition("*** Begin Patch")[0].strip()
                    if prefix:
                        commands.append(prefix)
                    paths.extend(match.strip() for match in PATCH_PATH.findall(text))
                else:
                    commands.append(text)
            return
        if lowered in PATH_KEYS or lowered.endswith("_path") or lowered.endswith("_paths"):
            paths.extend(_strings(value))
            return
        if lowered in PATCH_KEYS and isinstance(value, str) and "*** Begin Patch" in value:
            paths.extend(match.strip() for match in PATCH_PATH.findall(value))
            return
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                visit(nested_value, nested_key)
        elif isinstance(value, list):
            for item in value:
                visit(item, key)

    visit(payload)
    if not commands and not paths:
        raise GuardInputError("hook input has no inspectable command or path")
    return commands, paths


def _blocked_reason(payload: dict[str, object]) -> str | None:
    commands, paths = _guard_inputs(payload)
    for command in commands:
        if _git_is_destructive(command, _command_repository(payload, command)):
            return "destructive Git command"
        if _git_worktree_attributes_allowed(command):
            continue
        for candidate in _command_paths(command):
            if _sensitive(candidate):
                return f"sensitive command path: {candidate}"
    for path in paths:
        if _sensitive(path):
            return f"sensitive path: {path}"
    return None


def _protect() -> int:
    try:
        payload = _payload()
        reason = _blocked_reason(payload)
        commands, _ = _guard_inputs(payload)
        for command in commands:
            words = _shell_words(command)
            directory = _command_repository(payload, command)
            repository = directory
            if len(words) >= 3 and words[:2] == ["git", "-C"] and Path(words[2]).is_absolute():
                repository = Path(words[2]).resolve()
                words = ["git", *words[3:]]
            if words == ["git", "rev-parse", "--show-toplevel"] or words[:2] == ["git", "push"]:
                context = json.dumps(
                    {
                        "guard_script": str(Path(__file__).resolve()),
                        "command_workdir": str(directory) if directory is not None else None,
                        "git_repository": str(repository) if repository is not None else None,
                        "decision": "deny" if reason is not None else "allow",
                    }
                )
                if reason is not None:
                    print(context, file=sys.stderr)
                else:
                    print(
                        json.dumps(
                            {
                                "hookSpecificOutput": {
                                    "hookEventName": "PreToolUse",
                                    "additionalContext": context,
                                }
                            }
                        )
                    )
                break
    except GuardInputError as exc:
        print(f"Blocked invalid hook input: {exc}", file=sys.stderr)
        return 2
    if reason is not None:
        print(f"Blocked {reason}.", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "context":
        print(
            "Product-first delivery: define capability, runnable path, evidence and "
            "non-goals; preserve rights, evidence, PIT, idempotency and paper-only boundaries."
        )
        return 0
    if command == "protect":
        return _protect()
    print("usage: agent_guard.py {context|protect}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
