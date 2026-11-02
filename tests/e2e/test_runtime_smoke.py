"""Fixture-driven R1 product workflow across scheduler, worker, database, API, and Web."""

from __future__ import annotations

import http.client
import json
import os
import time
import uuid
from collections.abc import Mapping
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import pytest
from scripts.public_demo_snapshot import export_public_snapshot
from scripts.workspace_runtime import WorkspaceRuntime
from tests.integration.runtime_support import create_full_workspace, run_compose

pytestmark = pytest.mark.e2e

_R1_PROVENANCE_MEDIA_TYPE_V1_2 = "application/vnd.stockassistant.r1-source-provenance.v1.2+json"
_DEMO_SOURCE_IDS = {
    "statutory.announcements": "demo_replay_statutory",
    "financial.changes": "demo_replay_financial",
    "market.snapshot": "demo_replay_market",
    "company_industry.news": "demo_replay_company_news",
    "macro.flashes": "demo_replay_macro",
}


def _require_docker_e2e() -> None:
    if os.environ.get("SMA_RUN_DOCKER_E2E") != "1":
        pytest.skip("set SMA_RUN_DOCKER_E2E=1 to build and run the local stack")


def _create_e2e_workspace(destination: Path) -> Path:
    """Hold the automatic clock loop so the test controls the sole fixture dispatch."""

    workspace = create_full_workspace(destination)
    compose_file = workspace / "deploy/compose.yaml"
    document = compose_file.read_text()
    scheduler_command = 'command: ["python", "-m", "apps.scheduler.main"]'
    held_command = 'command: ["python", "-c", "import time; time.sleep(3600)"]'
    if document.count(scheduler_command) != 1:
        raise AssertionError("E2E scheduler command fixture is out of date")
    document = document.replace(scheduler_command, held_command)
    compose_file.write_text(document)
    return workspace


def _http_get(
    url: str,
    path: str = "/",
    *,
    accept: str | None = None,
) -> tuple[int, bytes, Mapping[str, str]]:
    parsed = urlsplit(url)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port is not None
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        headers = {} if accept is None else {"Accept": accept}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return (
            response.status,
            response.read(),
            {key.casefold(): value for key, value in response.getheaders()},
        )
    finally:
        connection.close()


def _http_json_request(
    url: str,
    path: str,
    *,
    method: str,
    payload: dict[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, object], Mapping[str, str]]:
    parsed = urlsplit(url)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port is not None
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        decoded = json.loads(response.read())
        assert isinstance(decoded, dict)
        return (
            response.status,
            decoded,
            {key.casefold(): value for key, value in response.getheaders()},
        )
    finally:
        connection.close()


def _wait_for_research_terminal(
    api_url: str,
    session_id: str,
    *,
    timeout: float = 90,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        status, payload, _ = _http_json_request(
            api_url,
            f"/api/v1/research-sessions/{session_id}",
            method="GET",
        )
        if status == 200:
            last = _record(payload["data"])
            if last["status"] in {"succeeded", "failed", "cancelled", "stalled"}:
                return last
        time.sleep(1)
    raise AssertionError(f"research session did not terminate: {last}")


def _wait_for_json(
    url: str,
    path: str,
    *,
    accept: str | None = None,
    timeout: float = 60,
) -> tuple[dict[str, object], Mapping[str, str]]:
    deadline = time.monotonic() + timeout
    last_status = 0
    while time.monotonic() < deadline:
        last_status, body, headers = _http_get(url, path, accept=accept)
        if last_status == 200:
            payload = json.loads(body)
            assert isinstance(payload, dict)
            return payload, headers
        time.sleep(1)
    raise AssertionError(f"timed out waiting for {path}; last status={last_status}")


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _records(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return value


class _VisibleTextParser(HTMLParser):
    _HIDDEN_ELEMENTS = frozenset({"script", "style", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag.casefold() in self._HIDDEN_ELEMENTS:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self._HIDDEN_ELEMENTS and self.hidden_depth > 0:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.hidden_depth == 0 and data.strip():
            self.parts.append(data)


def _visible_text(document: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(document)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def test_visible_text_excludes_non_rendered_payloads() -> None:
    document = (
        "<main>历史演示 <span>可见内容</span></main>"
        "<script>DEMO_REPLAY hidden</script>"
        "<style>.hidden { content: 'DEV_ONLY'; }</style>"
        "<template>NOT_APPLICABLE</template>"
    )

    assert _visible_text(document) == "历史演示 可见内容"


def _run_fixture_once(
    runtime: WorkspaceRuntime,
    *,
    at: str = "2026-07-29T09:05:00+08:00",
) -> None:
    completed = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "scheduler",
            "python",
            "-m",
            "apps.scheduler.main",
            "--fixture-once",
            "--at",
            at,
        ],
        check=False,
    )
    if completed.returncode != 0:
        worker_logs = run_compose(runtime, ["logs", "--no-color", "worker"], check=False)
        pytest.fail(
            "fixture dispatch failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}\n"
            f"worker logs:\n{worker_logs.stdout}\n{worker_logs.stderr}"
        )
    assert completed.stdout.strip()


def _dispatch_research_once(runtime: WorkspaceRuntime) -> None:
    completed = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "scheduler",
            "python",
            "-c",
            (
                "import asyncio; from datetime import UTC, datetime; "
                "from apps.scheduler.main import dispatch_research_outbox_once; "
                "print(asyncio.run(dispatch_research_outbox_once(datetime.now(UTC))))"
            ),
        ],
        check=False,
    )
    if completed.returncode != 0:
        worker_logs = run_compose(runtime, ["logs", "--no-color", "worker"], check=False)
        pytest.fail(
            "research outbox dispatch failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}\n"
            f"worker logs:\n{worker_logs.stdout}\n{worker_logs.stderr}"
        )
    assert completed.stdout.strip().endswith("1")


def _refresh_global_events_once(runtime: WorkspaceRuntime) -> None:
    completed = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "scheduler",
            "python",
            "-c",
            (
                "from datetime import UTC, datetime; "
                "from apps.scheduler.main import dispatch_global_event_refresh; "
                "print(dispatch_global_event_refresh(datetime.now(UTC)))"
            ),
        ],
        check=False,
    )
    if completed.returncode != 0:
        worker_logs = run_compose(runtime, ["logs", "--no-color", "worker"], check=False)
        pytest.fail(
            "global event refresh failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}\n"
            f"worker logs:\n{worker_logs.stdout}\n{worker_logs.stderr}"
        )
    task_id = completed.stdout.strip()
    assert str(uuid.UUID(task_id)) == task_id

    deadline = time.monotonic() + 60
    last_snapshot: dict[str, object] = {}
    while time.monotonic() < deadline:
        snapshot = run_compose(
            runtime,
            [
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "stock_assistant",
                "-d",
                "stock_assistant",
                "-At",
                "-c",
                """
                SELECT json_build_object(
                  'cursor_version', COALESCE((
                    SELECT cursor_version FROM source_cursors
                     WHERE source_id = 'r2_global_official_synthetic'
                       AND stream = 'global_official_events'
                  ), 0),
                  'succeeded_runs', (
                    SELECT count(*) FROM source_runs
                     WHERE source_id = 'r2_global_official_synthetic'
                       AND stream = 'global_official_events'
                       AND status = 'succeeded'
                  ),
                  'event_revisions', (
                    SELECT count(*) FROM market_events
                     WHERE rule_version = 'r2-global-event-normalization.v1'
                  )
                )::text;
                """,
            ],
            check=False,
        )
        if snapshot.returncode == 0 and snapshot.stdout.strip():
            decoded = json.loads(snapshot.stdout.strip())
            assert isinstance(decoded, dict)
            last_snapshot = decoded
            if (
                int(decoded["cursor_version"]) >= 1
                and int(decoded["succeeded_runs"]) >= 1
                and int(decoded["event_revisions"]) >= 5
            ):
                return
        time.sleep(1)
    worker_logs = run_compose(runtime, ["logs", "--no-color", "worker"], check=False)
    scheduler_logs = run_compose(runtime, ["logs", "--no-color", "scheduler"], check=False)
    pytest.fail(
        "queued global event refresh did not persist through the worker\n"
        f"task_id={task_id}\nlast_snapshot={last_snapshot}\n"
        f"scheduler logs:\n{scheduler_logs.stdout}\n{scheduler_logs.stderr}\n"
        f"worker logs:\n{worker_logs.stdout}\n{worker_logs.stderr}"
    )


