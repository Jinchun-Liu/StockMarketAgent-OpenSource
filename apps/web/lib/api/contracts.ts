import type { components } from "./openapi.generated";
import {
  validateEventEvidenceV1_2,
  validatePremarketBriefingV1_2,
} from "./generated/public-v12-validators.generated.cjs";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type HealthStatus = HealthResponse["status"];
export type ActiveTargetState = components["schemas"]["ActiveTargetState"];
export type ActiveTargetResponse = {
  data: ActiveTargetState;
  meta: components["schemas"]["ResponseMeta"];
};
export type PremarketBriefingViewV1_0 = components["schemas"]["PremarketBriefingView"];
export type PremarketBriefingViewV1_1 = components["schemas"]["PremarketBriefingViewV1_1"];
export type PremarketBriefingViewV1_2 = components["schemas"]["PremarketBriefingViewV1_2"];
export type PremarketBriefingViewV1_3 = components["schemas"]["PremarketBriefingViewV1_3"];
export type PremarketBriefingView =
  | PremarketBriefingViewV1_0
  | PremarketBriefingViewV1_1
  | PremarketBriefingViewV1_2
  | PremarketBriefingViewV1_3;
export type EventEvidenceViewV1_0 = components["schemas"]["EventEvidenceView"];
export type EventEvidenceViewV1_1 = components["schemas"]["EventEvidenceViewV1_1"];
export type EventEvidenceViewV1_2 = components["schemas"]["EventEvidenceViewV1_2"];
export type EventEvidenceView =
  | EventEvidenceViewV1_0
  | EventEvidenceViewV1_1
  | EventEvidenceViewV1_2;
export type SourceCoverage =
  | components["schemas"]["SourceCoverage"]
  | components["schemas"]["SourceCoverageV1_1"]
  | components["schemas"]["SourceCoverageV1_2"];
export type PremarketBriefingResponse = {
  data: PremarketBriefingView;
  meta: components["schemas"]["ResponseMeta"];
};
export type EventEvidenceResponse = {
  data: EventEvidenceView;
  meta: components["schemas"]["ResponseMeta"];
};
export type PublicSnapshotPointer = components["schemas"]["PublicSnapshotPointerV1"];
export type PublicSnapshotManifest = components["schemas"]["PublicSnapshotManifestV1_1"];
export type PublicSnapshotAsset = components["schemas"]["PublicSnapshotAsset"];
export type PublicRunStatusV1_0 = components["schemas"]["PublicRunStatusV1"];
export type PublicRunStatusV1_1 = components["schemas"]["PublicRunStatusV1_1"];
export type PublicRunStatus = PublicRunStatusV1_0 | PublicRunStatusV1_1;
export type PublicEvidenceIndex = components["schemas"]["PublicEvidenceIndexV1"];

export const R1_PROVENANCE_MEDIA_TYPE =
  "application/vnd.stockassistant.r1-source-provenance.v1+json" as const;
export const R1_PROVENANCE_MEDIA_TYPE_V1_2 =
  "application/vnd.stockassistant.r1-source-provenance.v1.2+json" as const;
export const R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3 =
  "application/vnd.stockassistant.r1-research-product.v1.3+json" as const;
type JsonMediaType =
  | "application/json"
  | typeof R1_PROVENANCE_MEDIA_TYPE
  | typeof R1_PROVENANCE_MEDIA_TYPE_V1_2
  | typeof R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256_RE = /^[a-f0-9]{64}$/;

function isNonEmpty(value: unknown, max = 512): value is string {
  return isString(value) && value.trim().length > 0 && value.length <= max;
}

function isUuid(value: unknown): value is string {
  return isString(value) && UUID_RE.test(value);
}

function isSha256(value: unknown): value is string {
  return isString(value) && SHA256_RE.test(value);
}

