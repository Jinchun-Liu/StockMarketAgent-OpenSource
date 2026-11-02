"""Deterministic interpretation of formal disclosure fields, never model output."""

import hashlib
import re
from datetime import UTC, datetime, time
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from stock_assistant.contracts.formal_disclosure import (
    DocumentKind,
    FormalDocumentIdentity,
    FormalDocumentLocation,
    OfficialDocumentIndexBinding,
    PublicationDateEvidence,
)
from stock_assistant.ports.ingestion import GovernedWebSourceError

# Frozen before candidate results; news, excerpts and IR paths are not formal titles.
_TITLES: Final[tuple[tuple[DocumentKind, str], ...]] = (
    ("annual", r"(?:20[0-9]{2}\s*年?\s*年度报告|20[0-9]{2}\s+Annual Report)"),
    ("interim", r"20[0-9]{2}\s*年?\s*(?:半年度报告|第[一二三四]季度报告)"),
    ("IR", r"(?:投资者关系活动记录表|业绩说明会材料)"),
    ("regulatory", r"(?:行政处罚决定书|监管措施决定书|监管函)"),
    ("announcement", r"关于[^\n]{1,180}的公告"),
)


def publication_date(value: str, *, chinese_source: bool) -> tuple[datetime, bool]:
    normalized = value.strip().replace("年", "-").replace("月", "-").replace("日", "")
    day_only = bool(re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", normalized))
    try:
        if day_only:
            if not chinese_source:
                raise ValueError("date has no source timezone")
            day = datetime.strptime(normalized, "%Y-%m-%d").date()
            parsed = datetime.combine(day, time.max, tzinfo=ZoneInfo("Asia/Shanghai"))
        else:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            if parsed.tzinfo is None and chinese_source:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            if parsed.tzinfo is None:
                raise ValueError("publication timezone missing")
    except ValueError as error:
        raise GovernedWebSourceError("WEB_PUBLICATION_TIME_UNKNOWN") from error
    return parsed.astimezone(UTC), day_only


def formal_identity(
    *,
    text: str,
    url: str,
    instrument_id: str,
    instrument_name: str,
    index: OfficialDocumentIndexBinding | None,
) -> FormalDocumentIdentity:
    code = instrument_id.split(":")[-1]
    code_match = re.search(
        r"(?:证券代码|股票代码|Stock Code)\s*[:\uFF1A]?\s*" + re.escape(code) + r"(?!\d)",
        text,
        re.I,
    )
    name_match = re.search(re.escape(instrument_name), text)
    if code_match is None or name_match is None:
        raise GovernedWebSourceError("WEB_FORMAL_IDENTITY_UNVERIFIED")
    titles = [
        (kind, match)
        for kind, pattern in _TITLES
        if (
            match := re.search(
                r"(?m)^[ \t]*(?:"
                + re.escape(instrument_name)
                + r"(?:股份有限公司)?[ \t]*)?("
                + pattern
                + r")[ \t]*$",
                text,
                re.I,
            )
        )
    ]
    if not titles:
        raise GovernedWebSourceError("WEB_FORMAL_DOCUMENT_UNVERIFIED")
    kind, title_match = min(titles, key=lambda item: item[1].start())
    tail = text[title_match.end() : title_match.end() + 10]
    if re.match(r"\s*(?:摘要|解读|新闻)", tail):
        raise GovernedWebSourceError("WEB_FORMAL_DOCUMENT_UNVERIFIED")
    native_match = re.search(r"(?:公告编号|文号|编号)\s*[:\uFF1A]?\s*([^\s\n]{3,100})", text)
    if kind in {"announcement", "regulatory"} and native_match is None:
        raise GovernedWebSourceError("WEB_FORMAL_DOCUMENT_UNVERIFIED")
    if (
        kind == "IR"
        and re.search(r"(?:活动时间|会议时间|记录编号)\s*[:\uFF1A]?\s*\S+", text) is None
    ):
        raise GovernedWebSourceError("WEB_FORMAL_DOCUMENT_UNVERIFIED")
    if index is not None and (
        index.identity.canonical_url != url
        or index.identity.instrument_id != instrument_id
        or index.identity.issuer_name != instrument_name
        or index.identity.document_kind != kind
    ):
        raise GovernedWebSourceError("WEB_FORMAL_INDEX_MISMATCH")
    anchors = []
    for match in (name_match, code_match):
        pages = re.findall(r"\[PDF page (\d+)\]", text[: match.start()])
        anchors.append(
            FormalDocumentLocation(
                field="normalized_original",
                start=match.start(),
                end=match.end(),
                page_number=int(pages[-1]) if pages else None,
            )
        )
    return FormalDocumentIdentity(
        document_kind=kind,
        issuer_name=instrument_name,
        instrument_id=instrument_id,
        title=title_match[0],
        native_document_id=index.identity.native_document_id
        if index
        else native_match[1]
        if native_match
        else hashlib.sha256(url.encode()).hexdigest(),
        canonical_url=url,
        binding_basis="official_index" if index else "document_fields",
        identity_anchors=anchors,
    )


def formal_publication(
    *,
    text: str,
    url: str,
    metadata: list[tuple[str, str]],
    index: OfficialDocumentIndexBinding | None,
    receipt_id: UUID,
    cutoff: datetime,
) -> tuple[datetime, list[PublicationDateEvidence]]:
    digest = hashlib.sha256(text.encode()).hexdigest()
    proofs = [index.publication] if index else []
    labels = re.finditer(
        r"(?m)^[ \t]*(?:发布日期|发布时间|公告日期|Publication Date)[ \t]*[:\uFF1A]?[ \t]*"
        r"(\d{4}[-年]\d{1,2}[-月]\d{1,2}日?(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?"
        r"(?:Z|[+-]\d{2}:\d{2})?)?)[ \t]*$",
        text,
        re.I,
    )
    for match in labels:
        stamp, day_only = publication_date(match[1], chinese_source=True)
        pages = re.findall(r"\[PDF page (\d+)\]", text[: match.start()])
        proofs.append(
            PublicationDateEvidence(
                kind="document_disclosure",
                raw_value=match[1],
                published_at=stamp,
                precision="day" if day_only else "instant",
                timezone="Asia/Shanghai" if day_only else "UTC",
                canonical_url=url,
                location=FormalDocumentLocation(
                    field="normalized_original",
                    start=match.start(),
                    end=match.end(),
                    page_number=int(pages[-1]) if pages else None,
                ),
                observation_sha256=digest,
                receipt_id=receipt_id,
            )
        )
        if len(proofs) > 8:
            raise GovernedWebSourceError("WEB_FORMAL_PROOF_CAPACITY")
    for raw, field in metadata:
        if len(raw) > 128 or len(field) > 256:
            raise GovernedWebSourceError("WEB_FORMAL_PROOF_CAPACITY")
        stamp, day_only = publication_date(raw, chinese_source=True)
        proofs.append(
            PublicationDateEvidence(
                kind="official_page_structured",
                raw_value=raw,
                published_at=stamp,
                precision="day" if day_only else "instant",
                timezone="Asia/Shanghai" if day_only else "UTC",
                canonical_url=url,
                location=FormalDocumentLocation(field=field),
                observation_sha256=digest,
                receipt_id=receipt_id,
            )
        )
        if len(proofs) > 8:
            raise GovernedWebSourceError("WEB_FORMAL_PROOF_CAPACITY")
    if not proofs:
        raise GovernedWebSourceError("WEB_PUBLICATION_TIME_UNKNOWN")
    days = {p.published_at.astimezone(ZoneInfo("Asia/Shanghai")).date() for p in proofs}
    exact = {p.published_at for p in proofs if p.precision == "instant"}
    if len(days) != 1 or len(exact) > 1:
        raise GovernedWebSourceError("WEB_PUBLICATION_TIME_CONFLICT")
    stamp = next(iter(exact)) if exact else max(p.published_at for p in proofs)
    if stamp > cutoff:
        raise GovernedWebSourceError("WEB_PUBLICATION_TIME_INVALID")
    return stamp, proofs