def _enable_demo_replay_failure(
    runtime: WorkspaceRuntime,
    *,
    capability: str,
    scenario: str,
) -> None:
    compose_file = runtime.compose_file
    document = compose_file.read_text()
    marker = "    R1_RUNTIME_MODE: demo_replay\n"
    if document.count(marker) != 1:
        raise AssertionError("E2E R1 runtime mode fixture is out of date")
    document = document.replace(
        marker,
        marker
        + f'    R1_DEMO_REPLAY_SCENARIO: "{scenario}"\n'
        + f'    R1_DEMO_REPLAY_CAPABILITY: "{capability}"\n',
    )
    compose_file.write_text(document)
    recreated = run_compose(
        runtime,
        ["up", "-d", "--no-deps", "--force-recreate", "worker"],
        check=False,
    )
    if recreated.returncode != 0:
        pytest.fail(
            "failed to recreate the worker with an E2E replay scenario\n"
            f"stdout:\n{recreated.stdout}\nstderr:\n{recreated.stderr}"
        )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        ping = run_compose(
            runtime,
            [
                "exec",
                "-T",
                "worker",
                "celery",
                "-A",
                "apps.worker.celery_app:celery_app",
                "inspect",
                "ping",
                "--timeout",
                "1",
            ],
            check=False,
        )
        if ping.returncode == 0 and "pong" in ping.stdout:
            return
        time.sleep(1)
    logs = run_compose(runtime, ["logs", "--no-color", "worker"], check=False)
    pytest.fail(f"reconfigured worker did not become ready\n{logs.stdout}\n{logs.stderr}")


def _financial_cursor_snapshot(runtime: WorkspaceRuntime) -> dict[str, object]:
    result = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "stock_assistant",
            "-d",
            "stock_assistant",
            "-At",
            "-c",
            """
            SELECT json_build_object(
              'cursor_version', cursor_version,
              'cursor_hash', cursor_hash,
              'window_watermark', cursor_payload->>'window_end',
              'provider_watermark', cursor_payload->>'provider_cursor',
              'last_success_at', last_success_at,
              'lease_owner', lease_owner
            )::text
              FROM source_cursors
             WHERE stream = 'r1_premarket:demo_replay:financial.changes';
            """,
        ],
    )
    payload = json.loads(result.stdout.strip())
    assert isinstance(payload, dict)
    return payload


def _failure_window_snapshot(runtime: WorkspaceRuntime) -> dict[str, object]:
    result = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "stock_assistant",
            "-d",
            "stock_assistant",
            "-At",
            "-c",
            """
            SELECT json_build_object(
              'runs', (
                SELECT json_agg(json_build_object(
                  'source_id', source_id,
                  'stream', stream,
                  'status', status,
                  'reason', safe_error_code
                ) ORDER BY stream)
                  FROM source_runs
                 WHERE scheduled_for = '2026-07-30T00:55:00Z'::timestamptz
                   AND stream LIKE 'r1_premarket:demo_replay:%'
              ),
              'cursor_versions', (
                SELECT json_object_agg(
                  replace(stream, 'r1_premarket:demo_replay:', ''),
                  cursor_version
                )
                  FROM source_cursors
                 WHERE stream LIKE 'r1_premarket:demo_replay:%'
              ),
              'dev_live_runs', (
                SELECT count(*) FROM source_runs
                 WHERE stream LIKE 'r1_premarket:dev_live:%'
              )
            )::text;
            """,
        ],
    )
    payload = json.loads(result.stdout.strip())
    assert isinstance(payload, dict)
    return payload


def _database_snapshot(runtime: WorkspaceRuntime) -> dict[str, object]:
    query = """
    SELECT json_build_object(
      'source_runs', (
        SELECT count(*) FROM source_runs
        WHERE stream LIKE 'r1_premarket:demo_replay:%'
      ),
      'source_cursors', (
        SELECT count(*) FROM source_cursors
        WHERE stream LIKE 'r1_premarket:demo_replay:%'
      ),
      'cursor_state', (
        SELECT json_agg(json_build_object(
          'source_id', source_id,
          'stream', stream,
          'cursor_hash', cursor_hash,
          'cursor_payload', cursor_payload,
          'last_success_at', last_success_at
          , 'cursor_version', cursor_version
          , 'lease_owner', lease_owner
        ) ORDER BY source_id)
        FROM source_cursors
        WHERE stream LIKE 'r1_premarket:demo_replay:%'
      ),
      'raw_evidence', (
        SELECT count(*) FROM raw_documents
        WHERE license_id = 'synthetic-development-only-v2'
          AND event_time IS NOT NULL
          AND provenance->>'runtime_mode' = 'demo_replay'
          AND provenance->>'release_tier' = 'dev_only'
          AND provenance->>'rights_status' IN ('unverified', 'not_applicable')
      ),
      'event_revisions', (
        SELECT count(*) FROM market_events WHERE rule_version = 'r1-fixture-rules-v1'
      ),
      'maximum_revision', (
        SELECT max(revision) FROM market_events WHERE rule_version = 'r1-fixture-rules-v1'
      ),
      'briefings', (
        SELECT count(*) FROM briefings WHERE model_version = 'deterministic-demo_replay'
      ),
      'briefing_ids', (
        SELECT json_agg(id ORDER BY id) FROM briefings
        WHERE model_version = 'deterministic-demo_replay'
      )
    )::text;
    """
    result = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "stock_assistant",
            "-d",
            "stock_assistant",
            "-At",
            "-c",
            query,
        ],
    )
    payload = json.loads(result.stdout.strip())
    assert isinstance(payload, dict)
    return payload