function isTimestamp(value: unknown): value is string {
  return (
    isString(value) &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

function isNullableString(value: unknown): value is string | null | undefined {
  return value === undefined || value === null || isString(value);
}

function isUniqueUuidArray(value: unknown, max = 100): value is string[] {
  return (
    Array.isArray(value) &&
    value.length <= max &&
    value.every(isUuid) &&
    new Set(value).size === value.length
  );
}

function isNonEmptyStringArray(value: unknown, max = 100): value is string[] {
  return Array.isArray(value) && value.length > 0 && value.length <= max && value.every((item) => isNonEmpty(item));
}

function isSlug(value: unknown, max = 100): value is string {
  return isString(value) && value.length >= 2 && value.length <= max && /^[a-z][a-z0-9_.-]*$/.test(value);
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isScore(value: unknown): value is number {
  return isInteger(value, 0) && value <= 100;
}

function isNullableScore(value: unknown): value is number | null {
  return value === null || isScore(value);
}

function isInteger(value: unknown, minimum = Number.MIN_SAFE_INTEGER): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum;
}

function isSourceRole(value: unknown): value is components["schemas"]["SourceRole"] {
  return (
    value === "statutory" ||
    value === "professional_market" ||
    value === "professional_information" ||
    value === "discovery"
  );
}

function isChangeType(value: unknown): value is components["schemas"]["BriefingChangeType"] {
  return value === "new" || value === "update" || value === "correction" || value === "contradiction";
}

function isDataMode(value: unknown): value is components["schemas"]["SourceDataMode"] {
  return value === "real" || value === "synthetic_development_only";
}

function isAdvanceState(value: unknown): value is components["schemas"]["SourceAdvanceState"] {
  return value === "advanced" || value === "not_advanced";
}

function isRuntimeMode(value: unknown): value is components["schemas"]["SourceRuntimeMode"] {
  return value === "demo_replay" || value === "dev_live" || value === "authorized";
}

function isReleaseTier(value: unknown): value is components["schemas"]["SourceReleaseTier"] {
  return value === "dev_only" || value === "authorized";
}

function isRightsStatus(value: unknown): value is components["schemas"]["SourceRightsStatus"] {
  return value === "unverified" || value === "approved" || value === "blocked";
}

function isAccessChannel(value: unknown): value is components["schemas"]["SourceAccessChannel"] {
  return (
    value === "demo_replay" ||
    value === "akshare" ||
    value === "official_http" ||
    value === "authorized_api"
  );
}

function isSourceProvenance(value: unknown): value is components["schemas"]["SourceProvenance"] {
  if (!isRecord(value)) {
    return false;
  }
  return (
    value.schema_version === "1.1" &&
    isString(value.provider) && /^[a-z][a-z0-9_.-]{1,99}$/.test(value.provider) &&
    isAccessChannel(value.access_channel) &&
    isString(value.adapter_name) && /^[a-z][a-z0-9_.-]{1,99}$/.test(value.adapter_name) &&
    isNonEmpty(value.adapter_version, 100) &&
    isString(value.endpoint_name) && /^[a-z][a-z0-9_.-]{1,199}$/.test(value.endpoint_name) &&
    isNonEmpty(value.source_identifier, 500) &&
    isRuntimeMode(value.runtime_mode) &&
    isReleaseTier(value.release_tier) &&
    isRightsStatus(value.rights_status)
  );
}

function isResponseMeta(value: unknown): value is components["schemas"]["ResponseMeta"] {
  return (
    isRecord(value) &&
    isTimestamp(value.as_of) &&
    isUuid(value.correlation_id) &&
    isUuid(value.request_id) &&
    (value.generated_at === undefined || isTimestamp(value.generated_at)) &&
    value.contract_version === "1.0"
  );
}

function isInstrument(value: unknown): value is components["schemas"]["InstrumentRef"] {
  return (
    isRecord(value) &&
    isNonEmpty(value.instrument_id, 100) &&
    isNonEmpty(value.symbol, 100) &&
    isNonEmpty(value.exchange, 50) &&
    isNonEmpty(value.market, 50) &&
    isNonEmpty(value.asset_class, 50) &&
    isNonEmpty(value.currency, 10)
  );
}

function isIngestionProgress(value: unknown): value is components["schemas"]["SourceIngestionProgress"] {
  return (
    isRecord(value) &&
    isAdvanceState(value.advance_state) &&
    isUuid(value.batch_audit_id) &&
    isDataMode(value.data_mode) &&
    isNullableString(value.business_watermark) &&
    (value.business_watermark === null || value.business_watermark === undefined || isTimestamp(value.business_watermark))
  );
}

function isBriefingIngestionProgress(
  value: unknown,
): value is components["schemas"]["BriefingIngestionProgress"] {
  return (
    isRecord(value) &&
    isIngestionProgress({
      advance_state: value.advance_state,
      batch_audit_id: value.batch_audit_id,
      business_watermark: value.business_watermark,
      data_mode: value.data_mode,
    }) &&
    isNonEmpty(value.source_id, 100) &&
    isSlug(value.capability, 100)
  );
}

function isCoverageStatus(value: unknown): value is components["schemas"]["SourceCoverageStatus"] {
  return (
    value === "ok" ||
    value === "delayed" ||
    value === "unavailable" ||
    value === "rights_blocked" ||
    value === "not_qualified" ||
    value === "timeliness_unverified"
  );
}

function isSourceCoverage(value: unknown): value is components["schemas"]["SourceCoverage"] {
  return (
    isRecord(value) &&
    isNonEmpty(value.source_id, 100) &&
    isSlug(value.capability, 100) &&
    isSourceRole(value.source_role) &&
    isCoverageStatus(value.status) &&
    isNonEmpty(value.license_id, 200) &&
    isTimestamp(value.observed_at) &&
    isNullableString(value.last_success_at) &&
    (value.last_success_at === null || value.last_success_at === undefined || isTimestamp(value.last_success_at)) &&
    isNullableString(value.gap_reason) &&
    isIngestionProgress(value.progress)
  );
}

type ProvenanceHolder = { provenance: components["schemas"]["SourceProvenance"] };
type EventTimeHolder = { event_time: string };

function hasProvenance(value: unknown): value is ProvenanceHolder {
  return isRecord(value) && isSourceProvenance(value.provenance);
}

function hasEventTime(value: unknown): value is EventTimeHolder {
  return isRecord(value) && isTimestamp(value.event_time);
}

function isSourceCoverageV11(value: unknown): value is components["schemas"]["SourceCoverageV1_1"] {
  return isSourceCoverage(value) && hasProvenance(value);
}

function isBriefingItem(value: unknown): value is components["schemas"]["PremarketBriefingItem"] {
  return (
    isRecord(value) &&
    isChangeType(value.change_type) &&
    isUuid(value.event_id) &&
    isInteger(value.event_revision, 1) &&
    isUniqueUuidArray(value.evidence_ids, 50) &&
    isNonEmpty(value.fact_summary, 2000) &&
    isTimestamp(value.first_seen_at) &&
    isNonEmpty(value.headline, 500) &&
    isNullableString(value.inference) &&
    isTimestamp(value.published_at) &&
    Array.isArray(value.source_roles) &&
    value.source_roles.length > 0 &&
    value.source_roles.length <= 4 &&
    value.source_roles.every(isSourceRole) &&
    new Set(value.source_roles).size === value.source_roles.length
  );
}

function isBriefingSection(value: unknown): value is components["schemas"]["PremarketBriefingSection"] {
  return isRecord(value) && (value.items === undefined || (Array.isArray(value.items) && value.items.every(isBriefingItem)));
}

function isBriefingSections(value: unknown): value is components["schemas"]["PremarketBriefingSections"] {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isBriefingSection(value.statutory_announcements) &&
    isBriefingSection(value.financial_changes) &&
    isBriefingSection(value.market_snapshot) &&
    isBriefingSection(value.company_industry_news) &&
    isBriefingSection(value.macro_flashes)
  );
}

function isFactSlot(value: unknown): value is components["schemas"]["EventFactSlot"] {
  if (!isRecord(value) || !isSlug(value.slot, 100) || !isUniqueUuidArray(value.evidence_ids, 50) || !isNullableString(value.period) || !isNullableString(value.unit) || !isRecord(value.value)) {
    return false;
  }
  return (
    (value.value.kind === "text" && isNonEmpty(value.value.value, 2000)) ||
    (value.value.kind === "decimal" && /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$/.test(String(value.value.value)))
  );
}

function isFactValue(value: unknown): boolean {
  return isRecord(value) &&
    ((value.kind === "text" && isNonEmpty(value.value, 2000)) ||
      (value.kind === "decimal" && typeof value.value === "string" && /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$/.test(value.value)));
}

function isCorrection(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  if (!isRecord(value) || (value.kind !== "replacement" && value.kind !== "retraction") ||
      !isSlug(value.slot, 100) || !isUniqueUuidArray(value.previous_evidence_ids, 50) ||
      !isUniqueUuidArray(value.current_evidence_ids, 50) ||
      !isFactValue(value.previous_value)) return false;
  if (value.kind === "replacement") return isFactValue(value.current_value);
  return value.current_value === null || value.current_value === undefined;
}

function isRevisionTimelineItem(value: unknown): value is components["schemas"]["EventRevisionTimelineItem"] {
  return (
    isRecord(value) &&
    isUuid(value.audit_id) &&
    isChangeType(value.change_type) &&
    isNonEmpty(value.extraction_version, 100) &&
    isInteger(value.revision, 1) &&
    isTimestamp(value.revision_known_at) &&
    isNonEmpty(value.ruleset_version, 100) &&
    (value.supersedes_revision === undefined || value.supersedes_revision === null ||
      isInteger(value.supersedes_revision, 1))
  );
}

function isRevisionTimeline(value: unknown): value is components["schemas"]["EventRevisionTimeline"] {
  return (
    isRecord(value) &&
    typeof value.has_older === "boolean" &&
    (value.before_revision === undefined || value.before_revision === null || isNumber(value.before_revision)) &&
    (value.next_before_revision === undefined || value.next_before_revision === null || isNumber(value.next_before_revision)) &&
    (value.items === undefined ||
      (Array.isArray(value.items) && value.items.length <= 50 && value.items.every(isRevisionTimelineItem) &&
        value.items.every((item, index, items) => index === 0 || item.revision < (items[index - 1]?.revision ?? Number.MAX_SAFE_INTEGER))))
  );
}

function isEvidenceSource(value: unknown): value is components["schemas"]["EventEvidenceSource"] {
  if (!isRecord(value) || !isSha256(value.content_sha256) || !isUuid(value.evidence_id) ||
      !isTimestamp(value.fetched_at) || !isTimestamp(value.first_seen_at) ||
      !isNonEmpty(value.license_id, 200) || !isTimestamp(value.published_at) ||
      !isTimestamp(value.revision_known_at) || !isSha256(value.rights_snapshot_hash) ||
      !isNonEmpty(value.source_id, 100) || !isSourceRole(value.source_role) ||
      !isNonEmpty(value.source_url, 2083) || !isNonEmpty(value.title, 1000)) {
    return false;
  }
  try {
    new URL(value.source_url);
  } catch {
    return false;
  }
  return isCorrectionRelation(value.correction_relation);
}

function isCorrectionRelation(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  return isRecord(value) &&
    (value.kind === "replacement" || value.kind === "retraction") &&
    isUuid(value.prior_evidence_id);
}

function isEvidenceSourceV11(value: unknown): value is components["schemas"]["EventEvidenceSourceV1_1"] {
  return (
    isEvidenceSource(value) &&
    hasEventTime(value) &&
    hasProvenance(value)
  );
}

function isV12Briefing(value: Record<string, unknown>): value is PremarketBriefingViewV1_2 {
  return validatePremarketBriefingV1_2(value);
}

const ATTENTION_CATEGORIES = new Set(["announcements", "financial", "market", "company_industry", "macro"]);
const ATTENTION_WEIGHTS: Readonly<Record<string, number>> = {
  announcements: 25,
  financial: 25,
  market: 20,
  company_industry: 20,
  macro: 10,
};
const PRESSURE_FACETS = new Set([
  "activity_divergence", "extension_crowding", "liquidity_fragility", "volatility_tail", "market_industry_divergence",
]);
const STRUCTURE_DIMENSIONS = new Set([
  "trend_organization", "position_recovery", "trading_participation",
  "volatility_environment", "intraday_acceptance", "relative_environment",
]);
const HISTORICAL_METRICS = new Set(["return_1d", "return_3d", "industry_excess", "mae", "mfe"]);
const EXPLANATION_CODE_RE = /^[A-Z][A-Z0-9_]{1,63}$/;
const DECIMAL_RE = /^(-?)(0|[1-9][0-9]*)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?$/;
const MAPPING_VERSION_RE = /^[a-z][a-z0-9_.-]{2,99}$/;

type ComparableDecimal = {
  sign: -1 | 0 | 1;
  digits: string;
  magnitude: bigint;
};

function parseComparableDecimal(value: unknown): ComparableDecimal | null {
  if (!isString(value)) return null;
  const match = DECIMAL_RE.exec(value);
  if (!match) return null;
  const digits = `${match[2] ?? ""}${match[3] ?? ""}`.replace(/^0+/, "");
  if (!digits) return { sign: 0, digits: "0", magnitude: 0n };
  const exponent = BigInt(match[4] ?? "0") - BigInt((match[3] ?? "").length);
  return {
    sign: match[1] === "-" ? -1 : 1,
    digits,
    magnitude: BigInt(digits.length) + exponent,
  };
}

function compareComparableDecimals(left: ComparableDecimal, right: ComparableDecimal): number {
  if (left.sign !== right.sign) return left.sign < right.sign ? -1 : 1;
  if (left.sign === 0) return 0;
  const direction = left.sign;
  if (left.magnitude !== right.magnitude) {
    return (left.magnitude < right.magnitude ? -1 : 1) * direction;
  }
  const length = Math.max(left.digits.length, right.digits.length);
  const leftDigits = left.digits.padEnd(length, "0");
  const rightDigits = right.digits.padEnd(length, "0");
  if (leftDigits === rightDigits) return 0;
  return (leftDigits < rightDigits ? -1 : 1) * direction;
}

function timestampNotAfter(left: string, right: string): boolean {
  return Date.parse(left) <= Date.parse(right);
}

function attentionBand(score: number): string {
  if (score <= 19) return "calm";
  if (score <= 39) return "notice";
  if (score <= 59) return "warming";
  if (score <= 79) return "tense";
  return "high_alert";
}

function pressureBand(score: number): string {
  if (score <= 39) return "stable";
  if (score <= 59) return "warming";
  if (score <= 74) return "attention";
  if (score <= 89) return "crowded";
  return "fragile";
}

function isIndexAvailability(value: unknown): boolean {
  return value === "ready" || value === "degraded" || value === "abstained";
}

function hasExpectedNames(values: unknown[], field: string, expected: ReadonlySet<string>): boolean {
  const names = values.flatMap((item) => isRecord(item) && isString(item[field]) ? [item[field]] : []);
  return names.length === expected.size && new Set(names).size === expected.size && names.every((name) => expected.has(name));
}

function isExplanationList(value: unknown, maximum: number): boolean {
  return value === undefined || (Array.isArray(value) && value.length <= maximum && value.every((item) =>
    isRecord(item) && isString(item.code) && EXPLANATION_CODE_RE.test(item.code) && isNonEmpty(item.text, 240)
  ));
}

function isAttentionIndex(value: unknown): boolean {
  if (!isRecord(value) || !isTimestamp(value.as_of) || !isIndexAvailability(value.availability) ||
      !isInteger(value.available_category_count, 0) || value.available_category_count > 5 ||
      !isScore(value.completeness_percent) || value.formula_version !== "attention-risk.v1" ||
      !isNonEmpty(value.disclaimer, 1000) || !isNullableScore(value.score) ||
      !Array.isArray(value.categories) || !hasExpectedNames(value.categories, "category", ATTENTION_CATEGORIES) ||
      !isExplanationList(value.drivers, 3) || !isExplanationList(value.mitigators, 2)) return false;
  const categories = value.categories.filter(isRecord);
  const categoriesValid = categories.every((item) => {
    if (typeof item.available !== "boolean" || !isNullableScore(item.score) ||
        !isInteger(item.contradiction_count, 0) || !isInteger(item.correction_count, 0) ||
        !isInteger(item.new_count, 0) || !isInteger(item.update_count, 0) ||
        item.available !== (item.score !== null)) return false;
    const expected = Math.min(100, 10 * item.new_count + 15 * item.update_count +
      25 * item.correction_count + 35 * item.contradiction_count);
    return !item.available || item.score === expected;
  });
  if (!categoriesValid) return false;
  const usable = categories.filter((item) => item.available === true).length;
  if (usable !== value.available_category_count || value.completeness_percent !== usable * 20) return false;
  if ((value.score === null) !== (value.band === null)) return false;
  if (usable < 3 && value.score !== null) return false;
  if (value.availability === "abstained") {
    if (value.score !== null) return false;
  } else if (value.score === null) {
    return false;
  }
  if (value.score !== null) {
    if (value.band !== attentionBand(value.score)) return false;
    let weightedTotal = 0;
    let weightTotal = 0;
    for (const item of categories) {
      if (!isScore(item.score) || !isString(item.category)) continue;
      const weight = ATTENTION_WEIGHTS[item.category];
      if (weight === undefined) return false;
      weightedTotal += item.score * weight;
      weightTotal += weight;
    }
    if (Math.floor(weightedTotal / weightTotal + 0.5) !== value.score) return false;
  }
  return true;
}

function isHistoricalAnalogs(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  if (!isRecord(value) || value.feature_set_version !== "behavior-analog-14d.v1" ||
      !isInteger(value.sample_count, 0) || value.sample_count > 200 || !isTimestamp(value.selection_as_of) ||
      !isSha256(value.selection_semantics_hash) || !isNullableString(value.reason) ||
      (value.reason !== undefined && value.reason !== null && !isNonEmpty(value.reason, 240)) ||
      !isNullableString(value.reason_code) ||
      (value.reason_code !== undefined && value.reason_code !== null &&
        (!isString(value.reason_code) || !EXPLANATION_CODE_RE.test(value.reason_code))) ||
      !["pending", "ready", "insufficient", "maturity_pending", "incomparable", "failed"].includes(String(value.state))) return false;
  const ranges = value.ranges === undefined ? [] : value.ranges;
  if (!Array.isArray(ranges) || ranges.length > 5 || !hasExpectedNames(ranges, "metric", new Set(ranges.map((range) =>
    isRecord(range) && isString(range.metric) ? range.metric : ""
  )))) return false;
  const rangesValid = ranges.every((range) => {
    if (!isRecord(range) || !isString(range.metric) || !HISTORICAL_METRICS.has(range.metric)) return false;
    const q1 = parseComparableDecimal(range.q1);
    const median = parseComparableDecimal(range.median);
    const q3 = parseComparableDecimal(range.q3);
    return q1 !== null && median !== null && q3 !== null &&
      compareComparableDecimals(q1, median) <= 0 && compareComparableDecimals(median, q3) <= 0;
  });
  if (!rangesValid) return false;
  if (value.state === "ready") {
    return value.sample_count >= 50 && ranges.length === 5 && hasExpectedNames(ranges, "metric", HISTORICAL_METRICS);
  }
  return ranges.length === 0 && isNonEmpty(value.reason, 240) &&
    isString(value.reason_code) && EXPLANATION_CODE_RE.test(value.reason_code);
}

function isScoreMapping(value: unknown): boolean {
  if (!isRecord(value) || !isScore(value.raw_total) || !isInteger(value.population_count, 1000) ||
      value.population_count > 10_000 || !isInteger(value.less_count, 0) ||
      !isInteger(value.equal_count, 1) || !isScore(value.mapped_score) ||
      !isString(value.reference_mapping_version) || !MAPPING_VERSION_RE.test(value.reference_mapping_version) ||
      !isSha256(value.reference_population_hash) ||
      value.less_count + value.equal_count > value.population_count) return false;
  const numerator = 100 * (2 * value.less_count + value.equal_count);
  const denominator = 2 * value.population_count;
  return Math.floor((numerator + value.population_count) / denominator) === value.mapped_score;
}

function isBehaviorIndex(value: unknown): boolean {
  if (!isRecord(value) || !isTimestamp(value.as_of) || !isIndexAvailability(value.availability) ||
      !isScore(value.completeness_percent) || !isNonEmpty(value.disclaimer, 1000) ||
      value.research_index !== true || !isNullableScore(value.score) ||
      (value.mode !== "post_close" && value.mode !== "intraday_10m") ||
      !Array.isArray(value.facets) || !hasExpectedNames(value.facets, "facet", PRESSURE_FACETS) ||
      !Array.isArray(value.structure_dimensions) || !hasExpectedNames(value.structure_dimensions, "dimension", STRUCTURE_DIMENSIONS) ||
      !isExplanationList(value.drivers, 3) || !isExplanationList(value.mitigators, 2) ||
      !isHistoricalAnalogs(value.historical_analogs)) return false;
  const facets = value.facets.filter(isRecord);
  const facetsValid = facets.every((item) => {
    if (!isIndexAvailability(item.availability) || !isNullableScore(item.score) || !isNullableString(item.reason_code) ||
        (item.reason_code !== undefined && item.reason_code !== null &&
          (!isString(item.reason_code) || !EXPLANATION_CODE_RE.test(item.reason_code)))) return false;
    if (item.availability === "abstained") return item.score === null && isString(item.reason_code);
    return item.score !== null;
  });
  const dimensionsValid = value.structure_dimensions.every((item) => {
    if (!isRecord(item) || !["ready", "degraded", "abstained", "not_applicable"].includes(String(item.availability)) ||
        !isNonEmpty(item.explanation, 240) || !isNullableString(item.state)) return false;
    const stateRequired = item.availability === "ready" || item.availability === "degraded";
    return stateRequired === (item.state !== null && item.state !== undefined && isNonEmpty(item.state, 80));
  });
  if (!facetsValid || !dimensionsValid) return false;
  const availableFacets = facets.filter((item) => item.score !== null).length;
  if (value.completeness_percent !== availableFacets * 20 || (value.score === null) !== (value.band === null)) return false;
  const scoreMapping = value.score_mapping === undefined ? null : value.score_mapping;
  if (scoreMapping !== null && !isScoreMapping(scoreMapping)) return false;
  if (value.availability === "abstained") {
    if (value.score !== null || scoreMapping !== null) return false;
  } else if (value.score === null || !isRecord(scoreMapping)) {
    return false;
  }
  if (value.score !== null && isRecord(scoreMapping)) {
    if (value.band !== pressureBand(value.score) || facets.some((item) => item.score === null)) return false;
    const rawTotal = Math.floor(facets.reduce((total, item) => total + (isScore(item.score) ? item.score : 0), 0) / 5 + 0.5);
    if (scoreMapping.raw_total !== rawTotal || scoreMapping.mapped_score !== value.score) return false;
  }
  const hasAnalogs = value.historical_analogs !== undefined && value.historical_analogs !== null;
  if (value.mode === "intraday_10m" && hasAnalogs) return false;
  if (hasAnalogs && isRecord(value.historical_analogs) &&
      (!isString(value.historical_analogs.selection_as_of) ||
        !timestampNotAfter(value.historical_analogs.selection_as_of, value.as_of))) return false;
  return true;
}

function isInterpretation(value: unknown): boolean {
  if (!isRecord(value) || value.label !== "智能解读" ||
      !["rules", "deepseek_flash", "deepseek_pro"].includes(String(value.origin)) ||
      !["generated", "fallback"].includes(String(value.status)) ||
      !isNonEmpty(value.summary, 800) || !isNonEmpty(value.title, 80)) return false;
  const pointsField = ["key", "points"].join("_");
  const points = value[pointsField];
  if (points !== undefined && (!Array.isArray(points) || points.length > 5 || !points.every(isString))) return false;
  return (value.origin === "rules" && value.status === "fallback") ||
    (value.origin !== "rules" && value.status === "generated");
}

function isPlainLanguageUpdate(value: unknown): boolean {
  return isRecord(value) && isInteger(value.checked_count, 0) && isInteger(value.no_change_count, 0) &&
    isInteger(value.succeeded_count, 0) && isInteger(value.unavailable_count, 0) &&
    isNullableString(value.last_success_at) &&
    (value.last_success_at === null || value.last_success_at === undefined || isTimestamp(value.last_success_at)) &&
    isNonEmpty(value.summary, 500) && isExplanationList(value.reasons, 5) &&
    value.succeeded_count + value.no_change_count + value.unavailable_count <= value.checked_count;
}

function isV13Briefing(value: Record<string, unknown>): value is PremarketBriefingViewV1_3 {
  const legacyProjection: Record<string, unknown> = { ...value, schema_version: "1.2" };
  delete legacyProjection.attention_risk_index;
  delete legacyProjection.behavior_pressure_summary;
  delete legacyProjection.flash_interpretation;
  delete legacyProjection.plain_language_update;
  delete legacyProjection.pro_interpretation;
  if (!validatePremarketBriefingV1_2(legacyProjection) || value.schema_version !== "1.3" ||
      !isRecord(value.instrument) || value.instrument.instrument_id !== "XSHG:603986" ||
      value.instrument.symbol !== "603986" || value.instrument.exchange !== "XSHG" ||
      value.instrument.market !== "CN" || value.instrument.asset_class !== "equity" ||
      value.instrument.currency !== "CNY" || !isTimestamp(value.as_of) ||
      !isAttentionIndex(value.attention_risk_index) || !isBehaviorIndex(value.behavior_pressure_summary) ||
      !isInterpretation(value.flash_interpretation) || !isPlainLanguageUpdate(value.plain_language_update) ||
      (value.pro_interpretation !== undefined && value.pro_interpretation !== null &&
        !isInterpretation(value.pro_interpretation))) return false;
  const attention = value.attention_risk_index;
  const behavior = value.behavior_pressure_summary;
  const flash = value.flash_interpretation;
  if (!isRecord(attention) || !isString(attention.as_of) || !timestampNotAfter(attention.as_of, value.as_of) ||
      !isRecord(behavior) || !isString(behavior.as_of) || !timestampNotAfter(behavior.as_of, value.as_of) ||
      !isRecord(flash) || flash.origin === "deepseek_pro") return false;
  if (value.pro_interpretation !== undefined && value.pro_interpretation !== null &&
      (!isRecord(value.pro_interpretation) || value.pro_interpretation.origin !== "deepseek_pro")) return false;
  return true;
}

function isV11Briefing(value: Record<string, unknown>): value is PremarketBriefingViewV1_1 {
  const progress = value.ingestion_progress;
  const coverage = value.source_coverage;
  return (
    value.schema_version === "1.1" &&
    isTimestamp(value.as_of) &&
    isTimestamp(value.generated_at) &&
    isTimestamp(value.comparison_since) &&
    (value.briefing_id === undefined || isUuid(value.briefing_id)) &&
    isNonEmpty(value.disclaimer, 1000) &&
    isInteger(value.omitted_duplicate_count, 0) &&
    isNonEmpty(value.ruleset_version, 100) &&
    isInstrument(value.instrument) &&
    isBriefingSections(value.sections) &&
    isRuntimeMode(value.runtime_mode) &&
    isReleaseTier(value.release_tier) &&
    isBriefingIngestionProgress(progress) &&
    hasProvenance(progress) &&
    (coverage === undefined ||
      (Array.isArray(coverage) &&
        coverage.every(isSourceCoverageV11)))
  );
}

function isV10Briefing(value: Record<string, unknown>): value is PremarketBriefingViewV1_0 {
  const coverage = value.source_coverage;
  return (
    value.schema_version === "1.0" &&
    isTimestamp(value.as_of) &&
    isTimestamp(value.generated_at) &&
    isTimestamp(value.comparison_since) &&
    (value.briefing_id === undefined || isUuid(value.briefing_id)) &&
    isNonEmpty(value.disclaimer, 1000) &&
    isInteger(value.omitted_duplicate_count, 0) &&
    isNonEmpty(value.ruleset_version, 100) &&
    isInstrument(value.instrument) &&
    isBriefingSections(value.sections) &&
    isBriefingIngestionProgress(value.ingestion_progress) &&
    (coverage === undefined || (Array.isArray(coverage) && coverage.every(isSourceCoverage)))
  );
}

function isV11Evidence(value: Record<string, unknown>): value is EventEvidenceViewV1_1 {
  const evidence = value.evidence;
  return (
    value.schema_version === "1.1" &&
    isTimestamp(value.as_of) &&
    isTimestamp(value.revision_known_at) &&
    isUuid(value.event_id) &&
    isUuid(value.audit_id) &&
    isChangeType(value.change_type) &&
    isUuid(value.cluster_id) &&
    isSlug(value.event_type, 100) &&
    isNonEmpty(value.extraction_version, 100) &&
    isNonEmptyStringArray(value.instrument_ids, 100) &&
    isInteger(value.revision, 1) &&
    isRevisionTimeline(value.revision_timeline) &&
    isNonEmpty(value.ruleset_version, 100) &&
    isNonEmpty(value.title, 1000) &&
    isCorrection(value.correction) &&
    isRuntimeMode(value.runtime_mode) &&
    Array.isArray(evidence) &&
    evidence.every(isEvidenceSourceV11) &&
    (value.facts === undefined || (Array.isArray(value.facts) && value.facts.every(isFactSlot)))
  );
}

function isV12Evidence(value: Record<string, unknown>): value is EventEvidenceViewV1_2 {
  return validateEventEvidenceV1_2(value);
}

function isV10Evidence(value: Record<string, unknown>): value is EventEvidenceViewV1_0 {
  return (
    value.schema_version === "1.0" &&
    isTimestamp(value.as_of) &&
    isTimestamp(value.revision_known_at) &&
    isUuid(value.event_id) &&
    isUuid(value.audit_id) &&
    isChangeType(value.change_type) &&
    isUuid(value.cluster_id) &&
    isSlug(value.event_type, 100) &&
    isNonEmpty(value.extraction_version, 100) &&
    isNonEmptyStringArray(value.instrument_ids, 100) &&
    isInteger(value.revision, 1) &&
    isRevisionTimeline(value.revision_timeline) &&
    isNonEmpty(value.ruleset_version, 100) &&
    isNonEmpty(value.title, 1000) &&
    isCorrection(value.correction) &&
    Array.isArray(value.evidence) &&
    value.evidence.every(isEvidenceSource) &&
    (value.facts === undefined || (Array.isArray(value.facts) && value.facts.every(isFactSlot)))
  );
}

function mediaTypeOf(value: string | null | undefined): JsonMediaType | null {
  if (value === undefined) return "application/json";
  if (value === null) return null;
  const mediaType = value?.split(";", 1)[0]?.trim().toLowerCase();
  if (mediaType === R1_PROVENANCE_MEDIA_TYPE_V1_2) return R1_PROVENANCE_MEDIA_TYPE_V1_2;
  if (mediaType === R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3) return R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3;
  if (mediaType === R1_PROVENANCE_MEDIA_TYPE) return R1_PROVENANCE_MEDIA_TYPE;
  if (mediaType === "application/json") return "application/json";
  return null;
}

export function parseHealthResponse(value: unknown): HealthResponse | null {
  if (!isRecord(value)) {
    return null;
  }
  const { status, service, version, timestamp } = value;
  if (
    (status !== "ok" && status !== "degraded") ||
    typeof service !== "string" ||
    typeof version !== "string" ||
    typeof timestamp !== "string"
  ) {
    return null;
  }
  return { status, service, version, timestamp };
}

export function parseActiveTargetResponse(value: unknown): ActiveTargetResponse | null {
  if (!isRecord(value) || !isRecord(value.data) || !isResponseMeta(value.meta)) {
    return null;
  }
  const data = value.data;
  if (
    data.schema_version !== "1.0" ||
    typeof data.found !== "boolean" ||
    !isTimestamp(data.as_of)
  ) {
    return null;
  }
  const activeTarget = data.active_target;
  if (!data.found) {
    return activeTarget === null || activeTarget === undefined
      ? {
          data: { schema_version: "1.0", found: false, as_of: data.as_of, active_target: null },
          meta: value.meta,
        }
      : null;
  }
  if (
    !isRecord(activeTarget) ||
    !isTimestamp(activeTarget.activated_at) ||
    !isInteger(activeTarget.revision, 1) ||
    !isInstrument(activeTarget.instrument)
  ) {
    return null;
  }
  return {
    data: {
      schema_version: "1.0",
      found: true,
      as_of: data.as_of,
      active_target: {
        revision: activeTarget.revision,
        activated_at: activeTarget.activated_at,
        instrument: activeTarget.instrument,
      },
    },
    meta: value.meta,
  };
}

export function parsePremarketBriefingResponse(
  value: unknown,
  responseMediaType?: string | null,
): PremarketBriefingResponse | null {
  if (!isRecord(value) || !isRecord(value.data) || !isResponseMeta(value.meta)) {
    return null;
  }
  const data = value.data;
  if (
    !isString(data.as_of) ||
    !isString(data.generated_at) ||
    !isRecord(data.instrument) ||
    !isRecord(data.sections) ||
    !isRecord(data.ingestion_progress)
  ) {
    return null;
  }
  const expected = mediaTypeOf(responseMediaType);
  if (expected === null) return null;
  if (expected === R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3) {
    return isV13Briefing(data) ? { data, meta: value.meta } : null;
  }
  if (expected === R1_PROVENANCE_MEDIA_TYPE_V1_2) {
    return isV12Briefing(data) ? { data, meta: value.meta } : null;
  }
  if (expected === R1_PROVENANCE_MEDIA_TYPE) {
    return isV11Briefing(data) ? { data, meta: value.meta } : null;
  }
  return isV10Briefing(data) ? { data, meta: value.meta } : null;
}

export function parsePremarketBriefingViewV1_2(
  value: unknown,
): PremarketBriefingViewV1_2 | null {
  return isRecord(value) && isV12Briefing(value) ? value : null;
}

export function parsePremarketBriefingViewV1_2OrV1_3(
  value: unknown,
): PremarketBriefingViewV1_2 | PremarketBriefingViewV1_3 | null {
  if (!isRecord(value)) return null;
  if (value.schema_version === "1.3") return isV13Briefing(value) ? value : null;
  return isV12Briefing(value) ? value : null;
}

export function parseEventEvidenceResponse(
  value: unknown,
  responseMediaType?: string | null,
): EventEvidenceResponse | null {
  if (!isRecord(value) || !isRecord(value.data) || !isResponseMeta(value.meta)) {
    return null;
  }
  const data = value.data;
  if (
    !isString(data.event_id) ||
    !isString(data.as_of) ||
    !isRecord(data.revision_timeline)
  ) {
    return null;
  }
  const expected = mediaTypeOf(responseMediaType);
  if (expected === null) return null;
  if (expected === R1_PROVENANCE_MEDIA_TYPE_V1_2) {
    return isV12Evidence(data) ? { data, meta: value.meta } : null;
  }
  if (expected === R1_PROVENANCE_MEDIA_TYPE) {
    return isV11Evidence(data) ? { data, meta: value.meta } : null;
  }
  return isV10Evidence(data) ? { data, meta: value.meta } : null;
}

export function parseEventEvidenceViewV1_2(
  value: unknown,
): EventEvidenceViewV1_2 | null {
  return isRecord(value) && isV12Evidence(value) ? value : null;
}

export function isPremarketBriefingV1_1(
  value: PremarketBriefingView,
): value is PremarketBriefingViewV1_1 {
  return value.schema_version === "1.1";
}

export function isPremarketBriefingV1_2(
  value: PremarketBriefingView,
): value is PremarketBriefingViewV1_2 {
  return value.schema_version === "1.2";
}

export function isPremarketBriefingV1_3(
  value: PremarketBriefingView,
): value is PremarketBriefingViewV1_3 {
  return value.schema_version === "1.3";
}

export function isEventEvidenceV1_1(value: EventEvidenceView): value is EventEvidenceViewV1_1 {
  return value.schema_version === "1.1";
}

export function isEventEvidenceV1_2(value: EventEvidenceView): value is EventEvidenceViewV1_2 {
  return value.schema_version === "1.2";
}

export function getCoverageProvenance(
  value: SourceCoverage,
): components["schemas"]["SourceProvenance"] | components["schemas"]["SourceProvenanceV1_2"] | null {
  return "provenance" in value ? value.provenance : null;
}
