from pathlib import Path

from scripts.project_tasks import (
    HELP,
    CommandResult,
    _schedule_policy_check,
    collect_doctor_checks,
    parse_major_version,
)


def test_help_exposes_five_minute_path() -> None:
    assert "make doctor" in HELP
    assert "make demo" in HELP
    assert "make workspace-up" in HELP
    assert "make parallel-isolation-test" in HELP


def test_parse_major_version_handles_common_tool_outputs() -> None:
    assert parse_major_version("Python 3.12.10") == 3
    assert parse_major_version("v22.14.0") == 22
    assert parse_major_version("uv 0.8.3") == 0
    assert parse_major_version("unparseable") is None


def test_doctor_fails_closed_when_required_tools_are_missing(tmp_path: Path) -> None:
    checks = collect_doctor_checks(
        root=tmp_path,
        which=lambda _name: None,
        runner=lambda _command: CommandResult(returncode=0, output="unused"),
    )

    by_name = {check.name: check for check in checks}
    assert by_name["Node.js"].status == "FAIL"
    assert by_name["npm"].status == "FAIL"
    assert by_name["Docker Compose"].status == "FAIL"
    assert by_name["uv"].status == "FAIL"
    assert "Fix:" in by_name["Node.js"].detail


def test_doctor_accepts_repository_virtualenv_uv(tmp_path: Path) -> None:
    local_uv = tmp_path / ".venv" / "bin" / "uv"
    local_uv.parent.mkdir(parents=True)
    local_uv.touch()

    def fake_runner(command: tuple[str, ...]) -> CommandResult:
        if command[0] == str(local_uv):
            return CommandResult(returncode=0, output="uv 0.8.3")
        return CommandResult(returncode=0, output="unused")

    checks = collect_doctor_checks(
        root=tmp_path,
        which=lambda _name: None,
        runner=fake_runner,
    )

    uv_check = next(check for check in checks if check.name == "uv")
    assert uv_check.status == "PASS"
    assert str(local_uv) in uv_check.detail


def test_doctor_prefers_supported_repository_python(tmp_path: Path) -> None:
    local_python = tmp_path / ".venv" / "bin" / "python"
    local_python.parent.mkdir(parents=True)
    local_python.touch()

    def fake_runner(command: tuple[str, ...]) -> CommandResult:
        if command[0] == str(local_python):
            return CommandResult(returncode=0, output="Python 3.12.10")
        return CommandResult(returncode=0, output="unused")

    checks = collect_doctor_checks(
        root=tmp_path,
        which=lambda _name: None,
        runner=fake_runner,
    )

    python_check = next(check for check in checks if check.name == "Python")
    assert python_check.status == "PASS"
    assert str(local_python) in python_check.detail


def test_schedule_doctor_accepts_the_fixture_request_factory() -> None:
    check = _schedule_policy_check()

    assert check.status == "PASS"
    assert "dispatchable" in check.detail