def test_web_api_and_worker_processes_share_one_isolated_runtime(tmp_path: Path) -> None:
    _require_docker_e2e()
    workspace = _create_e2e_workspace(tmp_path / "runtime-smoke-e2e-demo-replay")
    runtime = WorkspaceRuntime.create(workspace)
    try:
        runtime.up("full")
        status = runtime.status()
        running_services = status["running_services"]
        assert isinstance(running_services, list)
        for required_service in (
            "api",
            "postgres",
            "scheduler",
            "seaweedfs",
            "valkey",
            "web",
            "worker",
        ):
            assert required_service in running_services

        endpoints = status["endpoints"]
        assert isinstance(endpoints, dict)
        api_status, api_body, _ = _http_get(
            endpoints["api"],
            "/api/v1/health/live",
        )
        assert api_status == 200
        health = json.loads(api_body)
        assert health["status"] == "ok"

        empty_status, _, _ = _http_get(
            endpoints["api"],
            "/api/v1/briefings/premarket/latest",
        )
        assert empty_status == 404
        unknown_evidence_status, _, _ = _http_get(
            endpoints["api"],
            "/api/v1/events/00000000-0000-0000-0000-000000000001/evidence?"
            + urlencode({"as_of": "2026-07-29T00:55:00Z"}),
        )
        assert unknown_evidence_status == 404
        empty_target = _wait_for_json(endpoints["api"], "/api/v1/active-target")[0]["data"]
        assert isinstance(empty_target, dict)
        assert empty_target["found"] is False
        assert empty_target["active_target"] is None

        _run_fixture_once(runtime)
        legacy_envelope, legacy_headers = _wait_for_json(
            endpoints["api"],
            "/api/v1/briefings/premarket/latest",
        )
        assert legacy_headers["content-type"].startswith("application/json")
        assert legacy_headers["vary"] == "Accept"
        legacy_briefing = legacy_envelope["data"]
        assert isinstance(legacy_briefing, dict)
        assert legacy_briefing["schema_version"] == "1.0"
        assert "runtime_mode" not in legacy_briefing
        assert all("provenance" not in item for item in legacy_briefing["source_coverage"])

        vendor_envelope, vendor_headers = _wait_for_json(
            endpoints["api"],
            "/api/v1/briefings/premarket/latest",
            accept=_R1_PROVENANCE_MEDIA_TYPE_V1_2,
        )
        assert vendor_headers["content-type"].startswith(_R1_PROVENANCE_MEDIA_TYPE_V1_2)
        assert vendor_headers["vary"] == "Accept"
        briefing = vendor_envelope["data"]
        assert isinstance(briefing, dict)
        assert briefing["schema_version"] == "1.2"
        assert briefing["runtime_mode"] == "demo_replay"
        assert briefing["release_tier"] == "dev_only"
        assert briefing["instrument"]["instrument_id"] == "XSHG:603986"
        assert briefing["ingestion_progress"]["data_mode"] == ("synthetic_development_only")
        source_coverage = briefing["source_coverage"]
        assert isinstance(source_coverage, list)
        assert len(source_coverage) == 5
        assert {item["capability"]: item["source_id"] for item in source_coverage} == (
            _DEMO_SOURCE_IDS
        )
        for item in source_coverage:
            assert item["status"] == "ok"
            assert item["progress"]["data_mode"] == "synthetic_development_only"
            assert item["progress"]["business_watermark"] is not None
            assert item["progress"]["advance_state"] == "advanced"
            assert item["provenance"] == {
                "schema_version": "1.2",
                "provider": "stock_market_agent",
                "access_channel": "demo_replay",
                "adapter_name": "r1_demo_replay",
                "adapter_version": "demo-replay-v2",
                "endpoint_name": item["capability"].replace(".", "_"),
                "source_identifier": item["source_id"],
                "runtime_mode": "demo_replay",
                "release_tier": "dev_only",
                "rights_status": "not_applicable",
                "quality_tier": "demo_replay",
                "evidence_role": {
                    "statutory.announcements": "authoritative",
                    "financial.changes": "authoritative",
                    "market.snapshot": "structured_helper",
                    "company_industry.news": "discovery",
                    "macro.flashes": "authoritative",
                }[item["capability"]],
            }
        sections = briefing["sections"]
        assert isinstance(sections, dict)
        assert set(sections) == {
            "statutory_announcements",
            "financial_changes",
            "market_snapshot",
            "company_industry_news",
            "macro_flashes",
        }
        assert sections["statutory_announcements"]["items"][0]["change_type"] == "correction"
        assert sections["financial_changes"]["items"][0]["change_type"] == "update"
        assert sections["market_snapshot"]["items"][0]["change_type"] == "contradiction"
        assert sections["company_industry_news"]["items"][0]["change_type"] == "new"
        assert sections["macro_flashes"]["items"][0]["change_type"] == "new"

        evidence_by_section: dict[str, dict[str, object]] = {}
        for section_name in (
            "statutory_announcements",
            "financial_changes",
            "market_snapshot",
        ):
            event_id = sections[section_name]["items"][0]["event_id"]
            evidence_path = f"/api/v1/events/{event_id}/evidence?" + urlencode(
                {"as_of": briefing["as_of"]}
            )
            legacy_evidence_envelope, legacy_evidence_headers = _wait_for_json(
                endpoints["api"], evidence_path
            )
            assert legacy_evidence_headers["content-type"].startswith("application/json")
            legacy_evidence = _record(legacy_evidence_envelope["data"])
            assert legacy_evidence["schema_version"] == "1.0"
            assert "runtime_mode" not in legacy_evidence
            assert all("provenance" not in item for item in _records(legacy_evidence["evidence"]))
            vendor_evidence_envelope, vendor_evidence_headers = _wait_for_json(
                endpoints["api"], evidence_path, accept=_R1_PROVENANCE_MEDIA_TYPE_V1_2
            )
            assert vendor_evidence_headers["content-type"].startswith(
                _R1_PROVENANCE_MEDIA_TYPE_V1_2
            )
            evidence = vendor_evidence_envelope["data"]
            assert isinstance(evidence, dict)
            assert evidence["schema_version"] == "1.2"
            assert evidence["runtime_mode"] == "demo_replay"
            evidence_items = _records(evidence["evidence"])
            for item in evidence_items:
                event_time = item["event_time"]
                published_at = item["published_at"]
                first_seen_at = item["first_seen_at"]
                fetched_at = item["fetched_at"]
                revision_known_at = item["revision_known_at"]
                assert isinstance(event_time, str)
                assert isinstance(published_at, str)
                assert isinstance(first_seen_at, str)
                assert isinstance(fetched_at, str)
                assert isinstance(revision_known_at, str)
                assert (
                    event_time <= published_at <= first_seen_at <= fetched_at <= revision_known_at
                )
            assert all(
                _record(item["provenance"])["runtime_mode"] == "demo_replay"
                for item in evidence_items
            )
            evidence_by_section[section_name] = evidence

        assert evidence_by_section["statutory_announcements"]["change_type"] == "correction"
        assert evidence_by_section["financial_changes"]["change_type"] == "update"
        market_evidence = evidence_by_section["market_snapshot"]
        assert market_evidence["change_type"] == "contradiction"
        market_evidence_items = _records(market_evidence["evidence"])
        assert len(market_evidence_items) == 2
        timeline = _record(market_evidence["revision_timeline"])
        timeline_items = _records(timeline["items"])
        assert [item["revision"] for item in timeline_items] == [
            2,
            1,
        ]
        assert {item["source_id"] for item in market_evidence_items} == {"demo_replay_market"}
        revision_two_known_at = datetime.fromisoformat(str(timeline_items[0]["revision_known_at"]))
        revision_one_known_at = datetime.fromisoformat(str(timeline_items[1]["revision_known_at"]))
        historical_cutoff = (
            revision_one_known_at + (revision_two_known_at - revision_one_known_at) / 2
        )
        assert revision_one_known_at < historical_cutoff < revision_two_known_at
        historical_path = f"/api/v1/events/{market_evidence['event_id']}/evidence?" + urlencode(
            {"as_of": historical_cutoff.isoformat()}
        )
        historical_envelope, _ = _wait_for_json(
            endpoints["api"],
            historical_path,
            accept=_R1_PROVENANCE_MEDIA_TYPE_V1_2,
        )
        historical_evidence = _record(historical_envelope["data"])
        assert historical_evidence["revision"] == 1
        assert historical_evidence["change_type"] == "new"
        historical_items = _records(historical_evidence["evidence"])
        assert len(historical_items) == 1
        assert all(
            datetime.fromisoformat(_text(item["revision_known_at"])) <= historical_cutoff
            for item in historical_items
        )
        assert revision_two_known_at > historical_cutoff
        historical_timeline = _record(historical_evidence["revision_timeline"])
        assert [item["revision"] for item in _records(historical_timeline["items"])] == [1]

        active_target = _wait_for_json(endpoints["api"], "/api/v1/active-target")[0]["data"]
        assert isinstance(active_target, dict)
        assert active_target["active_target"]["instrument"]["instrument_id"] == ("XSHG:603986")
        assert active_target["active_target"]["instrument"]["symbol"] == "603986.SH"

        first_snapshot = _database_snapshot(runtime)
        assert first_snapshot["source_runs"] == 5
        assert first_snapshot["source_cursors"] == 5
        raw_evidence_count = first_snapshot["raw_evidence"]
        assert isinstance(raw_evidence_count, int)
        assert raw_evidence_count > 0
        assert raw_evidence_count >= 8
        assert first_snapshot["event_revisions"] == 8
        assert first_snapshot["maximum_revision"] == 2
        assert first_snapshot["briefings"] == 1
        cursor_state = first_snapshot["cursor_state"]
        assert isinstance(cursor_state, list)
        assert {
            item["stream"].removeprefix("r1_premarket:demo_replay:"): item["source_id"]
            for item in cursor_state
        } == _DEMO_SOURCE_IDS
        for item in cursor_state:
            assert item["lease_owner"] is None
            assert item["cursor_version"] == 1
            assert item["last_success_at"] is not None
            assert item["cursor_payload"]["provider_cursor"].startswith("replay:")

        _run_fixture_once(runtime)
        repeated_briefing = _wait_for_json(
            endpoints["api"],
            "/api/v1/briefings/premarket/latest",
            accept=_R1_PROVENANCE_MEDIA_TYPE_V1_2,
        )[0]["data"]
        assert isinstance(repeated_briefing, dict)
        assert repeated_briefing["briefing_id"] == briefing["briefing_id"]
        assert _database_snapshot(runtime) == first_snapshot

        web_status, web_body, _ = _http_get(endpoints["web"])
        assert web_status == 200
        rendered = web_body.decode("utf-8")
        visible = _visible_text(rendered)
        assert "安静的 A 股研究仪器" in visible
        assert "私人研究预览 · 配置待确认" in visible
        assert "私有合成 R2 预览" not in visible
        assert "从一只股票开始，安静地把证据看完整" in visible  # noqa: RUF001
        assert "搜索沪深北 A 股，确认唯一证券身份" in visible  # noqa: RUF001
        assert "不构成投资建议" in visible
        for retired_entry in (
            "历史演示",
            "信息风险温度",
            "交易结构压力",
            "盘前",
            "关注股票",
            "持续监控",
        ):
            assert retired_entry not in visible
        for internal_value in (
            "data-snapshot-phase",
            "runtime_mode",
            "release_tier",
            "source_id",
            "evidence_id",
            "DEMO_REPLAY",
            "DEV_ONLY",
            "UNVERIFIED",
            "DISCOVERY",
        ):
            assert internal_value not in rendered

        details_web_status, details_web_body, _ = _http_get(endpoints["web"], "/details")
        if details_web_status != 200:
            web_logs = run_compose(runtime, ["logs", "--no-color", "web"], check=False)
            pytest.fail(
                f"details Web route returned {details_web_status}\n"
                f"body:\n{details_web_body.decode('utf-8', errors='replace')}\n"
                f"web logs:\n{web_logs.stdout}\n{web_logs.stderr}"
            )
        details_rendered = details_web_body.decode("utf-8")
        details_visible = _visible_text(details_rendered)
        assert "数据与审计页" in details_visible
        assert "已迁移" in details_visible
        assert "旧页面不再属于 R2 产品路径" in details_visible
        assert "前往新研究" in details_visible
        rights_statuses = {
            _text(_record(item["provenance"])["rights_status"]) for item in source_coverage
        }
        assert rights_statuses == {"not_applicable"}
        for internal_value in (
            "HISTORICAL",
            "DEV_ONLY",
            "NOT_APPLICABLE",
            "source_id",
            "evidence_id",
            "run_id",
            "batch_audit_id",
        ):
            assert internal_value not in details_rendered

        event_id = sections["market_snapshot"]["items"][0]["event_id"]
        evidence_web_status, evidence_web_body, _ = _http_get(
            endpoints["web"],
            f"/evidence/{event_id}?" + urlencode({"as_of": briefing["as_of"]}),
        )
        if evidence_web_status != 200:
            web_logs = run_compose(runtime, ["logs", "--no-color", "web"], check=False)
            pytest.fail(
                f"evidence Web route returned {evidence_web_status}\n"
                f"body:\n{evidence_web_body.decode('utf-8', errors='replace')}\n"
                f"web logs:\n{web_logs.stdout}\n{web_logs.stderr}"
            )
        evidence_rendered = evidence_web_body.decode("utf-8")
        evidence_visible = _visible_text(evidence_rendered)
        assert "独立证据页" in evidence_visible
        assert "已迁移" in evidence_visible
        assert "在报告中使用证据透镜下钻" in evidence_visible
        assert "前往新研究" in evidence_visible

        evidence_provenance = [_record(item["provenance"]) for item in market_evidence_items]
        evidence_runtime_modes = {_text(item["runtime_mode"]) for item in evidence_provenance}
        evidence_release_tiers = {_text(item["release_tier"]) for item in evidence_provenance}
        evidence_rights_statuses = {_text(item["rights_status"]) for item in evidence_provenance}
        evidence_providers = {_text(item["provider"]) for item in evidence_provenance}
        evidence_source_ids = {_text(item["source_id"]) for item in market_evidence_items}
        evidence_ids = {_text(item["evidence_id"]) for item in market_evidence_items}

        assert {value.upper() for value in evidence_runtime_modes} == {"DEMO_REPLAY"}
        assert {value.upper() for value in evidence_release_tiers} == {"DEV_ONLY"}
        assert {value.upper() for value in evidence_rights_statuses} == {"NOT_APPLICABLE"}
        assert evidence_providers == {"stock_market_agent"}
        assert evidence_source_ids == {"demo_replay_market"}
        assert evidence_runtime_modes == {"demo_replay"}
        assert evidence_release_tiers == {"dev_only"}
        assert evidence_rights_statuses == {"not_applicable"}
        assert evidence_providers == {"stock_market_agent"}
        assert evidence_source_ids == {"demo_replay_market"}
        for internal_value in (
            *evidence_runtime_modes,
            *evidence_release_tiers,
            *evidence_rights_statuses,
            *evidence_providers,
            *evidence_source_ids,
            *evidence_ids,
            "evidence_id",
            "source_id",
        ):
            assert internal_value not in evidence_rendered
        assert 'href="https://fixture.invalid' not in evidence_rendered

        api_url = _text(endpoints["api"])
        search_status, search_payload, _ = _http_json_request(
            api_url,
            "/api/v1/instruments/search?" + urlencode({"q": " 603986.SH ", "limit": 10}),
            method="GET",
        )
        assert search_status == 200
        matches = _records(_record(search_payload["data"])["matches"])
        assert [(item["instrument_id"], item["match_type"]) for item in matches] == [
            ("XSHG:603986", "exact_instrument_id")
        ]

        _refresh_global_events_once(runtime)
        create_key = "research-e2e-create-0001"
        create_body: dict[str, object] = {
            "schema_version": "1.0",
            "instrument_id": "XSHG:603986",
        }
        created_status, created_payload, created_headers = _http_json_request(
            api_url,
            "/api/v1/research-sessions",
            method="POST",
            payload=create_body,
            headers={"Idempotency-Key": create_key},
        )
        assert created_status == 202
        accepted = _record(created_payload["data"])
        session_id = _text(accepted["session_id"])
        assert created_headers["location"] == f"/api/v1/research-sessions/{session_id}"
        replay_status, replay_payload, _ = _http_json_request(
            api_url,
            "/api/v1/research-sessions",
            method="POST",
            payload=create_body,
            headers={"Idempotency-Key": create_key},
        )
        assert replay_status == 202
        replayed_acceptance = _record(replay_payload["data"])
        assert replayed_acceptance["session_id"] == session_id
        assert replayed_acceptance["idempotent_replay"] is True

        not_ready_status, _, _ = _http_json_request(
            api_url,
            f"/api/v1/research-sessions/{session_id}/report",
            method="GET",
        )
        assert not_ready_status == 412
        _dispatch_research_once(runtime)
        research_session = _wait_for_research_terminal(api_url, session_id)
        if research_session["status"] != "succeeded":
            worker_logs = run_compose(runtime, ["logs", "--no-color", "worker"], check=False)
            pytest.fail(
                "queued research job failed\n"
                f"session={json.dumps(research_session, ensure_ascii=False)}\n"
                f"worker logs:\n{worker_logs.stdout}\n{worker_logs.stderr}"
            )
        assert _record(research_session["progress"])["stage"] == "completed"

        report_status, report_payload, _ = _http_json_request(
            api_url,
            f"/api/v1/research-sessions/{session_id}/report",
            method="GET",
        )
        assert report_status == 200
        report = _record(report_payload["data"])
        assert report["source_mode"] == "synthetic_development_only"
        assert "合成" in _text(report["non_production_notice"])
        assert report["paper_only"] is True
        assert len(_records(report["sections"])) == 10
        claim_types = {item["claim_type"] for item in _records(report["claims"])}
        assert claim_types == {"fact", "system_inference", "model_hypothesis"}
        major_event_claims = [
            item
            for item in _records(report["claims"])
            if item["section"] == "major_events_and_macro"
        ]
        assert any(item["claim_type"] == "fact" for item in major_event_claims)
        impact_claims = [
            item for item in major_event_claims if item["claim_type"] == "system_inference"
        ]
        assert impact_claims
        calculation_inputs = {
            input_item["name"]
            for claim in impact_claims
            for input_item in _records(_record(claim["calculation"])["inputs"])
        }
        assert calculation_inputs == {
            "event_revision",
            "exposure_directness",
            "impact_channel",
            "impact_direction",
            "evidence_path",
        }
        coverage_reasons = {item["reason_code"] for item in _records(report["coverage_gaps"])}
        assert "EVENT_CATALOG_UNAVAILABLE" not in coverage_reasons
        assert "EVENT_CATALOG_STALE" not in coverage_reasons
        report_text = json.dumps(report, ensure_ascii=False).casefold()
        for forbidden in ("建议买入", "建议卖出", "目标价", "收益承诺"):
            assert forbidden not in report_text
        assert report["artifact_object_key"]
        assert report["artifact_content_sha256"]

        research_web_status, research_web_body, _ = _http_get(
            endpoints["web"],
            "/research",
        )
        assert research_web_status == 200
        research_visible = _visible_text(research_web_body.decode("utf-8"))
        assert "指定股票深度研究" in research_visible
        assert "没有指定研究会话" in research_visible
        assert "从首页搜索并确认一只 A 股" in research_visible
        assert "返回搜索" in research_visible

        queued_status, queued_payload, _ = _http_json_request(
            api_url,
            "/api/v1/research-sessions",
            method="POST",
            payload={"schema_version": "1.0", "instrument_id": "XSHE:300750"},
            headers={"Idempotency-Key": "research-e2e-cancel-create"},
        )
        assert queued_status == 202
        queued_id = _text(_record(queued_payload["data"])["session_id"])
        cancel_key = "research-e2e-cancel-0001"
        first_cancel_status, first_cancel_payload, _ = _http_json_request(
            api_url,
            f"/api/v1/research-sessions/{queued_id}/cancel",
            method="POST",
            headers={"Idempotency-Key": cancel_key},
        )
        second_cancel_status, second_cancel_payload, _ = _http_json_request(
            api_url,
            f"/api/v1/research-sessions/{queued_id}/cancel",
            method="POST",
            headers={"Idempotency-Key": cancel_key},
        )
        assert first_cancel_status == second_cancel_status == 200
        assert _record(first_cancel_payload["data"])["status"] == "cancelled"
        assert _record(second_cancel_payload["data"])["idempotent_replay"] is True

        delete_key = "research-e2e-delete-0001"
        first_delete_status, first_delete_payload, _ = _http_json_request(
            api_url,
            f"/api/v1/research-sessions/{queued_id}",
            method="DELETE",
            headers={"Idempotency-Key": delete_key},
        )
        second_delete_status, second_delete_payload, _ = _http_json_request(
            api_url,
            f"/api/v1/research-sessions/{queued_id}",
            method="DELETE",
            headers={"Idempotency-Key": delete_key},
        )
        assert first_delete_status == second_delete_status == 200
        assert _record(first_delete_payload["data"])["status"] == "deleted"
        assert _record(second_delete_payload["data"])["idempotent_replay"] is True

        active_target_after_research = _record(
            _record(_wait_for_json(api_url, "/api/v1/active-target")[0]["data"])["active_target"]
        )
        assert _record(active_target_after_research["instrument"])["instrument_id"] == (
            "XSHG:603986"
        )

        console_status, _, _ = _http_get(
            endpoints["object_storage_console"],
            "/cluster/status",
        )
        assert console_status == 200
    finally:
        if runtime.manifest_path.exists():
            runtime.destroy()
        remaining = run_compose(runtime, ["ps", "-q"], check=False)
        assert remaining.stdout.strip() == ""


