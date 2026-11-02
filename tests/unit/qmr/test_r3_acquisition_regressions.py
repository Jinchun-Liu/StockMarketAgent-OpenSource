"""Offline synthetic reproduction only; no real model or network requests."""

from datetime import UTC, datetime, timedelta, tzinfo

import pytest
from tests.unit.qmr import test_research_fallback as t

from stock_assistant.contracts.discovery import ResearchAcquisitionState
from stock_assistant.contracts.intelligence import GenerationRequest, GenerationResponse
from stock_assistant.contracts.research import CoverageGap
from stock_assistant.modules.research.web_fallback import ResearchWebFallbackCoordinator


async def test_prerequisite_five_windows_respects_four_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(t.fallback_module, "datetime", FixedDatetime)
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    session = t._session(now)
    body = "603986 兆易创新\n\n" + "\n\n".join(
        "主要业务\n" + "固定无关说明" * 1800 for _ in range(5)
    )
    document, _ = t._document_fixture(body, now)

    class Empty(t._AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            if request.task_type == "research_web_evidence_normalization":
                response.payload = {"schema_version": "1.0", "facts": []}
            return response

    flash = Empty()
    pro = t._AuditProvider()
    coordinator = ResearchWebFallbackCoordinator(
        provider=flash,
        audit_provider=pro,
        discovery=t._AcquisitionDiscovery(now),
        connector=t._FailingConnector(),
        rights=t._rights(),
        receipts=t._Receipts(),
    )
    seed = t._packet(session)
    seed = seed.model_copy(
        update={
            "facts": [seed.facts[0].model_copy(update={"key": "identity.company"})],
            "coverage_gaps": [
                CoverageGap(
                    area="business.core_products",
                    reason_code="REQUIRED_FIELD_MISSING",
                    detail="固定缺口。",
                )
            ],
        }
    )
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=t.fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=now,
        deadline_at=now + timedelta(minutes=5),
        input_sha256="b" * 64,
        documents=[document],
        discovery_exhausted=True,
    )
    coordinator._acquisition = state
    coordinator._packet = seed
    result = await coordinator.complete(
        session=session,
        packet=seed,
        cutoff_mode=t.ResearchCutoffMode.CURRENT_RUN,
        task_deadline_at=state.deadline_at,
    )
    assert state.attempts == {"business.core_products": 4}
    assert len(state.candidates) == 4
    assert all(item.extraction_complete for item in state.candidates)
    assert flash.calls == 4
    assert pro.calls == 0
    assert result.coverage_gaps[0].area == "business.core_products"


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


async def test_deferred_exhaustion_does_not_reserve_original_requested_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(t.fallback_module, "datetime", FixedDatetime)
    now = FixedDatetime.now()
    session = t._session(now)
    headings = ["经营模式", "主营业务分析", "合并利润表", "核心竞争力分析", "利润分配方案"]
    headings += [heading for _ in range(3) for heading in ("经营模式", "利润分配方案")]
    document, _ = t._document_fixture(
        "603986 兆易创新\n\n" + "\n\n".join(h + "\n" + "原始说明" * 2000 for h in headings), now
    )
    flash, pro = EmptyFlash(), t._AuditProvider()
    discovery = t._AcquisitionDiscovery(now)
    coordinator = ResearchWebFallbackCoordinator(
        provider=flash,
        audit_provider=pro,
        discovery=discovery,
        connector=t._FailingConnector(),
        rights=t._rights(),
        receipts=t._Receipts(),
    )
    packet = t._packet(session)
    key = "business.customer_revenue_chain"
    packet = packet.model_copy(
        update={
            "facts": [
                packet.facts[0].model_copy(update={"key": k})
                for k in ("identity.company", "business.core_products")
            ],
            "coverage_gaps": [
                CoverageGap(area=key, reason_code="REQUIRED_FIELD_MISSING", detail="固定缺口")
            ],
        }
    )
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=t.fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=now,
        deadline_at=now + timedelta(minutes=5),
        input_sha256="b" * 64,
        documents=[document],
    )
    coordinator._acquisition, coordinator._packet = state, packet
    result = await coordinator.complete(
        session=session,
        packet=packet,
        cutoff_mode=t.ResearchCutoffMode.CURRENT_RUN,
        task_deadline_at=state.deadline_at,
    )
    assert state.attempts[key] == state.attempts["management.capital_allocation"] == 4
    assert flash.calls == 5
    assert pro.calls == 0
    assert discovery.counts == {}
    assert result.coverage_gaps


