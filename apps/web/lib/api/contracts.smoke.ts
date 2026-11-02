import assert from "node:assert/strict";

import publicV12Formats from "./generated/public-v12-formats.generated.cjs";
import {
  parseActiveTargetResponse,
  parseEventEvidenceResponse,
  parsePremarketBriefingResponse,
  R1_PROVENANCE_MEDIA_TYPE,
  R1_PROVENANCE_MEDIA_TYPE_V1_2,
  R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3,
} from "./contracts.ts";
import { safeExternalSourceUrl } from "../source-links.ts";
import { apiErrorCode } from "./errors.ts";
import type { components } from "./openapi.generated";

const now = "2026-08-01T00:00:00Z";
const id1 = "00000000-0000-4000-8000-000000000001";
const id2 = "00000000-0000-4000-8000-000000000002";
const id3 = "00000000-0000-4000-8000-000000000003";
const id4 = "00000000-0000-4000-8000-000000000004";
const hash = "a".repeat(64);
const dateTimeFormat = publicV12Formats["date-time"];
const uriFormat = publicV12Formats.uri;
for (const invalidDateTime of [
  "2026-02-30T00:00:00Z",
  "2025-02-29T00:00:00Z",
  "2026-08-04T24:00:00Z",
  "2026-08-04T02:60:00Z",
  "2026-08-04T02:00:61Z",
  "2026-08-04T02:00:00+24:00",
  "2026-08-04T02:00:00+08:60",
  "2026-08-04T02:00:00+0800",
]) {
  assert.equal(dateTimeFormat(invalidDateTime), false);
}
for (const validDateTime of [
  "2024-02-29T00:00:00Z",
  "2026-08-04T02:00:00Z",
  "2026-08-04T10:00:00+08:00",
  "2017-01-01T00:59:60+01:00",
]) {
  assert.equal(dateTimeFormat(validDateTime), true);
}
for (const invalidUri of [
  "https://%",
  "https://example.com/%",
  "https://example.com/%GG",
  "https://example.com/#a#b",
  "a:[]",
  "https://example.com/path[bad]",
  "https://example.com/?bad[query]",
  "https://example.com/#bad]",
  "https://[:::]/",
  "https://user@@example.com/",
  "https://example.com:abc/",
  "https://exa mple.com/source",
  "https://example.com/\u0001",
  "relative/path",
]) {
  assert.equal(uriFormat(invalidUri), false);
}
for (const validUri of [
  "https://example.com/%E4%B8%AD?source=official#item",
  "https://example.com/path?first?second=/ok#fragment?allowed",
  "https://[2001:db8::1]/source?first?second#item",
  "a://[v1.alpha:beta]/source",
  "urn:isbn:0451450523",
  "mailto:public@example.com",
]) {
  assert.equal(uriFormat(validUri), true);
}
const meta = { as_of: now, correlation_id: id1, request_id: id2, contract_version: "1.0" };
const instrument = {
  instrument_id: "XSHG:603986",
  symbol: "603986.SH",
  exchange: "SSE",
  market: "CN_A",
  asset_class: "equity",
  currency: "CNY",
};
const provenance: components["schemas"]["SourceProvenance"] = {
  schema_version: "1.1",
  provider: "fixture",
  access_channel: "demo_replay",
  adapter_name: "fixture",
  adapter_version: "1.0.0",
  endpoint_name: "replay",
  source_identifier: "fixture-1",
  runtime_mode: "demo_replay",
  release_tier: "dev_only",
  rights_status: "unverified",
};
const provenanceV12: components["schemas"]["SourceProvenanceV1_2"] = {
  schema_version: "1.2",
  provider: "fixture",
  access_channel: "demo_replay",
  adapter_name: "fixture",
  adapter_version: "1.2.0",
  endpoint_name: "replay",
  source_identifier: "fixture-1",
  runtime_mode: "public_static",
  release_tier: "dev_only",
  rights_status: "not_applicable",
  quality_tier: "demo_replay",
  evidence_role: "authoritative",
};
const progress = {
  source_id: "source",
  capability: "statutory_announcements",
  advance_state: "advanced",
  batch_audit_id: id3,
  business_watermark: now,
  data_mode: "synthetic_development_only",
};
const coverageProgress = {
  advance_state: progress.advance_state,
  batch_audit_id: progress.batch_audit_id,
  business_watermark: progress.business_watermark,
  data_mode: progress.data_mode,
};
const item = {
  change_type: "new",
  event_id: id4,
  event_revision: 1,
  evidence_ids: [id1],
  fact_summary: "summary",
  first_seen_at: now,
  headline: "headline",
  inference: null,
  published_at: now,
  source_roles: ["statutory"],
};
const sections = {
  statutory_announcements: { items: [item] },
  financial_changes: { items: [] },
  market_snapshot: { items: [] },
  company_industry_news: { items: [] },
  macro_flashes: { items: [] },
};
const coverage = {
  source_id: "source",
  capability: "statutory_announcements",
  source_role: "statutory",
  status: "ok",
  license_id: "fixture",
  observed_at: now,
  last_success_at: now,
  progress: coverageProgress,
};
const briefingV10 = {
  data: {
    schema_version: "1.0",
    as_of: now,
    generated_at: now,
    comparison_since: now,
    briefing_id: id3,
    disclaimer: "paper only",
    instrument,
    omitted_duplicate_count: 0,
    ruleset_version: "rules",
    sections,
    ingestion_progress: progress,
    source_coverage: [coverage],
  },
  meta,
};
const briefingV11 = {
  data: {
    ...briefingV10.data,
    schema_version: "1.1",
    runtime_mode: "demo_replay",
    release_tier: "dev_only",
    ingestion_progress: { ...progress, provenance },
    source_coverage: [{ ...coverage, provenance }],
  },
  meta,
};
const briefingV12 = {
  data: {
    ...briefingV10.data,
    schema_version: "1.2",
    runtime_mode: "public_static",
    release_tier: "dev_only",
    ingestion_progress: { ...progress, provenance: provenanceV12 },
    source_coverage: [{ ...coverage, provenance: provenanceV12 }],
  },
  meta,
};
const briefingV13 = {
  data: {
    ...briefingV12.data,
    schema_version: "1.3",
    instrument: {
      instrument_id: "XSHG:603986",
      symbol: "603986",
      exchange: "XSHG",
      market: "CN",
      asset_class: "equity",
      currency: "CNY",
    },
    attention_risk_index: {
      as_of: now,
      availability: "ready",
      available_category_count: 5,
      band: "tense",
      categories: [
        { category: "announcements", available: true, score: 75, contradiction_count: 0, correction_count: 3, new_count: 0, update_count: 0 },
        { category: "financial", available: true, score: 70, contradiction_count: 2, correction_count: 0, new_count: 0, update_count: 0 },
        { category: "market", available: true, score: 60, contradiction_count: 0, correction_count: 0, new_count: 0, update_count: 4 },
        { category: "company_industry", available: true, score: 60, contradiction_count: 0, correction_count: 0, new_count: 6, update_count: 0 },
        { category: "macro", available: true, score: 35, contradiction_count: 1, correction_count: 0, new_count: 0, update_count: 0 },
      ],
      completeness_percent: 100,
      disclaimer: "越高代表越需要核实信息，不代表上涨或下跌。",
      drivers: [{ code: "CHECK_CHANGES", text: "核对变化" }],
      formula_version: "attention-risk.v1",
      mitigators: [],
      score: 64,
    },
    behavior_pressure_summary: {
      as_of: now,
      availability: "ready",
      band: "crowded",
      completeness_percent: 100,
      disclaimer: "研究指数，不是下跌概率、买卖建议或目标价。",
      drivers: [{ code: "CHECK_STRUCTURE", text: "观察结构" }],
      facets: [
        { facet: "activity_divergence", availability: "ready", score: 82, reason_code: null },
        { facet: "extension_crowding", availability: "ready", score: 86, reason_code: null },
        { facet: "liquidity_fragility", availability: "ready", score: 62, reason_code: null },
        { facet: "volatility_tail", availability: "ready", score: 74, reason_code: null },
        { facet: "market_industry_divergence", availability: "ready", score: 78, reason_code: null },
      ],
      historical_analogs: {
        feature_set_version: "behavior-analog-14d.v1",
        ranges: [
          { metric: "return_1d", q1: "-0.01", median: "0", q3: "0.01" },
          { metric: "return_3d", q1: "-0.02", median: "0", q3: "0.02" },
          { metric: "industry_excess", q1: "-0.01", median: "0", q3: "0.01" },
          { metric: "mae", q1: "-0.03", median: "-0.02", q3: "-0.01" },
          { metric: "mfe", q1: "0.01", median: "0.02", q3: "0.03" },
        ],
        reason: null,
        reason_code: null,
        sample_count: 84,
        selection_as_of: now,
        selection_semantics_hash: hash,
        state: "ready",
      },
      mitigators: [],
      mode: "post_close",
      research_index: true,
      score: 78,
      score_mapping: {
        raw_total: 76,
        population_count: 1000,
        less_count: 775,
        equal_count: 10,
        mapped_score: 78,
        reference_mapping_version: "r1-ohlcv-midrank.v1",
        reference_population_hash: hash,
      },
      structure_dimensions: [
        { dimension: "trend_organization", availability: "ready", state: "有序", explanation: "趋势仍有序" },
        { dimension: "position_recovery", availability: "ready", state: "偏高", explanation: "位置偏高" },
        { dimension: "trading_participation", availability: "ready", state: "活跃", explanation: "成交活跃" },
        { dimension: "volatility_environment", availability: "ready", state: "扩张", explanation: "波动扩张" },
        { dimension: "intraday_acceptance", availability: "not_applicable", state: null, explanation: "盘后不评价" },
        { dimension: "relative_environment", availability: "ready", state: "偏离", explanation: "相对偏离" },
      ],
    },
    flash_interpretation: {
      label: "智能解读",
      origin: "deepseek_flash",
      status: "generated",
      summary: "先核对信息，再观察结构。",
      title: "研究解读",
    },
    pro_interpretation: null,
    plain_language_update: {
      checked_count: 5,
      last_success_at: now,
      no_change_count: 0,
      reasons: [],
      succeeded_count: 5,
      summary: "资料检查已完成。",
      unavailable_count: 0,
    },
  },
  meta,
};
const evidenceBase = {
  content_sha256: hash,
  evidence_id: id1,
  fetched_at: now,
  first_seen_at: now,
  license_id: "fixture",
  published_at: now,
  revision_known_at: now,
  rights_snapshot_hash: hash,
  source_id: "source",
  source_role: "statutory",
  source_url: "https://fixture.invalid/source",
  title: "title",
};
const timeline = {
  has_older: false,
  items: [{ audit_id: id2, change_type: "new", extraction_version: "extract", revision: 1, revision_known_at: now, ruleset_version: "rules" }],
};
const evidenceV10 = {
  data: {
    schema_version: "1.0",
    as_of: now,
    audit_id: id2,
    change_type: "new",
    cluster_id: id3,
    event_id: id4,
    event_type: "announcement",
    evidence: [evidenceBase],
    facts: [{ slot: "revenue", evidence_ids: [id1], value: { kind: "decimal", value: "1E+3" } }],
    extraction_version: "extract",
    instrument_ids: [instrument.instrument_id],
    revision: 1,
    revision_known_at: now,
    revision_timeline: timeline,
    ruleset_version: "rules",
    title: "title",
  },
  meta,
};
const evidenceV11 = {
  data: {
    ...evidenceV10.data,
    schema_version: "1.1",
    runtime_mode: "demo_replay",
    evidence: [{ ...evidenceBase, event_time: now, provenance }],
  },
  meta,
};
const evidenceV12 = {
  data: {
    ...evidenceV10.data,
    schema_version: "1.2",
    runtime_mode: "public_static",
    evidence: [{ ...evidenceBase, event_time: now, provenance: provenanceV12 }],
  },
  meta,
};