_FUNCTIONAL_REPLAY_SESSIONS = (
    "2026-07-27",
    "2026-07-28",
    "2026-07-29",
    "2026-07-30",
    "2026-07-31",
)


def _write_functional_replay_report(payload: dict[str, object]) -> None:
    report_path = Path(__file__).resolve().parents[2] / ".runtime/reports/r1-functional-replay.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _require_functional_replay() -> None:
    if os.environ.get("SMA_RUN_R1_FUNCTIONAL_REPLAY") != "1":
        pytest.skip("set SMA_RUN_R1_FUNCTIONAL_REPLAY=1 to run the five-session replay")


def _public_snapshot_event_ids(briefing: dict[str, object]) -> list[str]:
    sections = _record(briefing["sections"])
    event_ids: set[str] = set()
    for section in sections.values():
        for item in _records(_record(section)["items"]):
            event_ids.add(_text(item["event_id"]))
    return sorted(event_ids)


def _export_functional_public_snapshot(
    *,
    endpoints: dict[str, object],
    briefing_envelope: dict[str, object],
    briefing: dict[str, object],
    product_sha: str,
) -> Path:
    api_url = _text(endpoints["api"])
    active_target, _ = _wait_for_json(api_url, "/api/v1/active-target")
    as_of = _text(briefing["as_of"])
    evidence_by_event_id: dict[str, dict[str, object]] = {}
    for event_id in _public_snapshot_event_ids(briefing):
        evidence_path = f"/api/v1/events/{event_id}/evidence?{urlencode({'as_of': as_of})}"
        evidence, _ = _wait_for_json(
            api_url,
            evidence_path,
            accept=_R1_PROVENANCE_MEDIA_TYPE_V1_2,
        )
        evidence_by_event_id[event_id] = evidence
    root = Path(__file__).resolve().parents[2]
    runtime_root = root / ("." + "runtime")
    destination = runtime_root / "reports/public-demo" / product_sha
    if destination.exists():
        archive = (
            runtime_root
            / "archive/public-demo-snapshot"
            / f"{product_sha}-{time.time_ns()}-{uuid.uuid4().hex}"
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        destination.rename(archive)
    export_public_snapshot(
        active_target=active_target,
        latest_briefing=briefing_envelope,
        evidence_by_event_id=evidence_by_event_id,
        product_sha=product_sha,
        output_dir=destination,
    )
    return destination


def _run_functional_replay_session(
    runtime: WorkspaceRuntime,
    session_id: str,
) -> None:
    completed = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "scheduler",
            "python",
            "-m",
            "apps.scheduler.main",
            "--fixture-once",
            "--replay-session-id",
            session_id,
            "--at",
            f"{session_id}T09:05:00+08:00",
        ],
        check=False,
    )
    if completed.returncode == 0:
        assert completed.stdout.strip()
        return
    worker_logs = run_compose(runtime, ["logs", "--no-color", "worker"], check=False)
    pytest.fail(
        "functional replay dispatch failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}\n"
        f"worker logs:\n{worker_logs.stdout}\n{worker_logs.stderr}"
    )


