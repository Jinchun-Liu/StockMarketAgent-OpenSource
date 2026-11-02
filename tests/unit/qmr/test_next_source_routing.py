"""Frozen, network-free regressions for the next bounded source repair."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tests.integration.next_source_fixture import official_run
from tests.unit.dmi import test_governed_web_source as source_helpers
from tests.unit.qmr import test_research_fallback as helpers

from stock_assistant.contracts.discovery import (
    ResearchAcquisitionState,
    RobotsOriginStop,
    SourceDiscoveryCandidate,
    SourceDiscoveryQuery,
    SourceDiscoveryResult,
    SourceDiscoveryStatus,
)
from stock_assistant.contracts.research import ResearchEvidencePacket, ResearchStage
from stock_assistant.platform.discovery.cninfo import CninfoSourceDiscovery
from stock_assistant.ports.ingestion import GovernedWebSourceError

NOW = datetime(2026, 9, 15, 12, tzinfo=UTC)


class _FrozenTime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:
        return NOW


@pytest.mark.parametrize("change", ["schema", "rule", "unregistered_origin"])
async def test_acquisition_resume_does_not_clear_incompatible_state(
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    monkeypatch.setattr(helpers.fallback_module, "datetime", _FrozenTime)
    session = helpers._session(NOW).model_copy(
        update={
            "source_mode": helpers.ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )
    flash, pro = helpers._AcquisitionProvider(), helpers._AuditProvider()
    coordinator = helpers.ResearchWebFallbackCoordinator(
        provider=flash,
        audit_provider=pro,
        discovery=helpers._AcquisitionDiscovery(NOW),
        connector=helpers._FailingConnector(),
        rights=helpers._rights(),
        receipts=helpers._Receipts(),
    )
    saved = []

    async def save(state, packet):
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))
        raise asyncio.CancelledError

    async def progress(stage, message):
        pass

    with pytest.raises(asyncio.CancelledError):
        await coordinator.collect_real(
            session=session,
            state=None,
            packet=helpers.empty_real_evidence_packet(session),
            cutoff_mode=helpers.ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=NOW + timedelta(minutes=5),
            save=save,
            progress=progress,
        )
    state, packet = saved[-1]
    state.attempts = {"identity.company": 3}
    if change == "schema":
        state.schema_version = "1.1"
    elif change == "rule":
        state.rule_version = "research-real-acquisition.v30"
    else:
        state.robots_origin_stops = [
            RobotsOriginStop(origin="https://unknown.example", http_status=403, observed_at=NOW)
        ]
    original = state.model_dump_json()
    with pytest.raises(GovernedWebSourceError, match="RESEARCH_CHECKPOINT_INCOMPATIBLE"):
        await coordinator.collect_real(
            session=session,
            state=state,
            packet=packet,
            cutoff_mode=helpers.ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=state.deadline_at,
            save=save,
            progress=progress,
        )
    assert state.model_dump_json() == original
    assert len(saved) == 1 and flash.calls == pro.calls == 0


@pytest.mark.parametrize("wrong_company", [False, True])
async def test_other_official_source_is_audited_after_first_origin_denial(
    monkeypatch: pytest.MonkeyPatch,
    wrong_company: bool,
) -> None:
    monkeypatch.setattr(helpers.fallback_module, "datetime", _FrozenTime)
    session = helpers._session(NOW).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": helpers.ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )
    snapshots = []

    async def save(state, packet):
        snapshots.append((state.model_copy(deep=True), packet.model_copy(deep=True)))

    result, metrics = await official_run(
        session,
        None,
        helpers.empty_real_evidence_packet(session),
        save,
        wrong_company=wrong_company,
    )
    assert metrics["requests"].count(("www.cninfo.com.cn", "/robots.txt")) == 1
    assert metrics["requests"].count(("static.cninfo.com.cn", "/robots.txt")) == 1
    assert len(snapshots[-1][0].robots_origin_stops) == 2
    assert all(n <= 4 for n in snapshots[-1][0].attempts.values())
    if wrong_company:
        assert not result.facts
    else:
        assert {"identity.company", "business.core_products"} <= {f.key for f in result.facts}
        assert all(f.data_audit_status == "passed" for f in result.facts)
        assert metrics["audit_keys"].count("identity.company") == 1
        assert all(c.source_provider_id == "zmax_official" for c in result.citations)
        assert all(len(row) <= 5 for row in snapshots[-1][0].round_sources.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("same_query", [False, True])
async def test_robots_denial_stops_only_its_origin_and_preserves_other_rounds(
    monkeypatch: pytest.MonkeyPatch,
    same_query: bool,
) -> None:
    monkeypatch.setattr(helpers.fallback_module, "datetime", _FrozenTime)
    session = helpers._session(NOW).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": helpers.ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )

    class Discovery(helpers._Discovery):
        async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
            self.calls += 1
            self.queries.append(query)
            candidates = [
                SourceDiscoveryCandidate.model_validate(
                    {
                        **helpers._candidate(NOW, path=f"candidate/{i}").model_dump(mode="json"),
                        "url": f"https://static.cninfo.com.cn/candidate/{i}",
                        "domain": "static.cninfo.com.cn",
                    }
                )
                for i in range(5)
            ]
            return SourceDiscoveryResult(
                schema_version="1.1",
                provider="frozen-search",
                status=SourceDiscoveryStatus.SUCCEEDED,
                request_sha256="a" * 64,
                candidates=candidates,
                searched_at=NOW,
            )

    requested: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append((request.url.host, request.url.path))
        assert request.url.path == "/robots.txt"
        return httpx.Response(403)

    snapshots: list[ResearchAcquisitionState] = []

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        snapshots.append(state.model_copy(deep=True))

    async def progress(stage: ResearchStage, message: str) -> None:
        pass

    discovery = Discovery(now=NOW, candidates=[])

    class Flash(helpers._AcquisitionProvider):
        async def generate(self, request):
            response = await super().generate(request)
            if same_query and request.task_type == "research_web_gap_plan":
                response.payload["queries"][0]["query"] = "固定官方查询"
            return response

    flash, pro = Flash(), helpers._AuditProvider()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = helpers.ResearchWebFallbackCoordinator(
            provider=flash,
            audit_provider=pro,
            discovery=discovery,
            disclosure_discovery=CninfoSourceDiscovery(
                instrument_id=session.instrument_id,
                instrument_name=session.instrument_name,
                client=client,
                allowed_domains={"www.cninfo.com.cn", "static.cninfo.com.cn"},
                resolver=helpers._public_resolver,
                clock=lambda: NOW,
            ),
            connector=helpers.GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn", "static.cninfo.com.cn"},
                resolver=helpers._public_resolver,
                clock=lambda: NOW,
            ),
            rights=helpers._rights(),
            receipts=helpers._Receipts(),
        )
        result = await coordinator.collect_real(
            session=session,
            packet=helpers.empty_real_evidence_packet(session),
            cutoff_mode=helpers.ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=NOW + timedelta(minutes=5),
            state=None,
            save=save,
            progress=progress,
        )
    observed = {
        "attempts": snapshots[-1].attempts,
        "robots_requests": requested,
        "search_domains": [q.include_domains for q in discovery.queries],
        "flash_calls": flash.calls,
        "pro_calls": pro.calls,
        "facts": len(result.facts),
        "documents": len(snapshots[-1].documents),
    }
    print(observed)
    assert requested == [
        ("www.cninfo.com.cn", "/robots.txt"),
        ("static.cninfo.com.cn", "/robots.txt"),
    ]
    assert snapshots[-1].attempts == {"identity.company": 4}
    assert discovery.calls == (2 if same_query else 3)
    assert "www.zmax-opt.com" in discovery.queries[0].include_domains
    assert "www.cninfo.com.cn" not in discovery.queries[0].include_domains
    assert "static.cninfo.com.cn" not in discovery.queries[1].include_domains
    assert all("www.reuters.com" not in query.include_domains for query in discovery.queries)
    assert snapshots[-1].complete and not result.facts and pro.calls == 0


@pytest.mark.parametrize("document_mode", ["absent", "valid", "revoked"])
async def test_all_origins_stopped_ends_before_planning_but_reuses_legal_originals(
    monkeypatch: pytest.MonkeyPatch,
    document_mode: str,
) -> None:
    monkeypatch.setattr(helpers.fallback_module, "datetime", _FrozenTime)
    session = helpers._session(NOW).model_copy(
        update={
            "source_mode": helpers.ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )
    document, _ = helpers._document_fixture(
        "603986 兆易创新 公司身份。\n\n主营业务: 公司研发并销售存储芯片。",
        NOW,
    )
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=helpers.fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=session.as_of,
        deadline_at=NOW + timedelta(minutes=5),
        input_sha256=hashlib.sha256(
            helpers.fallback_module._canonical(
                {
                    "instrument_id": session.instrument_id,
                    "instrument_name": session.instrument_name,
                    "as_of": session.as_of.isoformat(),
                    "analysis_config": session.analysis_config.model_dump(mode="json"),
                    "cutoff_mode": helpers.ResearchCutoffMode.CURRENT_RUN.value,
                    "source_registrations": helpers.enabled_web_rights_snapshots(),
                }
            )
        ).hexdigest(),
        documents=[] if document_mode == "absent" else [document],
        robots_origin_stops=[
            RobotsOriginStop(origin=f"https://{source.domain}", http_status=403, observed_at=NOW)
            for source in helpers.fallback_module._load_allowlist().sources
            if source.enabled
        ],
    )

    class Discovery(helpers._AcquisitionDiscovery):
        async def discover(self, query):
            raise AssertionError("no remaining legal network action")

    class Flash(helpers._AcquisitionProvider):
        async def generate(self, request):
            assert request.task_type != "research_web_gap_plan"
            return await super().generate(request)

    flash, pro = Flash(), helpers._AuditProvider()
    saved = []

    async def save(value, packet):
        saved.append(value.model_copy(deep=True))

    async def progress(stage, message):
        pass

    coordinator = helpers.ResearchWebFallbackCoordinator(
        provider=flash,
        audit_provider=pro,
        discovery=Discovery(NOW),
        connector=helpers._FailingConnector(),
        rights=helpers._DeniedRights() if document_mode == "revoked" else helpers._rights(),
        receipts=helpers._Receipts(),
    )
    result = await coordinator.collect_real(
        session=session,
        state=state,
        packet=helpers.empty_real_evidence_packet(session),
        cutoff_mode=helpers.ResearchCutoffMode.CURRENT_RUN,
        task_deadline_at=state.deadline_at,
        save=save,
        progress=progress,
    )
    assert saved[-1].complete
    if document_mode == "valid":
        assert {"identity.company", "business.core_products"} <= {f.key for f in result.facts}
        assert saved[-1].documents == [document]
        assert result.web_retrieval_receipt_ids == [document.retrieval_receipt_id]
        assert all(c.first_seen_at == document.first_seen_at for c in result.citations)
    else:
        assert state.attempts == {} and flash.calls == pro.calls == 0 and not result.facts


@pytest.mark.parametrize(
    ("phase", "status", "code", "stopped"),
    [("robots", status, "WEB_ROBOTS_UNAVAILABLE", True) for status in (401, 403, 429)]
    + [
        ("robots", 500, "WEB_ROBOTS_UNAVAILABLE", False),
        ("disallow", 200, "WEB_ROBOTS_BLOCKED", False),
        ("document", 403, "WEB_FETCH_REJECTED", False),
        ("document", 404, "WEB_FETCH_REJECTED", False),
        ("timeout", 0, "WEB_FETCH_TEMPORARILY_UNAVAILABLE", False),
    ],
)
async def test_only_actual_robots_policy_responses_stop_an_origin(
    phase: str,
    status: int,
    code: str,
    stopped: bool,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if phase == "timeout":
            raise httpx.ReadTimeout("offline timeout", request=request)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                status if phase in {"robots", "disallow"} else 404,
                text="User-agent: *\nDisallow: /" if phase == "disallow" else "",
            )
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = helpers.GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=helpers._public_resolver,
            clock=lambda: source_helpers.AS_OF,
        )
        with pytest.raises(GovernedWebSourceError) as caught:
            await connector.fetch(source_helpers._request())
    assert helpers.GovernedWebSourceError is GovernedWebSourceError
    assert caught.value.code == code
    assert (caught.value.robots_origin_stop is not None) == stopped
    if stopped:
        assert caught.value.robots_origin_stop == RobotsOriginStop.model_validate(
            {
                "origin": "https://www.cninfo.com.cn",
                "http_status": status,
                "observed_at": source_helpers.AS_OF,
            }
        )
        assert requests == ["/robots.txt"]


def test_actual_query_scope_ignores_transport_identity_but_binds_domains_and_dates() -> None:
    session = helpers._session(NOW)
    query = SourceDiscoveryQuery(
        schema_version="1.0",
        context=helpers.fallback_module._operation(session),
        query="  固定 查询 AbC ",
        missing_fact_keys=["identity.company"],
        include_domains=["www.cninfo.com.cn", "www.zmax-opt.com"],
        end_date=NOW,
    )
    hash_query = helpers.fallback_module._discovery_scope_hash
    changed = query.model_copy(
        update={
            "query": "固定  查询 abc",
            "max_results": 1,
            "include_domains": list(reversed(query.include_domains)),
            "context": query.context.model_copy(
                update={
                    "request_id": helpers.uuid4(),
                    "idempotency_key": "different-job",
                    "deadline_at": NOW + timedelta(hours=1),
                }
            ),
        }
    )
    assert hash_query(changed) == hash_query(query)
    assert hash_query(
        query.model_copy(update={"include_domains": ["www.zmax-opt.com"]})
    ) != hash_query(query)
    assert hash_query(
        query.model_copy(update={"start_date": NOW - timedelta(days=3)})
    ) != hash_query(query)