const activeTarget = {
  data: {
    schema_version: "1.0",
    found: true,
    as_of: now,
    active_target: { revision: 1, activated_at: now, instrument },
  },
  meta,
};

assert.ok(parseActiveTargetResponse(activeTarget));
assert.equal(parseActiveTargetResponse({ ...activeTarget, data: { ...activeTarget.data, found: false } }), null);
assert.ok(parseActiveTargetResponse({ ...activeTarget, data: { schema_version: "1.0", found: false, as_of: now, active_target: null } }));
assert.ok(parsePremarketBriefingResponse(briefingV10, "application/json"));
assert.ok(parsePremarketBriefingResponse(briefingV11, `${R1_PROVENANCE_MEDIA_TYPE}; charset=utf-8`));
assert.ok(parsePremarketBriefingResponse(briefingV12, `${R1_PROVENANCE_MEDIA_TYPE_V1_2}; charset=utf-8`));
assert.ok(parsePremarketBriefingResponse(briefingV13, R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3));
const tamperedAttention = structuredClone(briefingV13);
tamperedAttention.data.attention_risk_index.score = 63;
assert.equal(parsePremarketBriefingResponse(tamperedAttention, R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3), null);
const tamperedMapping = structuredClone(briefingV13);
tamperedMapping.data.behavior_pressure_summary.score_mapping.raw_total = 75;
assert.equal(parsePremarketBriefingResponse(tamperedMapping, R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3), null);
const missingMapping = structuredClone(briefingV13);
Reflect.deleteProperty(missingMapping.data.behavior_pressure_summary, "score_mapping");
assert.equal(parsePremarketBriefingResponse(missingMapping, R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3), null);
const futureResearchCutoff = structuredClone(briefingV13);
futureResearchCutoff.data.attention_risk_index.as_of = "2026-08-01T00:00:01Z";
assert.equal(parsePremarketBriefingResponse(futureResearchCutoff, R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3), null);
assert.equal(parsePremarketBriefingResponse(briefingV12, R1_PROVENANCE_MEDIA_TYPE), null);
assert.equal(parsePremarketBriefingResponse(briefingV11, "application/json"), null);
assert.equal(parsePremarketBriefingResponse(briefingV10, R1_PROVENANCE_MEDIA_TYPE), null);
assert.equal(parsePremarketBriefingResponse(briefingV10, null), null);
assert.ok(parsePremarketBriefingResponse(briefingV10));
assert.equal(parsePremarketBriefingResponse({ ...briefingV11, data: { ...briefingV11.data, sections: {} } }, R1_PROVENANCE_MEDIA_TYPE), null);
assert.ok(parseEventEvidenceResponse(evidenceV10, "application/json"));
assert.ok(parseEventEvidenceResponse(evidenceV11, R1_PROVENANCE_MEDIA_TYPE));
assert.ok(parseEventEvidenceResponse(evidenceV12, R1_PROVENANCE_MEDIA_TYPE_V1_2));
assert.equal(parsePremarketBriefingResponse({
  ...briefingV12,
  data: { ...briefingV12.data, as_of: "2026-02-30T00:00:00Z" },
}, R1_PROVENANCE_MEDIA_TYPE_V1_2), null);
assert.ok(parsePremarketBriefingResponse({
  ...briefingV12,
  data: { ...briefingV12.data, as_of: "2026-08-01T08:00:00+08:00" },
}, R1_PROVENANCE_MEDIA_TYPE_V1_2));
assert.equal(parseEventEvidenceResponse({
  ...evidenceV12,
  data: {
    ...evidenceV12.data,
    evidence: [{ ...evidenceV12.data.evidence[0], source_url: "https://%" }],
  },
}, R1_PROVENANCE_MEDIA_TYPE_V1_2), null);
assert.ok(parseEventEvidenceResponse({
  ...evidenceV12,
  data: {
    ...evidenceV12.data,
    evidence: [{ ...evidenceV12.data.evidence[0], source_url: "urn:isbn:0451450523" }],
  },
}, R1_PROVENANCE_MEDIA_TYPE_V1_2));
const longInstrumentId = `XSHG:${"A".repeat(512)}`;
assert.ok(parsePremarketBriefingResponse({
  ...briefingV12,
  data: {
    ...briefingV12.data,
    instrument: { ...briefingV12.data.instrument, instrument_id: longInstrumentId },
  },
}, R1_PROVENANCE_MEDIA_TYPE_V1_2));
assert.ok(parseEventEvidenceResponse({
  ...evidenceV12,
  data: { ...evidenceV12.data, instrument_ids: [longInstrumentId] },
}, R1_PROVENANCE_MEDIA_TYPE_V1_2));
assert.equal(parsePremarketBriefingResponse({
  ...briefingV12,
  data: {
    ...briefingV12.data,
    sections: {
      ...briefingV12.data.sections,
      statutory_announcements: {
        items: [{ ...item, unexpected_nested_field: true }],
      },
    },
  },
}, R1_PROVENANCE_MEDIA_TYPE_V1_2), null);