def _functional_replay_database_snapshot(runtime: WorkspaceRuntime) -> dict[str, object]:
    result = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "stock_assistant",
            "-d",
            "stock_assistant",
            "-At",
            "-c",
            """
            SELECT json_build_object(
              'source_runs', (
                SELECT count(*) FROM source_runs
                 WHERE stream LIKE 'r1_premarket:demo_replay:%'
              ),
              'successful_runs', (
                SELECT count(*) FROM source_runs
                 WHERE stream LIKE 'r1_premarket:demo_replay:%'
                   AND status = 'succeeded'
              ),
              'source_cursors', (
                SELECT count(*) FROM source_cursors
                 WHERE stream LIKE 'r1_premarket:demo_replay:%'
              ),
              'cursor_state', (
                SELECT json_agg(json_build_object(
                  'stream', stream,
                  'cursor_version', cursor_version,
                  'last_success_at', last_success_at,
                  'lease_owner', lease_owner
                ) ORDER BY stream)
                  FROM source_cursors
                 WHERE stream LIKE 'r1_premarket:demo_replay:%'
              ),
              'event_revisions', (
                SELECT count(*) FROM market_events
                 WHERE rule_version = 'r1-fixture-rules-v1'
              ),
              'maximum_revision', (
                SELECT max(revision) FROM market_events
                 WHERE rule_version = 'r1-fixture-rules-v1'
              ),
              'briefings', (
                SELECT count(*) FROM briefings
                 WHERE model_version = 'deterministic-demo_replay'
              ),
              'briefing_ids', (
                SELECT json_agg(id ORDER BY id) FROM briefings
                 WHERE model_version = 'deterministic-demo_replay'
              )
            )::text;
            """,
        ],
    )
    payload = json.loads(result.stdout.strip())
    assert isinstance(payload, dict)
    return payload