class EmptyFlash(t._AcquisitionProvider):
    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        response = await super().generate(request)
        if request.task_type == "research_web_evidence_normalization":
            response.payload = {"schema_version": "1.0", "facts": []}
        return response


async def test_unknown_send_resume_cannot_reuse_reserved_ordinal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import hashlib

    monkeypatch.setattr(t.fallback_module, "datetime", FixedDatetime)
    now = FixedDatetime.now()
    session = t._session(now).model_copy(
        update={"source_mode": t.ResearchRealSourceMode.DEVELOPMENT_ONLY}
    )
    key = "business.customer_revenue_chain"
    document, _ = t._document_fixture(
        "603986 兆易创新\n\n" + "\n\n".join("经营模式\n" + "固定无关说明" * 1800 for _ in range(5)),
        now,
    )
    packet = t._packet(session)
    packet = packet.model_copy(
        update={
            "facts": [
                packet.facts[0].model_copy(update={"key": k})
                for k in t.fallback_module.REAL_FACT_GAPS
                if k != key
            ],
            "coverage_gaps": [
                CoverageGap(area=key, reason_code="REQUIRED_FIELD_MISSING", detail="固定缺口")
            ],
        }
    )
    input_hash = hashlib.sha256(
        t.fallback_module._canonical(
            {
                "instrument_id": session.instrument_id,
                "instrument_name": session.instrument_name,
                "as_of": session.as_of.isoformat(),
                "analysis_config": session.analysis_config.model_dump(mode="json"),
                "cutoff_mode": t.ResearchCutoffMode.CURRENT_RUN.value,
                "source_registrations": t.fallback_module.enabled_web_rights_snapshots(),
            }
        )
    ).hexdigest()
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=t.fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=now,
        deadline_at=now + timedelta(minutes=5),
        input_sha256=input_hash,
        documents=[document],
        discovery_exhausted=True,
    )
    saved = []
    ordinals = []

    async def save(value, current_packet):
        saved.append((value.model_dump_json(), current_packet.model_dump_json()))

    async def progress(stage, message):
        pass

    class InterruptFlash(EmptyFlash):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            persisted = ResearchAcquisitionState.model_validate_json(saved[-1][0])
            ordinals.append(persisted.candidates[-1].attempt_numbers[key])
            if len(ordinals) == 1:
                raise asyncio.CancelledError
            return await super().generate(request)

    flash, pro = InterruptFlash(), t._AuditProvider()

    def coordinator():
        return ResearchWebFallbackCoordinator(
            provider=flash,
            audit_provider=pro,
            discovery=t._AcquisitionDiscovery(now),
            connector=t._FailingConnector(),
            rights=t._rights(),
            receipts=t._Receipts(),
        )

    with pytest.raises(asyncio.CancelledError):
        await coordinator().collect_real(
            session=session,
            packet=packet,
            cutoff_mode=t.ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=state.deadline_at,
            state=state,
            save=save,
            progress=progress,
        )
    state = ResearchAcquisitionState.model_validate_json(saved[-1][0])
    packet = t.ResearchEvidencePacket.model_validate_json(saved[-1][1])
    result = await coordinator().collect_real(
        session=session,
        packet=packet,
        cutoff_mode=t.ResearchCutoffMode.CURRENT_RUN,
        task_deadline_at=state.deadline_at,
        state=state,
        save=save,
        progress=progress,
    )
    assert ordinals == [1, 2, 3, 4]
    assert state.attempts[key] == 4
    assert len(state.candidates) == 4
    assert not state.candidates[0].extraction_complete
    assert all(c.extraction_complete for c in state.candidates[1:])
    assert state.complete and result.coverage_gaps
    assert pro.calls == 0


async def test_query_reservation_does_not_pay_for_new_windows_of_same_original() -> None:
    now = FixedDatetime.now()
    session = t._session(now)
    key = "business.core_products"
    document, registration = t._document_fixture(
        "603986 兆易创新\n\n" + "\n\n".join("主要业务\n" + "固定说明" * 2200 for _ in range(5)), now
    )
    packet = t._packet(session)
    packet = packet.model_copy(
        update={"facts": [packet.facts[0].model_copy(update={"key": "identity.company"})]}
    )
    flash = EmptyFlash()
    coordinator = ResearchWebFallbackCoordinator(
        provider=flash,
        audit_provider=t._AuditProvider(),
        discovery=t._AcquisitionDiscovery(now),
        connector=t._FailingConnector(),
        rights=t._rights(),
        receipts=t._Receipts(),
    )
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=t.fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=now,
        deadline_at=now + timedelta(minutes=5),
        input_sha256="b" * 64,
        documents=[document],
        attempts={key: 1},
        active_gap=key,
        active_fact_keys=[key],
        pending_query=t.SourceDiscoveryQuery(
            schema_version="1.0",
            context=t.fallback_module._operation(session),
            query="固定主营资料查询",
            missing_fact_keys=[key],
            include_domains=[registration.domain],
            end_date=now,
        ),
    )
    coordinator._acquisition, coordinator._packet = state, packet
    for _ in range(5):
        await coordinator._run_document_batch(
            document=document,
            session=session,
            registration=registration,
            requested_keys=[key],
        )
    assert flash.calls == 4
    assert [item.attempt_numbers[key] for item in state.candidates] == [1, 2, 3, 4]


