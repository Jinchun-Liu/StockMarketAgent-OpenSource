"""R1 1.2 development-only free-source matrix behind existing source Ports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from pydantic import JsonValue, ValidationError

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.time import utc_now
from stock_assistant.config.yaml_config import load_versioned_config
from stock_assistant.contracts.ingestion import (
    ContentTier,
    FactEligibility,
    FetchBatch,
    FetchRequest,
    RightsCheck,
    RightsDecision,
    RightsOperation,
    RightsPermittedUse,
    SourceAccessChannelV1_2,
    SourceCapability,
    SourceDescriptor,
    SourceEvidenceRole,
    SourceProvenanceV1_2,
    SourceQualityTier,
    SourceRecord,
    SourceRecordV1_2,
    SourceReleaseTier,
    SourceRightsStatusV1_2,
    SourceRuntimeModeV1_2,
    SourceTier,
)
from stock_assistant.contracts.portfolio import R1_FIXED_TARGET_INSTRUMENT_ID
from stock_assistant.contracts.premarket_views import SourceCoverageStatus
from stock_assistant.modules.ingestion.r1_sources import R1SourceBatch, terminal_gap_batch
from stock_assistant.modules.ingestion.source_rights import normalize_r1_instrument_code
from stock_assistant.platform.sources.free_source_runner import FreeSourceRunResult
from stock_assistant.platform.sources.qinghong_flash_runner import (
    QINGHONG_DOCUMENTATION_URL,
    QINGHONG_ENDPOINTS,
    QINGHONG_PROVIDER,
    QINGHONG_REQUIRED_HOST,
    QINGHONG_SOURCE_IDS,
)
from stock_assistant.platform.sources.r1_dev_live import (
    DEV_LIVE_RIGHTS_HASH,
    AkshareDevLiveAdapter,
)
from stock_assistant.platform.sources.research_preview_runner import (
    PROMAX_PROVIDER,
    QUANTDATA_PROVIDER,
    RESEARCH_PREVIEW_PROVIDER_BY_CAPABILITY,
    RESEARCH_PREVIEW_SOURCE_IDS,
)
from stock_assistant.ports.ingestion import RightsPolicyPort

_SHANGHAI: Final = ZoneInfo("Asia/Shanghai")
_FREE_SOURCE_NAMESPACE: Final = UUID("4a79cd91-f42f-53ad-9f6f-ced5f246dcde")
FREE_SOURCE_ADAPTER_VERSION: Final = "r1-free-source-v1.2"
FREE_SOURCE_LICENSE_ID: Final = "public-upstream-rights-unverified-v1"
FREE_SOURCE_RIGHTS_HASH: Final = hashlib.sha256(
    b"r1-free-metadata-display-summary-nondisplay-v1"
).hexdigest()


@dataclass(frozen=True, slots=True)
class FreeSourceDefinition:
    """One fixed provider/capability route; no URLs may be supplied by callers."""

    source_id: str
    capability: str
    provider: str
    access_channel: SourceAccessChannelV1_2
    quality_tier: SourceQualityTier
    evidence_role: SourceEvidenceRole
    endpoint_name: str
    adapter_name: str
    canonical_url: str
    source_tier: SourceTier = SourceTier.DEVELOPMENT_ONLY
    content_tier: ContentTier = ContentTier.DEVELOPMENT_ONLY
    fact_eligibility: FactEligibility = FactEligibility.INELIGIBLE
    publisher: str | None = None
    category: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    enabled: bool = False


_DEV_LIVE_DISCOVERY_SOURCE_IDS: Final = (
    "gov_cn_public_macro",
    "miit_public_macro",
)
_DEV_LIVE_DISCOVERY_EXPECTATIONS: Final = {
    "gov_cn_public_macro": {
        "provider": "gov_cn",
        "endpoint_name": "official_gov_policy",
        "canonical_url": "https://sousuo.www.gov.cn/zcwjk/",
        "allowed_hosts": frozenset({"sousuo.www.gov.cn", "www.gov.cn"}),
    },
    "miit_public_macro": {
        "provider": "miit",
        "endpoint_name": "official_miit_policy",
        "canonical_url": "https://www.miit.gov.cn/zwgk/index.html",
        "allowed_hosts": frozenset({"www.miit.gov.cn"}),
    },
}
_RESEARCH_PREVIEW_EXPECTATIONS: Final = {
    "research_preview_announcements": {
        "capability": "statutory.announcements",
        "provider": PROMAX_PROVIDER,
        "adapter_name": "promax_tushare_research_http",
        "canonical_url": "https://www.yuque.com/tenglong-emkah/foo0eo/ztr29xs6iarfom71",
        "allowed_host": "pcd.mobcvb.cn",
        "endpoint_name": "anns_d",
        "content_tier": "statutory_disclosure",
        "fact_eligibility": "company_fact",
        "evidence_role": "structured_helper",
    },
    "research_preview_financials": {
        "capability": "financial.changes",
        "provider": PROMAX_PROVIDER,
        "adapter_name": "promax_tushare_research_http",
        "canonical_url": "https://www.yuque.com/tenglong-emkah/foo0eo/ztr29xs6iarfom71",
        "allowed_host": "pcd.mobcvb.cn",
        "endpoint_name": "income",
        "content_tier": "statutory_disclosure",
        "fact_eligibility": "company_fact",
        "evidence_role": "structured_helper",
    },
    "research_preview_market": {
        "capability": "market.snapshot",
        "provider": QUANTDATA_PROVIDER,
        "adapter_name": "quantdata888_tushare_research_http",
        "canonical_url": "https://quantdata888.duckdns.org",
        "allowed_host": "quantdata888.duckdns.org",
        "endpoint_name": "daily",
        "content_tier": "market_data",
        "fact_eligibility": "market_observation",
        "evidence_role": "structured_helper",
    },
    "research_preview_company_news": {
        "capability": "company_industry.news",
        "provider": PROMAX_PROVIDER,
        "adapter_name": "promax_tushare_research_http",
        "canonical_url": "https://www.yuque.com/tenglong-emkah/foo0eo/ztr29xs6iarfom71",
        "allowed_host": "pcd.mobcvb.cn",
        "endpoint_name": "news",
        "content_tier": "authoritative_financial_media",
        "fact_eligibility": "context_only",
        "evidence_role": "discovery",
    },
    "research_preview_macro": {
        "capability": "macro.flashes",
        "provider": PROMAX_PROVIDER,
        "adapter_name": "promax_tushare_research_http",
        "canonical_url": "https://www.yuque.com/tenglong-emkah/foo0eo/ztr29xs6iarfom71",
        "allowed_host": "pcd.mobcvb.cn",
        "endpoint_name": "npr",
        "content_tier": "official_policy",
        "fact_eligibility": "context_only",
        "evidence_role": "discovery",
    },
}
_RESEARCH_PREVIEW_SOURCE_ID_SET: Final = frozenset(RESEARCH_PREVIEW_SOURCE_IDS.values())
_QINGHONG_EXPECTATIONS: Final = {
    "qinghong_cninfo_flash": {
        "capability": "statutory.announcements",
        "content_tier": "statutory_disclosure",
        "endpoint_name": "cninfo-latest",
    },
    "qinghong_cls_flash": {
        "capability": "company_industry.news",
        "content_tier": "authoritative_financial_media",
        "endpoint_name": "cls-telegraph",
    },
    "qinghong_jin10_flash": {
        "capability": "macro.flashes",
        "content_tier": "authoritative_financial_media",
        "endpoint_name": "jin10",
    },
}
_QINGHONG_SOURCE_ID_SET: Final = frozenset(QINGHONG_SOURCE_IDS.values())
_RESEARCH_PREVIEW_OVERLAP: Final = timedelta(days=7)
_QINGHONG_OVERLAP: Final = timedelta(days=7)
_QUALIFIED_OFFICIAL_HTTP_SOURCE_IDS: Final = (
    frozenset(_DEV_LIVE_DISCOVERY_SOURCE_IDS)
    | (_RESEARCH_PREVIEW_SOURCE_ID_SET)
    | _QINGHONG_SOURCE_ID_SET
)


def _required_config_string(mapping: Mapping[object, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"DEV_LIVE source config requires {key}")
    return value.strip()


def configured_discovery_sources(
    document: Mapping[str, object],
) -> tuple[FreeSourceDefinition, ...]:
    """Validate every configured route, including routes currently disabled."""

    raw_sources: object = document.get("sources")
    if not isinstance(raw_sources, list):
        raise RuntimeError("sources.yaml must contain a sources list")
    sources: dict[str, Mapping[object, object]] = {}
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            raise RuntimeError("sources.yaml source entries must be objects")
        source_entry = cast(dict[object, object], raw_source)
        source_id = source_entry.get("id")
        if isinstance(source_id, str):
            sources[source_id] = source_entry

    definitions: list[FreeSourceDefinition] = []
    for source_id in _DEV_LIVE_DISCOVERY_SOURCE_IDS:
        try:
            configured_source = sources[source_id]
        except KeyError as error:
            raise RuntimeError(f"missing DEV_LIVE source config: {source_id}") from error
        enabled = configured_source.get("enabled")
        if not isinstance(enabled, bool):
            raise RuntimeError(f"DEV_LIVE source requires a boolean enabled flag: {source_id}")
        raw_profile = configured_source.get("dev_live")
        if not isinstance(raw_profile, dict):
            raise RuntimeError(f"DEV_LIVE source profile is missing: {source_id}")
        profile = cast(dict[object, object], raw_profile)
        if (
            profile.get("runtime_mode") != "dev_live"
            or profile.get("release_tier") != "dev_only"
            or profile.get("rights_status") != "unverified"
        ):
            raise RuntimeError(f"DEV_LIVE source safety labels are invalid: {source_id}")
        raw_hosts = profile.get("allowed_hosts")
        if (
            not isinstance(raw_hosts, list)
            or not raw_hosts
            or any(not isinstance(host, str) or not host for host in raw_hosts)
        ):
            raise RuntimeError(f"DEV_LIVE source requires a non-empty host allowlist: {source_id}")
        access_channel = SourceAccessChannelV1_2(_required_config_string(profile, "access_channel"))
        if access_channel is not SourceAccessChannelV1_2.OFFICIAL_HTTP:
            raise RuntimeError(f"DEV_LIVE discovery must use official HTTPS: {source_id}")
        definition = FreeSourceDefinition(
            source_id=source_id,
            capability=_required_config_string(profile, "capability"),
            provider=_required_config_string(profile, "provider"),
            access_channel=access_channel,
            quality_tier=SourceQualityTier(_required_config_string(profile, "quality_tier")),
            evidence_role=SourceEvidenceRole(_required_config_string(profile, "evidence_role")),
            endpoint_name=_required_config_string(profile, "endpoint_name"),
            adapter_name=_required_config_string(profile, "adapter_name"),
            canonical_url=_required_config_string(profile, "canonical_url"),
            source_tier=SourceTier(_required_config_string(configured_source, "tier")),
            content_tier=ContentTier(_required_config_string(configured_source, "tier")),
            fact_eligibility=FactEligibility(_required_config_string(profile, "fact_eligibility")),
            publisher=_required_config_string(profile, "publisher"),
            category=_required_config_string(profile, "category"),
            allowed_hosts=tuple(cast(list[str], raw_hosts)),
            enabled=enabled,
        )
        expected = _DEV_LIVE_DISCOVERY_EXPECTATIONS[source_id]
        parsed_url = urlsplit(definition.canonical_url)
        if (
            definition.capability != "macro.flashes"
            or definition.provider != expected["provider"]
            or definition.endpoint_name != expected["endpoint_name"]
            or definition.canonical_url != expected["canonical_url"]
            or definition.source_tier is not SourceTier.OFFICIAL_POLICY
            or definition.content_tier is not ContentTier.OFFICIAL_POLICY
            or definition.quality_tier is not SourceQualityTier.FREE_OFFICIAL
            or definition.evidence_role is not SourceEvidenceRole.DISCOVERY
            or definition.fact_eligibility is not FactEligibility.CONTEXT_ONLY
            or parsed_url.scheme != "https"
            or parsed_url.username is not None
            or parsed_url.password is not None
            or frozenset(definition.allowed_hosts) != expected["allowed_hosts"]
        ):
            raise RuntimeError(f"DEV_LIVE source discovery boundary is invalid: {source_id}")
        definitions.append(definition)
    return tuple(definitions)


def _load_dev_live_discovery_sources() -> tuple[FreeSourceDefinition, ...]:
    document = load_versioned_config("configs/sources.yaml")
    return configured_discovery_sources(cast(Mapping[str, object], document.data))


def configured_research_preview_sources(
    document: Mapping[str, object],
) -> tuple[FreeSourceDefinition, ...]:
    """Validate the five R1-R6 research routes without accepting caller URLs."""

    raw_sources = document.get("sources")
    if not isinstance(raw_sources, list):
        raise RuntimeError("sources.yaml must contain a sources list")
    sources: dict[str, Mapping[object, object]] = {}
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            raise RuntimeError("sources.yaml source entries must be objects")
        entry = cast(dict[object, object], raw_source)
        source_id = entry.get("id")
        if isinstance(source_id, str):
            sources[source_id] = entry

    definitions: list[FreeSourceDefinition] = []
    for source_id, expected in _RESEARCH_PREVIEW_EXPECTATIONS.items():
        try:
            source = sources[source_id]
        except KeyError as error:
            raise RuntimeError(f"missing research preview source config: {source_id}") from error
        enabled = source.get("enabled")
        profile = source.get("dev_live")
        if not isinstance(enabled, bool) or not isinstance(profile, dict):
            raise RuntimeError(f"invalid research preview source profile: {source_id}")
        typed_profile = cast(dict[object, object], profile)
        raw_hosts = typed_profile.get("allowed_hosts")
        expected_host = expected["allowed_host"]
        if raw_hosts != [expected_host]:
            raise RuntimeError(f"research preview host allowlist is invalid: {source_id}")
        definition = FreeSourceDefinition(
            source_id=source_id,
            capability=_required_config_string(typed_profile, "capability"),
            provider=_required_config_string(typed_profile, "provider"),
            access_channel=SourceAccessChannelV1_2(
                _required_config_string(typed_profile, "access_channel")
            ),
            quality_tier=SourceQualityTier(_required_config_string(typed_profile, "quality_tier")),
            evidence_role=SourceEvidenceRole(
                _required_config_string(typed_profile, "evidence_role")
            ),
            endpoint_name=_required_config_string(typed_profile, "endpoint_name"),
            adapter_name=_required_config_string(typed_profile, "adapter_name"),
            canonical_url=_required_config_string(typed_profile, "canonical_url"),
            source_tier=SourceTier(_required_config_string(source, "tier")),
            content_tier=ContentTier(_required_config_string(typed_profile, "content_tier")),
            fact_eligibility=FactEligibility(
                _required_config_string(typed_profile, "fact_eligibility")
            ),
            allowed_hosts=(expected_host,),
            enabled=enabled,
        )
        parsed_url = urlsplit(definition.canonical_url)
        if (
            definition.capability != expected["capability"]
            or definition.provider != expected["provider"]
            or RESEARCH_PREVIEW_PROVIDER_BY_CAPABILITY[definition.capability] != definition.provider
            or definition.endpoint_name != expected["endpoint_name"]
            or definition.adapter_name != expected["adapter_name"]
            or definition.access_channel is not SourceAccessChannelV1_2.OFFICIAL_HTTP
            or definition.quality_tier is not SourceQualityTier.FREE_STRUCTURED
            or definition.evidence_role.value != expected["evidence_role"]
            or definition.content_tier.value != expected["content_tier"]
            or definition.fact_eligibility.value != expected["fact_eligibility"]
            or definition.source_tier is not SourceTier.LICENSED_AGGREGATOR
            or parsed_url.scheme != "https"
            or definition.canonical_url != expected["canonical_url"]
            or parsed_url.hostname != urlsplit(expected["canonical_url"]).hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or typed_profile.get("runtime_mode") != "dev_live"
            or typed_profile.get("release_tier") != "dev_only"
            or typed_profile.get("rights_status") != "unverified"
        ):
            raise RuntimeError(f"research preview source boundary is invalid: {source_id}")
        definitions.append(definition)
    return tuple(definitions)


def _load_research_preview_sources() -> tuple[FreeSourceDefinition, ...]:
    document = load_versioned_config("configs/sources.yaml")
    return configured_research_preview_sources(cast(Mapping[str, object], document.data))


def configured_qinghong_sources(
    document: Mapping[str, object],
) -> tuple[FreeSourceDefinition, ...]:
    """Validate the dormant Qinghong routes without accepting caller URLs."""

    raw_sources = document.get("sources")
    if not isinstance(raw_sources, list):
        raise RuntimeError("sources.yaml must contain a sources list")
    sources: dict[str, Mapping[object, object]] = {}
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            raise RuntimeError("sources.yaml source entries must be objects")
        entry = cast(dict[object, object], raw_source)
        source_id = entry.get("id")
        if isinstance(source_id, str):
            sources[source_id] = entry

    definitions: list[FreeSourceDefinition] = []
    for source_id, expected in _QINGHONG_EXPECTATIONS.items():
        try:
            source = sources[source_id]
        except KeyError as error:
            raise RuntimeError(f"missing Qinghong source config: {source_id}") from error
        enabled = source.get("enabled")
        profile = source.get("dev_live")
        if not isinstance(enabled, bool) or not isinstance(profile, dict):
            raise RuntimeError(f"invalid Qinghong source profile: {source_id}")
        typed_profile = cast(dict[object, object], profile)
        if typed_profile.get("allowed_hosts") != [QINGHONG_REQUIRED_HOST]:
            raise RuntimeError(f"Qinghong host allowlist is invalid: {source_id}")
        definition = FreeSourceDefinition(
            source_id=source_id,
            capability=_required_config_string(typed_profile, "capability"),
            provider=_required_config_string(typed_profile, "provider"),
            access_channel=SourceAccessChannelV1_2(
                _required_config_string(typed_profile, "access_channel")
            ),
            quality_tier=SourceQualityTier(_required_config_string(typed_profile, "quality_tier")),
            evidence_role=SourceEvidenceRole(
                _required_config_string(typed_profile, "evidence_role")
            ),
            endpoint_name=_required_config_string(typed_profile, "endpoint_name"),
            adapter_name=_required_config_string(typed_profile, "adapter_name"),
            canonical_url=_required_config_string(typed_profile, "canonical_url"),
            source_tier=SourceTier(_required_config_string(source, "tier")),
            content_tier=ContentTier(_required_config_string(typed_profile, "content_tier")),
            fact_eligibility=FactEligibility(
                _required_config_string(typed_profile, "fact_eligibility")
            ),
            allowed_hosts=(QINGHONG_REQUIRED_HOST,),
            enabled=enabled,
        )
        parsed_documentation_url = urlsplit(definition.canonical_url)
        if (
            definition.capability != expected["capability"]
            or definition.provider != QINGHONG_PROVIDER
            or definition.endpoint_name != expected["endpoint_name"]
            or QINGHONG_ENDPOINTS[source_id] != definition.endpoint_name
            or definition.adapter_name != "qinghong_finance_flash_http"
            or definition.access_channel is not SourceAccessChannelV1_2.OFFICIAL_HTTP
            or definition.quality_tier is not SourceQualityTier.FREE_STRUCTURED
            or definition.evidence_role is not SourceEvidenceRole.DISCOVERY
            or definition.content_tier.value != expected["content_tier"]
            or definition.fact_eligibility is not FactEligibility.CONTEXT_ONLY
            or definition.source_tier is not SourceTier.LICENSED_AGGREGATOR
            or parsed_documentation_url.scheme != "https"
            or parsed_documentation_url.hostname != "my.feishu.cn"
            or parsed_documentation_url.username is not None
            or parsed_documentation_url.password is not None
            or definition.canonical_url != QINGHONG_DOCUMENTATION_URL
            or definition.enabled
            or typed_profile.get("activation_blocker") != "https_endpoint_required"
            or typed_profile.get("runtime_mode") != "dev_live"
            or typed_profile.get("release_tier") != "dev_only"
            or typed_profile.get("rights_status") != "unverified"
        ):
            raise RuntimeError(f"Qinghong source boundary is invalid: {source_id}")
        definitions.append(definition)
    return tuple(definitions)


def _load_qinghong_sources() -> tuple[FreeSourceDefinition, ...]:
    document = load_versioned_config("configs/sources.yaml")
    return configured_qinghong_sources(cast(Mapping[str, object], document.data))


def active_free_source_definitions(
    catalog: Sequence[FreeSourceDefinition],
) -> tuple[FreeSourceDefinition, ...]:
    """Return the only definitions eligible for runtime registration and scheduling."""

    return tuple(item for item in catalog if item.enabled)


FREE_SOURCE_CATALOG: Final = (
    FreeSourceDefinition(
        "cninfo_public_statutory",
        "statutory.announcements",
        "cninfo_public",
        SourceAccessChannelV1_2.AKSHARE,
        SourceQualityTier.FREE_OFFICIAL,
        SourceEvidenceRole.AUTHORITATIVE,
        "stock_zh_a_disclosure_report_cninfo",
        "akshare_cninfo_public",
        "https://www.cninfo.com.cn/",
        SourceTier.STATUTORY_DISCLOSURE,
        ContentTier.STATUTORY_DISCLOSURE,
        FactEligibility.COMPANY_FACT,
    ),
    FreeSourceDefinition(
        "sse_public_disclosure",
        "statutory.announcements",
        "sse_public",
        SourceAccessChannelV1_2.OFFICIAL_HTTP,
        SourceQualityTier.FREE_OFFICIAL,
        SourceEvidenceRole.CROSS_CHECK,
        "official_sse_disclosures",
        "official_http_sse",
        "https://www.sse.com.cn/",
        SourceTier.STATUTORY_DISCLOSURE,
        ContentTier.STATUTORY_DISCLOSURE,
        FactEligibility.COMPANY_FACT,
    ),
    FreeSourceDefinition(
        "cninfo_public_financial",
        "financial.changes",
        "cninfo_public",
        SourceAccessChannelV1_2.AKSHARE,
        SourceQualityTier.FREE_OFFICIAL,
        SourceEvidenceRole.AUTHORITATIVE,
        "stock_zh_a_disclosure_report_cninfo",
        "akshare_cninfo_reports",
        "https://www.cninfo.com.cn/",
        SourceTier.STATUTORY_DISCLOSURE,
        ContentTier.STATUTORY_DISCLOSURE,
        FactEligibility.COMPANY_FACT,
    ),
    FreeSourceDefinition(
        "sse_public_financial",
        "financial.changes",
        "sse_public",
        SourceAccessChannelV1_2.OFFICIAL_HTTP,
        SourceQualityTier.FREE_OFFICIAL,
        SourceEvidenceRole.AUTHORITATIVE,
        "official_sse_reports",
        "official_http_sse",
        "https://www.sse.com.cn/",
        SourceTier.STATUTORY_DISCLOSURE,
        ContentTier.STATUTORY_DISCLOSURE,
        FactEligibility.COMPANY_FACT,
    ),
    FreeSourceDefinition(
        "eastmoney_public_financial",
        "financial.changes",
        "eastmoney_public",
        SourceAccessChannelV1_2.AKSHARE,
        SourceQualityTier.FREE_STRUCTURED,
        SourceEvidenceRole.STRUCTURED_HELPER,
        "stock_financial_analysis_indicator_em",
        "akshare_eastmoney_public",
        "https://www.eastmoney.com/",
    ),
    FreeSourceDefinition(
        "eastmoney_public_market",
        "market.snapshot",
        "eastmoney_public",
        SourceAccessChannelV1_2.AKSHARE,
        SourceQualityTier.FREE_STRUCTURED,
        SourceEvidenceRole.STRUCTURED_HELPER,
        "stock_zh_a_hist",
        "akshare_eastmoney_public",
        "https://www.eastmoney.com/",
        SourceTier.MARKET_DATA,
        ContentTier.MARKET_DATA,
        FactEligibility.MARKET_OBSERVATION,
    ),
    FreeSourceDefinition(
        "baostock_public_market",
        "market.snapshot",
        "baostock",
        SourceAccessChannelV1_2.BAOSTOCK_SDK,
        SourceQualityTier.FREE_STRUCTURED,
        SourceEvidenceRole.CROSS_CHECK,
        "baostock_history",
        "baostock_public",
        "https://www.baostock.com/",
        SourceTier.MARKET_DATA,
        ContentTier.MARKET_DATA,
        FactEligibility.MARKET_OBSERVATION,
    ),
    FreeSourceDefinition(
        "eastmoney_public_news",
        "company_industry.news",
        "eastmoney_public",
        SourceAccessChannelV1_2.AKSHARE,
        SourceQualityTier.DEV_WEB,
        SourceEvidenceRole.DISCOVERY,
        "stock_news_em",
        "akshare_eastmoney_public",
        "https://www.eastmoney.com/",
    ),
    *_load_research_preview_sources(),
    *_load_qinghong_sources(),
    *_load_dev_live_discovery_sources(),
)
FREE_SOURCE_CATALOG_BY_ID: Final = {item.source_id: item for item in FREE_SOURCE_CATALOG}
FREE_SOURCE_MATRIX: Final = active_free_source_definitions(FREE_SOURCE_CATALOG)
FREE_SOURCE_BY_ID: Final = {item.source_id: item for item in FREE_SOURCE_MATRIX}
DISABLED_OFFICIAL_DOMAINS: Final = (
    "ndrc.gov.cn",
    "mofcom.gov.cn",
    "pbc.gov.cn",
)

_REQUIRED_RIGHTS: Final = (
    (RightsOperation.FETCH, RightsPermittedUse.NON_DISPLAY),
    (RightsOperation.CACHE_METADATA, RightsPermittedUse.NON_DISPLAY),
    (RightsOperation.RETAIN, RightsPermittedUse.NON_DISPLAY),
    (RightsOperation.DERIVE, RightsPermittedUse.DISPLAY),
    (RightsOperation.DERIVE, RightsPermittedUse.SUMMARY),
)
_ALLOWED_OPERATIONS: Final = {
    RightsOperation.FETCH,
    RightsOperation.CACHE_METADATA,
    RightsOperation.RETAIN,
    RightsOperation.DERIVE,
}
_ALLOWED_USES: Final = {
    RightsPermittedUse.DISPLAY,
    RightsPermittedUse.SUMMARY,
    RightsPermittedUse.NON_DISPLAY,
}
_LEGACY_CAPABILITY_FOR_SPEC: Final = {
    "stock_zh_a_disclosure_report_cninfo": "statutory.announcements",
    "stock_financial_analysis_indicator_em": "financial.changes",
    "stock_zh_a_hist": "market.snapshot",
    "stock_news_em": "company_industry.news",
}


class RowsRunner(Protocol):
    async def run(
        self,
        endpoint_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> FreeSourceRunResult | list[dict[str, JsonValue]]: ...


class FreeDevelopmentRightsPolicy:
    """R1-R6 research profile; never an R7 provider authorization grant."""

    async def authorize(self, check: RightsCheck) -> RightsDecision:
        allowed = (
            check.source_id in FREE_SOURCE_BY_ID
            and check.rights_snapshot_hash == FREE_SOURCE_RIGHTS_HASH
            and check.operation in _ALLOWED_OPERATIONS
            and check.permitted_use in _ALLOWED_USES
        )
        return RightsDecision(
            allowed=allowed,
            reason_code="R1_R6_RESEARCH_ALLOWED" if allowed else "FREE_RIGHTS_BLOCKED",
            decided_at=check.context.as_of,
            rights_snapshot_hash=check.rights_snapshot_hash,
            retention_until=(check.context.as_of + timedelta(days=1) if allowed else None),
        )


class FreeLiveAdapter:
    """Pure request/normalization policy for one fixed free-source route."""

    def __init__(self, source_id: str) -> None:
        try:
            self.definition = FREE_SOURCE_CATALOG_BY_ID[source_id]
        except KeyError as error:
            raise ValueError(f"unknown R1 free source: {source_id}") from error
        definition = self.definition
        self.source_id = definition.source_id
        self.capability = definition.capability
        self.endpoint_name = definition.endpoint_name
        self.provenance = SourceProvenanceV1_2(
            schema_version="1.2",
            provider=definition.provider,
            access_channel=definition.access_channel,
            adapter_name=definition.adapter_name,
            adapter_version=FREE_SOURCE_ADAPTER_VERSION,
            endpoint_name=definition.endpoint_name,
            source_identifier=f"603986.SH:{definition.source_id}:{definition.capability}",
            runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            release_tier=SourceReleaseTier.DEV_ONLY,
            rights_status=SourceRightsStatusV1_2.UNVERIFIED,
            quality_tier=definition.quality_tier,
            evidence_role=definition.evidence_role,
        )
        self.descriptor = SourceDescriptor(
            schema_version="1.0",
            source_id=definition.source_id,
            display_name=f"R1 free {definition.provider} {definition.capability}",
            adapter_version=FREE_SOURCE_ADAPTER_VERSION,
            source_tier=definition.source_tier,
            capabilities=[
                SourceCapability(
                    capability=definition.capability,
                    content_tier=definition.content_tier,
                    fact_eligibility=definition.fact_eligibility,
                )
            ],
            authority=0.0,
            enabled=definition.enabled,
            rights_registry_id="r1-free-upstream-unverified",
        )

    def validate_request(self, request: FetchRequest) -> None:
        if request.source_id != self.source_id or request.capability != self.capability:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")
        if request.rights_snapshot_hash != FREE_SOURCE_RIGHTS_HASH:
            raise ApplicationError(ErrorCode.RIGHTS_BLOCKED, "FREE_RIGHTS_PROFILE_MISMATCH")
        if request.instrument_ids != [R1_FIXED_TARGET_INSTRUMENT_ID]:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_TARGET_LOCKED")
        normalize_r1_instrument_code(request.instrument_ids[0])
        if request.until is None:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_WINDOW_REQUIRED")

    def function_arguments(self, request: FetchRequest) -> dict[str, JsonValue]:
        self.validate_request(request)
        assert request.until is not None
        if self.source_id in _RESEARCH_PREVIEW_SOURCE_ID_SET:
            return _research_preview_arguments(self, request)
        if self.source_id in _QINGHONG_SOURCE_ID_SET:
            return _qinghong_arguments(self)
        if self.definition.access_channel is SourceAccessChannelV1_2.AKSHARE:
            legacy_capability = _LEGACY_CAPABILITY_FOR_SPEC[self.endpoint_name]
            legacy = AkshareDevLiveAdapter(legacy_capability)
            return legacy.function_arguments(
                request.model_copy(
                    update={
                        "source_id": legacy.source_id,
                        "capability": legacy_capability,
                        "rights_snapshot_hash": DEV_LIVE_RIGHTS_HASH,
                    }
                )
            )
        start = (request.since or request.until - timedelta(days=30)).astimezone(_SHANGHAI)
        end = request.until.astimezone(_SHANGHAI)
        if self.definition.access_channel is SourceAccessChannelV1_2.BAOSTOCK_SDK:
            return {
                "code": "sh.603986",
                "fields": "date,code,open,high,low,close,volume,amount",
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": end.strftime("%Y-%m-%d"),
            }
        arguments: dict[str, JsonValue] = {
            "instrument_id": R1_FIXED_TARGET_INSTRUMENT_ID,
            "since": start.isoformat(),
            "until": end.isoformat(),
        }
        if request.cursor is not None:
            conditional = _decode_official_cursor(request.cursor)
            if conditional[2] > request.until:
                raise ApplicationError(
                    ErrorCode.PRECONDITION_FAILED,
                    "R1_SOURCE_CURSOR_INVALID",
                )
            if conditional[0] is not None:
                arguments["if_none_match"] = conditional[0]
            if conditional[1] is not None:
                arguments["if_modified_since"] = conditional[1]
        return arguments

    def normalize(
        self,
        request: FetchRequest,
        rows: Sequence[Mapping[str, JsonValue]],
        *,
        execution_started_at: datetime,
        execution_completed_at: datetime,
        response_etag: str | None = None,
        response_last_modified: str | None = None,
    ) -> FetchBatch:
        self.validate_request(request)
        if (
            self.definition.access_channel is SourceAccessChannelV1_2.OFFICIAL_HTTP
            and self.source_id not in _QUALIFIED_OFFICIAL_HTTP_SOURCE_IDS
        ):
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "FREE_SOURCE_NOT_QUALIFIED",
            )
        if execution_completed_at < execution_started_at:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_CLOCK_INVALID")
        try:
            records = (
                self._normalize_akshare(
                    request,
                    rows,
                    execution_started_at=execution_started_at,
                    execution_completed_at=execution_completed_at,
                )
                if self.definition.access_channel is SourceAccessChannelV1_2.AKSHARE
                else self._normalize_structured(
                    request,
                    rows,
                    execution_started_at=execution_started_at,
                    execution_completed_at=execution_completed_at,
                )
            )
        except ApplicationError:
            raise
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_FREE_SOURCE_SCHEMA_INVALID",
            ) from error
        assert request.until is not None
        cursor = (
            request.cursor
            if self.source_id in _QINGHONG_SOURCE_ID_SET and not records
            else _encode_official_cursor(
                window_end=request.until,
                etag=response_etag,
                last_modified=response_last_modified,
            )
            if self.definition.access_channel is SourceAccessChannelV1_2.OFFICIAL_HTTP
            else request.until.isoformat()
        )
        identity = _canonical_json(
            {
                "source_id": self.source_id,
                "capability": self.capability,
                "cursor": cursor,
                "records": [str(item.record_id) for item in records],
            }
        )
        if self.source_id in _RESEARCH_PREVIEW_SOURCE_ID_SET:
            source_watermark = max(
                (item.event_time for item in records),
                default=_research_preview_start(request),
            )
        elif self.source_id in _QINGHONG_SOURCE_ID_SET:
            source_watermark = max(
                (item.event_time for item in records),
                default=_qinghong_start(request),
            )
        elif self.definition.access_channel is SourceAccessChannelV1_2.OFFICIAL_HTTP:
            source_watermark = request.until
        else:
            source_watermark = max((item.event_time for item in records), default=request.until)
        return FetchBatch(
            schema_version="1.0",
            batch_id=uuid5(_FREE_SOURCE_NAMESPACE, f"batch:{identity}"),
            source_id=self.source_id,
            records=list(records),
            next_cursor=cursor,
            exhausted=True,
            started_at=execution_started_at,
            completed_at=execution_completed_at,
            source_watermark=source_watermark,
            rights_snapshot_hash=request.rights_snapshot_hash,
        )

    def _normalize_akshare(
        self,
        request: FetchRequest,
        rows: Sequence[Mapping[str, JsonValue]],
        *,
        execution_started_at: datetime,
        execution_completed_at: datetime,
    ) -> tuple[SourceRecordV1_2, ...]:
        self._validate_provider_rows(rows)
        legacy_capability = _LEGACY_CAPABILITY_FOR_SPEC[self.endpoint_name]
        legacy = AkshareDevLiveAdapter(legacy_capability)
        legacy_request = request.model_copy(
            update={
                "source_id": legacy.source_id,
                "capability": legacy_capability,
                "rights_snapshot_hash": DEV_LIVE_RIGHTS_HASH,
            }
        )
        batch = legacy.normalize(
            legacy_request,
            rows,
            execution_started_at=execution_started_at,
            execution_completed_at=execution_completed_at,
        )
        return tuple(self._project_record(request, record) for record in batch.records)

    def _normalize_structured(
        self,
        request: FetchRequest,
        rows: Sequence[Mapping[str, JsonValue]],
        *,
        execution_started_at: datetime,
        execution_completed_at: datetime,
    ) -> tuple[SourceRecordV1_2, ...]:
        assert request.until is not None
        if any(row.get("page_observation") is True for row in rows):
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "FREE_SOURCE_NOT_QUALIFIED")
        records: dict[UUID, SourceRecordV1_2] = {}
        lower_bound = (
            _research_preview_start(request)
            if self.source_id in _RESEARCH_PREVIEW_SOURCE_ID_SET
            else _qinghong_start(request)
            if self.source_id in _QINGHONG_SOURCE_ID_SET
            else request.since
        )
        for row in rows:
            if self.source_id in _RESEARCH_PREVIEW_SOURCE_ID_SET and not _research_row_relevant(
                self.endpoint_name,
                row,
            ):
                continue
            if self.source_id in _QINGHONG_SOURCE_ID_SET and not _qinghong_row_relevant(
                self.endpoint_name,
                row,
            ):
                continue
            payload, title, canonical_url, event_time = self._structured_fields(
                row,
            )
            if event_time > request.until or (lower_bound is not None and event_time < lower_bound):
                continue
            content_hash = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
            external_id = (
                _research_external_id(self.endpoint_name, row, content_hash)
                if self.source_id in _RESEARCH_PREVIEW_SOURCE_ID_SET
                else _qinghong_external_id(self.endpoint_name, row, content_hash)
                if self.source_id in _QINGHONG_SOURCE_ID_SET
                else str(
                    row.get("document_id") or row.get("url") or row.get("date") or content_hash
                )[:500]
            )
            record_identity = f"record:{self.source_id}:{self.capability}:{external_id}"
            if self.definition.access_channel is not SourceAccessChannelV1_2.OFFICIAL_HTTP:
                record_identity = f"{record_identity}:{content_hash}"
            record_id = uuid5(_FREE_SOURCE_NAMESPACE, record_identity)
            records[record_id] = SourceRecordV1_2(
                record_id=record_id,
                source_id=self.source_id,
                external_id=external_id,
                capability=self.capability,
                record_type=(
                    _discovery_record_type(self.capability)
                    if self.definition.evidence_role is SourceEvidenceRole.DISCOVERY
                    else self.capability
                ),
                source_tier=self.definition.source_tier,
                content_tier=self.definition.content_tier,
                fact_eligibility=self.definition.fact_eligibility,
                authority=0.0,
                title=title,
                canonical_url=canonical_url,
                event_time=event_time,
                published_at=event_time,
                first_seen_at=execution_started_at,
                fetched_at=execution_completed_at,
                content_sha256=content_hash,
                payload=payload,
                payload_namespace="stock_assistant.r1_free_live",
                payload_owner="dmi",
                rights_snapshot_hash=request.rights_snapshot_hash,
                provenance=self.provenance,
            )
        ordered = tuple(records[key] for key in sorted(records, key=str))
        if (
            self.source_id in _RESEARCH_PREVIEW_SOURCE_ID_SET | _QINGHONG_SOURCE_ID_SET
            and len(ordered) > request.limit
        ):
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_SOURCE_RESULT_TRUNCATED",
            )
        return ordered[: request.limit]

    def _project_record(self, request: FetchRequest, record: SourceRecord) -> SourceRecordV1_2:
        payload = record.model_dump(mode="json")
        payload.update(
            {
                "record_id": uuid5(
                    _FREE_SOURCE_NAMESPACE,
                    f"record:{self.source_id}:{record.external_id}:{record.content_sha256}",
                ),
                "source_id": self.source_id,
                "capability": self.capability,
                "record_type": self.capability,
                "source_tier": self.definition.source_tier,
                "content_tier": self.definition.content_tier,
                "fact_eligibility": self.definition.fact_eligibility,
                "rights_snapshot_hash": request.rights_snapshot_hash,
                "provenance": self.provenance,
                "payload_namespace": "stock_assistant.r1_free_live",
                "payload_owner": "dmi",
            }
        )
        return SourceRecordV1_2.model_validate(payload)

    def _validate_provider_rows(self, rows: Sequence[Mapping[str, JsonValue]]) -> None:
        accepted = {"603986", "603986.sh", "sh.603986", "xshg:603986"}
        for row in rows:
            for key in ("代码", "股票代码", "证券代码", "symbol", "code"):
                value = row.get(key)
                if value not in (None, "") and str(value).strip().lower() not in accepted:
                    raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_TARGET_LOCKED")

    def _structured_fields(
        self,
        row: Mapping[str, JsonValue],
    ) -> tuple[dict[str, JsonValue], str, str | None, datetime]:
        if self.source_id in _RESEARCH_PREVIEW_SOURCE_ID_SET:
            return _research_structured_fields(self.endpoint_name, row)
        if self.source_id in _QINGHONG_SOURCE_ID_SET:
            return _qinghong_structured_fields(self.endpoint_name, row)
        if self.definition.access_channel is SourceAccessChannelV1_2.BAOSTOCK_SDK:
            required = ("date", "code", "close")
            if any(row.get(key) is None for key in required):
                raise ValueError("missing BaoStock market field")
            if str(row["code"]).strip().lower() != "sh.603986":
                raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_TARGET_LOCKED")
            fields: Sequence[str] = (
                "date",
                "code",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            )
            payload = {key: row[key] for key in fields if row.get(key) not in (None, "")}
            return payload, f"603986.SH {row['date']} 行情", None, _parse_datetime(row["date"])
        required = ("title", "published_at", "url")
        if any(row.get(key) is None for key in required):
            raise ValueError("missing official metadata field")
        publisher = row.get("publisher")
        category = row.get("category")
        if not isinstance(publisher, str) or not publisher.strip():
            raise ValueError("missing official metadata publisher")
        if not isinstance(category, str) or not category.strip():
            raise ValueError("missing official metadata category")
        canonical_url = str(row["url"])
        parsed_url = urlsplit(canonical_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname not in self.definition.allowed_hosts
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "FREE_SOURCE_FORBIDDEN")
        fields = ("title", "published_at", "publisher", "category", "url")
        payload = {key: row[key] for key in fields}
        return (
            payload,
            str(row["title"])[:500],
            canonical_url,
            _parse_datetime(row["published_at"]),
        )


class RightsGatedFreeConnector:
    """SourceConnectorPort adapter enforcing rights before login or network."""

    def __init__(
        self,
        *,
        adapter: FreeLiveAdapter,
        rights_policy: RightsPolicyPort,
        runner: RowsRunner,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._adapter = adapter
        self._rights_policy = rights_policy
        self._runner = runner
        self._clock = clock

    @property
    def descriptor(self) -> SourceDescriptor:
        return self._adapter.descriptor

    async def fetch(self, request: FetchRequest) -> FetchBatch:
        self._adapter.validate_request(request)
        for operation, permitted_use in _REQUIRED_RIGHTS:
            decision = await self._rights_policy.authorize(
                RightsCheck(
                    context=request.context,
                    source_id=request.source_id,
                    operation=operation,
                    rights_snapshot_hash=request.rights_snapshot_hash,
                    content_category=request.capability,
                    permitted_use=permitted_use,
                )
            )
            if (
                not decision.allowed
                or decision.rights_snapshot_hash != request.rights_snapshot_hash
                or decision.decided_at > request.context.as_of
                or (
                    decision.retention_until is not None
                    and decision.retention_until < request.context.as_of
                )
            ):
                raise ApplicationError(ErrorCode.RIGHTS_BLOCKED, decision.reason_code)
        if (
            self._adapter.definition.access_channel is SourceAccessChannelV1_2.OFFICIAL_HTTP
            and self._adapter.source_id not in _QUALIFIED_OFFICIAL_HTTP_SOURCE_IDS
        ):
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "FREE_SOURCE_NOT_QUALIFIED",
            )
        started_at = _aware_utc(self._clock())
        run_result = await self._runner.run(
            self._adapter.endpoint_name,
            self._adapter.function_arguments(request),
        )
        completed_at = _aware_utc(self._clock())
        if isinstance(run_result, FreeSourceRunResult):
            rows = run_result.rows
            response_etag = run_result.response_etag
            response_last_modified = run_result.response_last_modified
        else:
            rows = run_result
            response_etag = None
            response_last_modified = None
        return self._adapter.normalize(
            request,
            rows,
            execution_started_at=started_at,
            execution_completed_at=completed_at,
            response_etag=response_etag,
            response_last_modified=response_last_modified,
        )


def free_source_gap(
    *,
    source_id: str,
    observed_at: datetime,
    reason_code: str,
    status: SourceCoverageStatus,
) -> R1SourceBatch:
    """Return one explicit terminal free-source result without fallback data."""

    adapter = FreeLiveAdapter(source_id)
    return terminal_gap_batch(
        source_id=adapter.source_id,
        capability=adapter.capability,
        runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        provenance=adapter.provenance,
        rights_snapshot_hash=FREE_SOURCE_RIGHTS_HASH,
        license_id=FREE_SOURCE_LICENSE_ID,
        observed_at=observed_at,
        status=status,
        reason_code=reason_code,
    )


def free_sources_for_capability(capability: str) -> tuple[FreeSourceDefinition, ...]:
    return tuple(item for item in FREE_SOURCE_MATRIX if item.capability == capability)


def _discovery_record_type(capability: str) -> str:
    try:
        return {
            "statutory.announcements": "statutory.announcement.discovery",
            "company_industry.news": "company.news.discovery",
            "macro.flashes": "macro.flash.discovery",
        }[capability]
    except KeyError as error:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R1_SOURCE_REQUEST_MISMATCH",
        ) from error


def _research_row_relevant(
    endpoint_name: str,
    row: Mapping[str, JsonValue],
) -> bool:
    if endpoint_name in {"anns_d", "income", "daily"}:
        if str(row.get("ts_code", "")).upper() != "603986.SH":
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_TARGET_LOCKED")
        return True
    if endpoint_name == "news":
        title = str(row.get("title") or "").lower()
        return any(term in title for term in ("兆易创新", "603986", "gigadevice"))
    return endpoint_name == "npr"


def _qinghong_row_relevant(
    endpoint_name: str,
    row: Mapping[str, JsonValue],
) -> bool:
    title = str(row.get("title") or "").lower()
    if endpoint_name in {"cninfo-latest", "cls-telegraph"}:
        return any(term in title for term in ("兆易创新", "603986", "gigadevice"))
    if endpoint_name == "jin10":
        return any(
            term in title
            for term in (
                "半导体",
                "芯片",
                "存储",
                "科技",
                "人工智能",
                "关税",
                "利率",
                "央行",
                "美联储",
            )
        )
    raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")


def _research_external_id(
    endpoint_name: str,
    row: Mapping[str, JsonValue],
    content_hash: str,
) -> str:
    candidates: dict[str, tuple[object, ...]] = {
        "anns_d": (row.get("url"), row.get("ann_date"), row.get("title")),
        "income": (row.get("end_date"), row.get("ann_date"), row.get("report_type")),
        "daily": (row.get("trade_date"),),
        "news": (row.get("datetime"), row.get("title")),
        "npr": (row.get("pcode"), row.get("url"), row.get("title")),
    }
    values = candidates.get(endpoint_name)
    if values is None:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")
    joined = "|".join(str(value) for value in values if value not in (None, ""))
    return (joined or content_hash)[:500]


def _qinghong_external_id(
    endpoint_name: str,
    row: Mapping[str, JsonValue],
    content_hash: str,
) -> str:
    if endpoint_name not in set(QINGHONG_ENDPOINTS.values()):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")
    if row.get("platform_id") != endpoint_name:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")
    candidate = row.get("item_id")
    return (str(candidate) if candidate not in (None, "") else content_hash)[:500]


def _research_structured_fields(
    endpoint_name: str,
    row: Mapping[str, JsonValue],
) -> tuple[dict[str, JsonValue], str, str | None, datetime]:
    if endpoint_name == "anns_d":
        _require_research_fields(row, ("ann_date", "ts_code", "title"))
        payload = _allowlisted_payload(
            row,
            ("ann_date", "ts_code", "name", "title", "rec_time"),
        )
        event_time = _parse_datetime(row.get("rec_time") or row["ann_date"])
        return (
            payload,
            str(row["title"])[:500],
            _safe_research_link(row.get("url"), require_cninfo_attachment=True),
            event_time,
        )
    if endpoint_name == "income":
        _require_research_fields(row, ("ts_code", "ann_date", "end_date"))
        payload = _allowlisted_payload(
            row,
            (
                "ts_code",
                "ann_date",
                "f_ann_date",
                "end_date",
                "report_type",
                "comp_type",
                "basic_eps",
                "diluted_eps",
                "total_revenue",
                "revenue",
                "operate_profit",
                "total_profit",
                "n_income",
                "n_income_attr_p",
            ),
        )
        title = f"兆易创新 {row['end_date']} 财务报表摘要"
        disclosure_date = row.get("f_ann_date") or row["ann_date"]
        return payload, title[:500], None, _parse_datetime(disclosure_date)
    if endpoint_name == "daily":
        _require_research_fields(row, ("ts_code", "trade_date", "close"))
        payload = _allowlisted_payload(
            row,
            (
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "change",
                "pct_chg",
                "vol",
                "amount",
            ),
        )
        title = f"兆易创新 {row['trade_date']} 日行情"
        return payload, title[:500], None, _parse_datetime(row["trade_date"])
    if endpoint_name == "news":
        _require_research_fields(row, ("datetime", "title"))
        payload = _allowlisted_payload(row, ("datetime", "title", "channels"))
        return payload, str(row["title"])[:500], None, _parse_datetime(row["datetime"])
    if endpoint_name == "npr":
        _require_research_fields(row, ("pubtime", "title"))
        payload = _allowlisted_payload(
            row,
            ("pubtime", "title", "pcode", "puborg", "ptype"),
        )
        return (
            payload,
            str(row["title"])[:500],
            _safe_research_link(row.get("url"), require_gov_cn=True),
            _parse_datetime(row["pubtime"]),
        )
    raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")


def _qinghong_structured_fields(
    endpoint_name: str,
    row: Mapping[str, JsonValue],
) -> tuple[dict[str, JsonValue], str, None, datetime]:
    if endpoint_name not in set(QINGHONG_ENDPOINTS.values()):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")
    _require_research_fields(row, ("platform_id", "item_id", "title", "published_at"))
    if row["platform_id"] != endpoint_name:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")
    payload = _allowlisted_payload(
        row,
        ("platform_id", "item_id", "title", "published_at"),
    )
    return payload, str(row["title"])[:500], None, _parse_datetime(row["published_at"])


def _require_research_fields(
    row: Mapping[str, JsonValue],
    required: tuple[str, ...],
) -> None:
    if any(row.get(key) in (None, "") for key in required):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_FREE_SOURCE_SCHEMA_INVALID")


def _allowlisted_payload(
    row: Mapping[str, JsonValue],
    fields: tuple[str, ...],
) -> dict[str, JsonValue]:
    return {key: row[key] for key in fields if row.get(key) not in (None, "")}


def _safe_research_link(
    value: JsonValue | None,
    *,
    require_gov_cn: bool = False,
    require_cninfo_attachment: bool = False,
) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or (require_gov_cn and not (hostname == "gov.cn" or hostname.endswith(".gov.cn")))
        or (
            require_cninfo_attachment
            and (
                hostname != "static.cninfo.com.cn"
                or parsed.netloc != "static.cninfo.com.cn"
                or parsed.query
                or parsed.fragment
                or not _is_canonical_cninfo_attachment_path(parsed.path)
            )
        )
    ):
        return None
    return value[:2_000]


def _is_canonical_cninfo_attachment_path(path: str) -> bool:
    if "%" in path or "\\" in path:
        return False
    segments = path.split("/")
    return (
        len(segments) >= 3
        and segments[:2] == ["", "finalpage"]
        and all(segment not in {"", ".", ".."} for segment in segments[2:])
        and segments[-1].casefold().endswith(".pdf")
    )


def _research_preview_arguments(
    adapter: FreeLiveAdapter,
    request: FetchRequest,
) -> dict[str, JsonValue]:
    assert request.until is not None
    start = _research_preview_start(request)
    start_local = start.astimezone(_SHANGHAI)
    end_local = request.until.astimezone(_SHANGHAI)
    if adapter.endpoint_name == "anns_d":
        return {
            "ts_code": "603986.SH",
            "start_date": start_local.strftime("%Y%m%d"),
            "end_date": end_local.strftime("%Y%m%d"),
        }
    if adapter.endpoint_name == "income":
        return {
            "ts_code": "603986.SH",
            "start_date": start_local.strftime("%Y%m%d"),
            "end_date": end_local.strftime("%Y%m%d"),
        }
    if adapter.endpoint_name == "daily":
        return {
            "ts_code": "603986.SH",
            "start_date": start_local.strftime("%Y%m%d"),
            "end_date": end_local.strftime("%Y%m%d"),
        }
    if adapter.endpoint_name == "news":
        news_start = max(start_local, end_local - timedelta(hours=6))
        return {
            "src": "eastmoney",
            "start_date": news_start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end_local.strftime("%Y-%m-%d %H:%M:%S"),
        }
    if adapter.endpoint_name == "npr":
        return {
            "ptype": "科技",
            "start_date": start_local.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end_local.strftime("%Y-%m-%d %H:%M:%S"),
        }
    raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")


def _qinghong_arguments(adapter: FreeLiveAdapter) -> dict[str, JsonValue]:
    if adapter.endpoint_name == "cninfo-latest":
        return {"stock": "603986"}
    if adapter.endpoint_name in {"cls-telegraph", "jin10"}:
        return {}
    raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")


def _qinghong_start(request: FetchRequest) -> datetime:
    assert request.until is not None
    retention_floor = request.until - timedelta(days=30)
    requested_start = max(retention_floor, request.since or retention_floor)
    if request.cursor is None:
        return max(retention_floor, requested_start - _QINGHONG_OVERLAP)
    _, _, prior_window_end = _decode_official_cursor(request.cursor)
    if prior_window_end > request.until:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_CURSOR_INVALID")
    return max(
        retention_floor,
        min(requested_start, prior_window_end) - _QINGHONG_OVERLAP,
    )


def _research_preview_start(request: FetchRequest) -> datetime:
    assert request.until is not None
    retention_floor = request.until - timedelta(days=30)
    start = max(retention_floor, request.since or retention_floor)
    if request.cursor is None:
        return start
    _, _, prior_window_end = _decode_official_cursor(request.cursor)
    if prior_window_end > request.until:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_CURSOR_INVALID")
    return max(retention_floor, min(start, prior_window_end - _RESEARCH_PREVIEW_OVERLAP))


def _parse_datetime(value: JsonValue) -> datetime:
    raw = str(value).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(raw, pattern).replace(tzinfo=_SHANGHAI)
        except ValueError:
            continue
        return parsed.astimezone(UTC)
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SHANGHAI)
    return parsed.astimezone(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_CLOCK_INVALID")
    return value.astimezone(UTC)


def _encode_official_cursor(
    *,
    window_end: datetime,
    etag: str | None,
    last_modified: str | None,
) -> str:
    return _canonical_json(
        {
            "etag": etag,
            "last_modified": last_modified,
            "version": 1,
            "window_end": window_end.isoformat(),
        }
    )


def _decode_official_cursor(cursor: str) -> tuple[str | None, str | None, datetime]:
    try:
        payload = json.loads(cursor)
    except json.JSONDecodeError as error:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_CURSOR_INVALID") from error
    if not isinstance(payload, dict) or set(payload) != {
        "etag",
        "last_modified",
        "version",
        "window_end",
    }:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_CURSOR_INVALID")
    etag = payload.get("etag")
    last_modified = payload.get("last_modified")
    raw_window_end = payload.get("window_end")
    if (
        payload.get("version") != 1
        or not isinstance(raw_window_end, str)
        or (etag is not None and not _safe_header_value(etag))
        or (last_modified is not None and not _safe_header_value(last_modified))
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_CURSOR_INVALID")
    try:
        window_end = datetime.fromisoformat(raw_window_end)
    except ValueError as error:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R1_SOURCE_CURSOR_INVALID",
        ) from error
    if window_end.tzinfo is None or window_end.utcoffset() is None:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_CURSOR_INVALID")
    return cast(str | None, etag), cast(str | None, last_modified), window_end


def _safe_header_value(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 500 and "\r" not in value and "\n" not in value


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = [
    "DISABLED_OFFICIAL_DOMAINS",
    "FREE_SOURCE_ADAPTER_VERSION",
    "FREE_SOURCE_BY_ID",
    "FREE_SOURCE_CATALOG",
    "FREE_SOURCE_CATALOG_BY_ID",
    "FREE_SOURCE_LICENSE_ID",
    "FREE_SOURCE_MATRIX",
    "FREE_SOURCE_RIGHTS_HASH",
    "FreeDevelopmentRightsPolicy",
    "FreeLiveAdapter",
    "FreeSourceDefinition",
    "RightsGatedFreeConnector",
    "active_free_source_definitions",
    "configured_discovery_sources",
    "configured_qinghong_sources",
    "configured_research_preview_sources",
    "free_source_gap",
    "free_sources_for_capability",
]