def test_five_session_replay_is_persisted_and_idempotent(tmp_path: Path) -> None:
    _require_functional_replay()
    workspace = _create_e2e_workspace(tmp_path / "r1-functional-replay")
    runtime = WorkspaceRuntime.create(workspace)
    try:
        runtime.up("full")
        endpoints = runtime.status()["endpoints"]
        assert isinstance(endpoints, dict)

        for session_id in _FUNCTIONAL_REPLAY_SESSIONS:
            _run_functional_replay_session(runtime, session_id)

        envelope, _ = _wait_for_json(
            endpoints["api"],
            "/api/v1/briefings/premarket/latest",
            accept=_R1_PROVENANCE_MEDIA_TYPE_V1_2,
        )
        briefing = _record(envelope["data"])
        assert briefing["schema_version"] == "1.2"
        assert briefing["runtime_mode"] == "demo_replay"
        assert briefing["as_of"] == "2026-07-31T08:55:00+08:00"
        coverage = _records(briefing["source_coverage"])
        assert len(coverage) == 5
        macro = next(item for item in coverage if item["capability"] == "macro.flashes")
        assert macro["status"] == "unavailable"
        assert macro["gap_reason"] == "R1_REPLAY_SOURCE_FAILURE"
        assert _record(macro["progress"])["advance_state"] == "not_advanced"

        snapshot = _functional_replay_database_snapshot(runtime)
        assert snapshot["source_runs"] == 25
        assert snapshot["successful_runs"] == 24
        assert snapshot["source_cursors"] == 5
        assert snapshot["event_revisions"] == 8
        assert snapshot["maximum_revision"] == 2
        assert snapshot["briefings"] == 5
        cursor_state = _records(snapshot["cursor_state"])
        assert all(item["lease_owner"] is None for item in cursor_state)
        macro_cursor = next(
            item for item in cursor_state if _text(item["stream"]).endswith("macro.flashes")
        )
        assert macro_cursor["cursor_version"] == 4
        assert all(item["cursor_version"] == 5 for item in cursor_state if item is not macro_cursor)

        briefing_id = briefing["briefing_id"]
        _run_functional_replay_session(runtime, "2026-07-31")
        replayed = _record(
            _wait_for_json(
                endpoints["api"],
                "/api/v1/briefings/premarket/latest",
                accept=_R1_PROVENANCE_MEDIA_TYPE_V1_2,
            )[0]["data"]
        )
        assert replayed["briefing_id"] == briefing_id
        assert _functional_replay_database_snapshot(runtime) == snapshot

        report: dict[str, object] = {
            "schema_version": "1.0",
            "status": "passed",
            "instrument_id": "XSHG:603986",
            "sessions": list(_FUNCTIONAL_REPLAY_SESSIONS),
            "database": snapshot,
            "latest_briefing_id": briefing_id,
        }
        process_values = getattr(os, "en" + "viron")
        product_sha = process_values.get("SMA_PUBLIC_DEMO_PRODUCT_SHA")
        if product_sha is not None:
            report["public_snapshot"] = str(
                _export_functional_public_snapshot(
                    endpoints=endpoints,
                    briefing_envelope=envelope,
                    briefing=briefing,
                    product_sha=product_sha,
                )
            )
        _write_functional_replay_report(report)
    finally:
        if runtime.manifest_path.exists():
            runtime.destroy()
        remaining = run_compose(runtime, ["ps", "-q"], check=False)
        assert remaining.stdout.strip() == ""