@pytest.mark.parametrize("change", ["job", "observation"])
async def test_same_original_window_does_not_gain_a_call_from_new_identity(change: str) -> None:
    from uuid import uuid4

    now = FixedDatetime.now()
    session = t._session(now)
    key = "business.core_products"
    document, registration = t._document_fixture("603986 兆易创新\n主要业务\n固定说明。", now)
    packet = t._packet(session)
    packet = packet.model_copy(
        update={"facts": [packet.facts[0].model_copy(update={"key": "identity.company"})]}
    )
    flash = EmptyFlash()
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=t.fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=now,
        deadline_at=now + timedelta(minutes=5),
        input_sha256="b" * 64,
        documents=[document],
    )
    for step in range(2):
        coordinator = ResearchWebFallbackCoordinator(
            provider=flash,
            audit_provider=t._AuditProvider(),
            discovery=t._AcquisitionDiscovery(now),
            connector=t._FailingConnector(),
            rights=t._rights(),
            receipts=t._Receipts(),
        )
        coordinator._acquisition, coordinator._packet = state, packet
        await coordinator._run_document_batch(
            document=document, session=session, registration=registration, requested_keys=[key]
        )
        if step == 0:
            state = ResearchAcquisitionState.model_validate_json(state.model_dump_json())
            if change == "job":
                session = session.model_copy(update={"job_id": uuid4()})
            else:
                document = document.model_copy(
                    update={
                        "document_id": uuid4(),
                        "first_seen_at": now + timedelta(seconds=1),
                        "revision_known_at": now + timedelta(seconds=1),
                    }
                )
                state.documents.append(document)
    assert flash.calls == 1 and state.attempts[key] == 1
    assert len(state.candidates) == 1 and state.candidates[0].extraction_complete


async def test_fourth_attempt_saved_rejection_keeps_its_unique_correction() -> None:
    from stock_assistant.contracts.discovery import ResearchDataAuditVerdict

    now = FixedDatetime.now()
    session = t._session(now)
    key = "business.core_products"
    document, registration = t._document_fixture("603986 兆易创新\n主要业务\n固定说明。", now)
    packet = t._packet(session)
    packet = packet.model_copy(
        update={"facts": [packet.facts[0].model_copy(update={"key": "identity.company"})]}
    )
    flash = EmptyFlash()
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=t.fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=now,
        deadline_at=now + timedelta(minutes=5),
        input_sha256="b" * 64,
        documents=[document],
        attempts={key: 3},
    )

    def coordinator():
        value = ResearchWebFallbackCoordinator(
            provider=flash,
            audit_provider=t._AuditProvider(),
            discovery=t._AcquisitionDiscovery(now),
            connector=t._FailingConnector(),
            rights=t._rights(),
            receipts=t._Receipts(),
        )
        value._acquisition, value._packet = state, packet
        return value

    await coordinator()._run_document_batch(
        document=document, session=session, registration=registration, requested_keys=[key]
    )
    # Fixed saved audit result at the boundary before the existing correction.
    # This does not grant an additional attempt or mark any other window read.
    state.candidates[0].facts = [
        t.ResearchWebNormalizedFactDraft(
            missing_fact_key=key, source_excerpt="固定说明。", value="固定说明。"
        )
    ]
    state.candidates[0].verdicts = [
        ResearchDataAuditVerdict(
            fact_key=key,
            outcome="rejected",
            reason_code="EXCERPT_MISMATCH",
            reason="固定原文定位待纠正。",
        )
    ]
    state = ResearchAcquisitionState.model_validate_json(state.model_dump_json())
    for _ in range(2):
        await coordinator()._run_document_batch(
            document=document, session=session, registration=registration, requested_keys=[key]
        )
    assert state.attempts[key] == 4
    assert flash.calls == 2
    assert [c.attempt_numbers[key] for c in state.candidates] == [4, 4]
    assert state.candidates[-1].correction_keys == [key]
