from __future__ import annotations

import pytest

from stock_assistant.common.runtime_resources import runtime_resource_path


def test_runtime_resource_path_resolves_required_research_files() -> None:
    method_library = runtime_resource_path("research_methods/ai-berkshire/adapted/methods.v1.json")
    web_allowlist = runtime_resource_path("research_sources/web-allowlist.v1.json")

    assert method_library.is_file()
    assert web_allowlist.is_file()


@pytest.mark.parametrize("value", ["../secrets", "/absolute/path", "nested/../../escape"])
def test_runtime_resource_path_rejects_unsafe_paths(value: str) -> None:
    with pytest.raises(ValueError, match="safe relative path"):
        runtime_resource_path(value)


def test_runtime_resource_path_fails_closed_without_required_file() -> None:
    with pytest.raises(FileNotFoundError, match="required runtime resource"):
        runtime_resource_path("research_sources/does-not-exist.json")