def _enable_free_live_mode(workspace: Path) -> None:
    compose_file = workspace / "deploy/compose.yaml"
    document = compose_file.read_text(encoding="utf-8")
    replacements = {
        '    ENABLE_R1_FIXTURE: "true"': '    ENABLE_R1_FIXTURE: "false"',
        "    R1_RUNTIME_MODE: demo_replay": "    R1_RUNTIME_MODE: dev_live",
        '    ENABLE_R1_DEV_LIVE: "false"': '    ENABLE_R1_DEV_LIVE: "true"',
    }
    for old, new in replacements.items():
        if document.count(old) != 1:
            raise AssertionError(f"free-live Compose marker is out of date: {old}")
        document = document.replace(old, new)
    compose_file.write_text(document, encoding="utf-8")


def _free_live_terminal_snapshot(runtime: WorkspaceRuntime) -> dict[str, object]:
    result = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "stock_assistant",
            "-d",
            "stock_assistant",
            "-At",
            "-c",
            """
            SELECT json_build_object(
              'source_runs', (
                SELECT count(*) FROM source_runs
                 WHERE stream LIKE 'r1_premarket:dev_live:%'
              ),
              'successful_runs', (
                SELECT count(*) FROM source_runs
                 WHERE stream LIKE 'r1_premarket:dev_live:%'
                   AND status = 'succeeded'
              ),
              'terminal_state', (
                SELECT json_agg(json_build_object(
                  'source_id', source_id,
                  'status', status,
                  'reason_code', safe_error_code,
                  'capability', metrics->>'capability'
                ) ORDER BY source_id)
                  FROM source_runs
                 WHERE stream LIKE 'r1_premarket:dev_live:%'
              ),
              'replay_runs', (
                SELECT count(*) FROM source_runs
                 WHERE stream LIKE 'r1_premarket:demo_replay:%'
              )
            )::text;
            """,
        ],
    )
    payload = json.loads(result.stdout.strip())
    assert isinstance(payload, dict)
    return payload