// Ordering and PIT invariants execute once in the Python snapshot publisher, not in the browser.
assert.ok(parsePremarketBriefingResponse({
  ...briefingV12,
  data: {
    ...briefingV12.data,
    comparison_since: "2026-08-01T00:00:02Z",
    as_of: "2026-08-01T00:00:01Z",
    generated_at: "2026-08-01T00:00:00Z",
    sections: {
      ...briefingV12.data.sections,
      statutory_announcements: {
        items: [{ ...item, first_seen_at: "2026-08-01T00:00:02Z" }],
      },
    },
  },
}, R1_PROVENANCE_MEDIA_TYPE_V1_2));
assert.ok(parseEventEvidenceResponse({
  ...evidenceV12,
  data: {
    ...evidenceV12.data,
    as_of: "2026-08-01T00:00:01Z",
    evidence: [{
      ...evidenceV12.data.evidence[0],
      revision_known_at: "2026-08-01T00:00:02Z",
    }],
  },
}, R1_PROVENANCE_MEDIA_TYPE_V1_2));
assert.equal(parseEventEvidenceResponse(evidenceV12, R1_PROVENANCE_MEDIA_TYPE), null);
assert.equal(parseEventEvidenceResponse(evidenceV10, R1_PROVENANCE_MEDIA_TYPE), null);
assert.equal(parseEventEvidenceResponse(evidenceV10, null), null);
assert.equal(parseEventEvidenceResponse({ ...evidenceV11, data: { ...evidenceV11.data, evidence: [{ ...evidenceBase, event_time: "bad" }] } }, R1_PROVENANCE_MEDIA_TYPE), null);
assert.equal(safeExternalSourceUrl("https://foo.invalid/source", null), null);
assert.equal(safeExternalSourceUrl("https://foo.invalid./source", { ...provenance, runtime_mode: "authorized", release_tier: "authorized", rights_status: "approved", access_channel: "authorized_api" }), null);
assert.equal(safeExternalSourceUrl("https://foo.example/source", provenance), null);
assert.equal(safeExternalSourceUrl("https://foo.example/source", { ...provenance, runtime_mode: "authorized", release_tier: "authorized", rights_status: "approved" }), "https://foo.example/source");
assert.equal(apiErrorCode(404), "not_found");
assert.equal(apiErrorCode(412), "provenance_unavailable");
assert.equal(apiErrorCode(503), "dependency_unavailable");
assert.equal(parsePremarketBriefingResponse(briefingV10, "text/plain"), null);
assert.equal(parsePremarketBriefingResponse({ ...briefingV10, meta: { ...meta, request_id: "req" } }, "application/json"), null);
assert.equal(parsePremarketBriefingResponse({ ...briefingV11, data: { ...briefingV11.data, runtime_mode: "unknown" } }, R1_PROVENANCE_MEDIA_TYPE), null);
assert.equal(parsePremarketBriefingResponse({ ...briefingV12, data: { ...briefingV12.data, ingestion_progress: { ...briefingV12.data.ingestion_progress, provenance: { ...provenanceV12, access_channel: "baostock_sdk" } } } }, R1_PROVENANCE_MEDIA_TYPE_V1_2), null);
assert.equal(parseEventEvidenceResponse({ ...evidenceV10, data: { ...evidenceV10.data, as_of: "2026-08-01T00:00:00" } }, "application/json"), null);
assert.ok(parseEventEvidenceResponse({
  ...evidenceV10,
  data: {
    ...evidenceV10.data,
    evidence: [{ ...evidenceBase, published_at: "2026-08-03T00:00:00Z", fetched_at: "2026-08-01T00:00:00Z" }],
  },
}, "application/json"));

console.log("contracts smoke passed");