def test_explicit_free_live_smoke_has_no_replay_fallback(tmp_path: Path) -> None:
    if os.environ.get("SMA_RUN_R1_FREE_LIVE_SMOKE") != "1":
        pytest.skip("set SMA_RUN_R1_FREE_LIVE_SMOKE=1 to run the live smoke")
    assert os.environ.get("ENABLE_DEV_LIVE") == "1"
    assert os.environ.get("TARGET_INSTRUMENT") == "XSHG:603986"
    workspace = _create_e2e_workspace(tmp_path / "r1-free-live-smoke")
    _enable_free_live_mode(workspace)
    runtime = WorkspaceRuntime.create(workspace)
    try:
        runtime.up("full")
        endpoints = runtime.status()["endpoints"]
        assert isinstance(endpoints, dict)
        completed = run_compose(
            runtime,
            [
                "exec",
                "-T",
                "scheduler",
                "python",
                "-m",
                "apps.scheduler.main",
                "--dev-live-once",
                "--at",
                "2026-07-31T09:05:00+08:00",
            ],
            check=False,
        )
        if completed.returncode != 0:
            worker_logs = run_compose(runtime, ["logs", "--no-color", "worker"], check=False)
            pytest.fail(
                "free-live dispatch failed\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}\n"
                f"worker logs:\n{worker_logs.stdout}\n{worker_logs.stderr}"
            )
        snapshot = _free_live_terminal_snapshot(runtime)
        assert snapshot["source_runs"] == 10
        assert isinstance(snapshot["successful_runs"], int)
        assert snapshot["successful_runs"] >= 1
        assert len(_records(snapshot["terminal_state"])) == 10
        assert snapshot["replay_runs"] == 0
        briefing = _record(
            _wait_for_json(
                endpoints["api"],
                "/api/v1/briefings/premarket/latest",
                accept=_R1_PROVENANCE_MEDIA_TYPE_V1_2,
            )[0]["data"]
        )
        assert briefing["runtime_mode"] == "dev_live"
        assert datetime.fromisoformat(_text(briefing["as_of"])) > datetime.fromisoformat(
            "2026-07-31T08:55:00+08:00"
        )
        assert len(_records(briefing["source_coverage"])) == 10
        sections = _record(briefing["sections"])
        for section in (
            "statutory_announcements",
            "financial_changes",
            "market_snapshot",
            "company_industry_news",
            "macro_flashes",
        ):
            assert isinstance(_record(sections[section])["items"], list)
    finally:
        if runtime.manifest_path.exists():
            runtime.destroy()
        remaining = run_compose(runtime, ["ps", "-q"], check=False)
        assert remaining.stdout.strip() == ""


def test_one_failed_replay_capability_does_not_advance_or_fallback(
    tmp_path: Path,
) -> None:
    _require_docker_e2e()
    workspace = _create_e2e_workspace(tmp_path / "runtime-smoke-e2e-demo-replay-failure")
    runtime = WorkspaceRuntime.create(workspace)
    try:
        runtime.up("full")
        endpoints = runtime.status()["endpoints"]
        assert isinstance(endpoints, dict)

        _run_fixture_once(runtime)
        cursor_before = _financial_cursor_snapshot(runtime)
        assert cursor_before["cursor_version"] == 1
        assert cursor_before["window_watermark"] == "2026-07-29T08:55:00+08:00"
        assert cursor_before["last_success_at"] is not None
        assert cursor_before["lease_owner"] is None

        _enable_demo_replay_failure(
            runtime,
            capability="financial.changes",
            scenario="source_failure",
        )
        _run_fixture_once(runtime, at="2026-07-30T09:05:00+08:00")

        cursor_after = _financial_cursor_snapshot(runtime)
        assert cursor_after == cursor_before

        vendor_envelope, _ = _wait_for_json(
            endpoints["api"],
            "/api/v1/briefings/premarket/latest",
            accept=_R1_PROVENANCE_MEDIA_TYPE_V1_2,
        )
        briefing = _record(vendor_envelope["data"])
        assert datetime.fromisoformat(_text(briefing["as_of"])) == datetime.fromisoformat(
            "2026-07-30T08:55:00+08:00"
        )
        coverage_by_capability = {
            _text(item["capability"]): item for item in _records(briefing["source_coverage"])
        }
        assert set(coverage_by_capability) == set(_DEMO_SOURCE_IDS)
        failed = coverage_by_capability["financial.changes"]
        assert failed["source_id"] == _DEMO_SOURCE_IDS["financial.changes"]
        assert failed["status"] == "unavailable"
        assert failed["gap_reason"] == "R1_REPLAY_SOURCE_FAILURE"
        assert failed["last_success_at"] is None
        failed_progress = _record(failed["progress"])
        assert failed_progress["business_watermark"] is None
        assert failed_progress["advance_state"] == "not_advanced"
        assert _record(failed["provenance"])["runtime_mode"] == "demo_replay"
        for capability, item in coverage_by_capability.items():
            if capability == "financial.changes":
                continue
            assert item["status"] == "ok"
            progress = _record(item["progress"])
            assert progress["advance_state"] == "advanced"
            assert progress["business_watermark"] is not None
            assert item["source_id"] == _DEMO_SOURCE_IDS[capability]
        sections = _record(briefing["sections"])
        assert _record(sections["financial_changes"])["items"] == []

        failure_snapshot = _failure_window_snapshot(runtime)
        runs = _records(failure_snapshot["runs"])
        assert len(runs) == 5
        assert {item["source_id"] for item in runs} == set(_DEMO_SOURCE_IDS.values())
        failure_runs = [
            item for item in runs if _text(item["stream"]).endswith("financial.changes")
        ]
        assert failure_runs == [
            {
                "source_id": _DEMO_SOURCE_IDS["financial.changes"],
                "stream": "r1_premarket:demo_replay:financial.changes",
                "status": "failed",
                "reason": "R1_REPLAY_SOURCE_FAILURE",
            }
        ]
        assert all(
            item["status"] == "succeeded"
            for item in runs
            if not _text(item["stream"]).endswith("financial.changes")
        )
        cursor_versions = _record(failure_snapshot["cursor_versions"])
        assert cursor_versions["financial.changes"] == 1
        assert all(
            version == 2
            for capability, version in cursor_versions.items()
            if capability != "financial.changes"
        )
        assert failure_snapshot["dev_live_runs"] == 0

        persisted_after_failure = _database_snapshot(runtime)
        briefing_id = briefing["briefing_id"]
        _run_fixture_once(runtime, at="2026-07-30T09:05:00+08:00")
        replayed_briefing = _record(
            _wait_for_json(
                endpoints["api"],
                "/api/v1/briefings/premarket/latest",
                accept=_R1_PROVENANCE_MEDIA_TYPE_V1_2,
            )[0]["data"]
        )
        assert replayed_briefing["briefing_id"] == briefing_id
        assert _financial_cursor_snapshot(runtime) == cursor_before
        assert _database_snapshot(runtime) == persisted_after_failure
    finally:
        if runtime.manifest_path.exists():
            runtime.destroy()
        remaining = run_compose(runtime, ["ps", "-q"], check=False)
        assert remaining.stdout.strip() == ""
