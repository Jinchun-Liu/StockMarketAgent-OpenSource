import type { components } from "./openapi.generated";
import type { RuntimeWebConfig } from "@/lib/preview/runtime-config";

export type SearchMatch = components["schemas"]["InstrumentSearchMatchV1_1"];
export type AvailableResult = components["schemas"]["stock_assistant__contracts__research__ResearchAvailableResult"];
export type Session = components["schemas"]["stock_assistant__contracts__research__ResearchSessionView"];
export type Report = components["schemas"]["ResearchReportV2_2"] | components["schemas"]["ResearchReportV2_1"] | components["schemas"]["ResearchReportV1_2"] | components["schemas"]["ResearchReportV2"];
export type Claim = components["schemas"]["stock_assistant__contracts__research__ResearchClaim"];
export type Citation = components["schemas"]["stock_assistant__contracts__research__ResearchEvidenceCitation"];
export type SessionStage = components["schemas"]["ResearchStage"];
export type SkillCatalog = components["schemas"]["ResearchSkillCatalog"];
export type SkillCatalogItem = components["schemas"]["ResearchSkillCatalogItem"];
export type SkillId = components["schemas"]["ResearchSkillId"];
export type SkillSelection = components["schemas"]["ResearchSkillSelection"];

type SearchResult = components["schemas"]["InstrumentSearchResultV1_1"];
type SessionAccepted = components["schemas"]["ResearchSessionAccepted"];
type SessionPage = components["schemas"]["stock_assistant__contracts__research__ResearchSessionPage"];
type MutationResult = components["schemas"]["ResearchSessionMutationResult"];

export class ApiProblem extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiProblem";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isSearchMatch(value: unknown): value is SearchMatch {
  if (!isRecord(value)) return false;
  return (
    isString(value.instrument_id) &&
    isString(value.symbol) &&
    isString(value.short_name) &&
    isString(value.legal_name) &&
    typeof value.legal_name_verified === "boolean" &&
    isString(value.exchange) &&
    isString(value.listing_status) &&
    isString(value.match_type) &&
    isString(value.revision_known_at)
  );
}

function isSearchResult(value: unknown): value is SearchResult {
  return (
    isRecord(value) &&
    isString(value.query) &&
    isString(value.as_of) &&
    isString(value.searched_at) &&
    Array.isArray(value.matches) &&
    value.matches.every(isSearchMatch)
  );
}

const statuses: ReadonlySet<string> = new Set([
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "stalled",
  "expired",
  "deleted",
]);

const stages: ReadonlySet<string> = new Set([
  "queued",
  "resolving_instrument",
  "collecting_history",
  "auditing_data",
  "refetching_data",
  "scoring",
  "generating_report",
  "analyzing_business",
  "analyzing_financials",
  "analyzing_valuation",
  "analyzing_events",
  "debating_cases",
  "validating_report",
  "completed",
]);

const acquisitionModes: ReadonlySet<string> = new Set(["built_in_api", "model_assisted_web"]);
const sourceLevels: ReadonlySet<string> = new Set([
  "regulatory_official",
  "company_official",
  "trusted_media_data",
  "synthetic",
]);
const verificationStatuses: ReadonlySet<string> = new Set([
  "independently_verified",
  "single_source",
  "unverified",
]);
const decimalFactor = /^(?:0(?:\.\d+)?|1(?:\.0+)?)$/;
const sha256 = /^[a-f0-9]{64}$/;

function isSession(value: unknown): value is Session {
  if (!isRecord(value) || !isRecord(value.progress)) return false;
  return (
    value.schema_version === "1.0" &&
    (value.result_completeness == null || (isString(value.result_completeness) && ["complete", "partial", "insufficient_evidence", "execution_failed"].includes(value.result_completeness))) &&
    (value.available_result == null || isAvailableResult(value.available_result)) &&
    isString(value.session_id) &&
    isString(value.job_id) &&
    isString(value.instrument_id) &&
    isString(value.instrument_name) &&
    isString(value.status) &&
    statuses.has(value.status) &&
    typeof value.retryable === "boolean" &&
    isString(value.created_at) &&
    isString(value.updated_at) &&
    isString(value.expires_at) &&
    isString(value.progress.stage) &&
    stages.has(value.progress.stage) &&
    isString(value.progress.latest_progress_at) &&
    isString(value.progress.message)
  );
}

function isSessionAccepted(value: unknown): value is SessionAccepted {
  return (
    isRecord(value) &&
    isString(value.session_id) &&
    isString(value.job_id) &&
    value.status === "queued" &&
    isString(value.accepted_at) &&
    typeof value.idempotent_replay === "boolean"
  );
}

function isSessionPage(value: unknown): value is SessionPage {
  return (
    isRecord(value) &&
    isString(value.as_of) &&
    typeof value.has_more === "boolean" &&
    Array.isArray(value.items) &&
    value.items.every(isSession)
  );
}

function isMutationResult(value: unknown): value is MutationResult {
  return (
    isRecord(value) &&
    isString(value.session_id) &&
    isString(value.status) &&
    statuses.has(value.status) &&
    isString(value.processed_at) &&
    typeof value.idempotent_replay === "boolean"
  );
}

function isSkillCatalogItem(value: unknown): value is SkillCatalogItem {
  return (
    isRecord(value) &&
    isString(value.skill_id) &&
    isString(value.display_name) &&
    isString(value.plain_language_purpose) &&
    isString(value.group) &&
    typeof value.is_core === "boolean" &&
    isString(value.availability) &&
    isString(value.method_version)
  );
}

function isSkillCatalog(value: unknown): value is SkillCatalog {
  return (
    isRecord(value) &&
    isString(value.instrument_id) &&
    isString(value.method_library_version) &&
    Array.isArray(value.items) &&
    value.items.length === 21 &&
    value.items.every(isSkillCatalogItem)
  );
}

function isFormalCitation(value: unknown): boolean {
  if (!isRecord(value) || !isString(value.document_id) || !isString(value.evidence_id) ||
      !isString(value.retrieval_receipt_id) || !isRecord(value.verification) ||
      !isRecord(value.fact_locations) || !isString(value.extraction_scope)) return false;
  const identity = value.verification.identity;
  if (!isRecord(identity) || !isString(identity.title) || !isString(identity.issuer_name) ||
      !isString(identity.native_document_id) || !isString(identity.canonical_url)) return false;
  try { if (new URL(identity.canonical_url).protocol !== "https:") return false; } catch { return false; }
  if (!Object.values(value.fact_locations).every((location) => isRecord(location) &&
      typeof location.start === "number" && typeof location.end === "number" &&
      location.start >= 0 && location.end > location.start && location.end <= 100000 &&
      (location.page_number == null || typeof location.page_number === "number"))) return false;
  const proofs = value.verification.publication_evidence;
  return Array.isArray(proofs) && proofs.length > 0 && proofs.length <= 8 && proofs.every((proof) =>
    isRecord(proof) && isString(proof.kind) && isString(proof.raw_value) &&
    isRecord(proof.location) && isString(proof.location.field));
}

function isCitation(value: unknown): value is Citation {
  if (!isRecord(value)) return false;
  if (value.formal_document != null && !isFormalCitation(value.formal_document)) return false;
  const baseValid = (
    isString(value.evidence_id) &&
    isString(value.source_label) &&
    isString(value.source_role) &&
    isString(value.source_provider_id) &&
    isString(value.independent_source_group) &&
    isString(value.published_at) &&
    isString(value.first_seen_at) &&
    isString(value.revision_known_at) &&
    isString(value.locator) &&
    isString(value.acquisition_mode) &&
    acquisitionModes.has(value.acquisition_mode) &&
    isString(value.source_level) &&
    sourceLevels.has(value.source_level) &&
    isString(value.verification_status) &&
    verificationStatuses.has(value.verification_status) &&
    isString(value.reliability_factor) &&
    decimalFactor.test(value.reliability_factor) &&
    (value.content_sha256 === null || (isString(value.content_sha256) && sha256.test(value.content_sha256))) &&
    (value.rights_snapshot_sha256 === null ||
      (isString(value.rights_snapshot_sha256) && sha256.test(value.rights_snapshot_sha256)))
  );
  if (!baseValid) return false;
  if (value.acquisition_mode === "model_assisted_web") {
    return isString(value.content_sha256) && isString(value.rights_snapshot_sha256);
  }
  return true;
}

function isClaim(value: unknown): value is Claim {
  if (!isRecord(value)) return false;
  return (
    isString(value.claim_id) &&
    isString(value.claim_type) &&
    isString(value.section) &&
    isString(value.statement) &&
    typeof value.publication_eligible === "boolean" &&
    Array.isArray(value.evidence) &&
    value.evidence.every(isCitation) &&
    Array.isArray(value.counter_evidence) &&
    value.counter_evidence.every(isCitation)
  );
}

function isSection(value: unknown): boolean {
  return isRecord(value) && isString(value.kind) && isString(value.title) &&
    isString(value.summary) && Array.isArray(value.claim_ids) && value.claim_ids.every(isString);
}

function isGap(value: unknown): boolean {
  return isRecord(value) && isString(value.area) && isString(value.reason_code) && isString(value.detail);
}

function isScorecard(value: unknown): boolean {
  return isRecord(value) && (value.schema_version === "1.0" || value.schema_version === "1.1") &&
    ((value.schema_version === "1.0" && value.scoring_basis === undefined) || value.scoring_basis === "real_evidence" || value.scoring_basis === "synthetic_scenario") &&
    (value.status === "scored" || value.status === "not_scored") &&
    (value.overall_score == null || isString(value.overall_score)) &&
    (value.score_band == null || isString(value.score_band)) &&
    isString(value.score_meaning) && isString(value.evidence_confidence) &&
    isString(value.evidence_confidence_meaning) && isString(value.recommendation_notice) &&
    Array.isArray(value.dimensions) && value.dimensions.length === 6 &&
    value.dimensions.every((dimension) => isRecord(dimension) &&
      isString(dimension.dimension) && isString(dimension.display_name) && isString(dimension.weight) &&
      (dimension.adjusted_score == null || isString(dimension.adjusted_score)) &&
      (dimension.unscored_reason == null || isString(dimension.unscored_reason)) &&
      (dimension.reasons === undefined || (Array.isArray(dimension.reasons) && dimension.reasons.every(isString))) &&
      (dimension.evidence_ids === undefined || (Array.isArray(dimension.evidence_ids) && dimension.evidence_ids.every(isString))) &&
      (dimension.counter_evidence_ids === undefined || (Array.isArray(dimension.counter_evidence_ids) && dimension.counter_evidence_ids.every(isString))));
}

function isReaderEdition(value: unknown): boolean {
  return isRecord(value) && isString(value.one_sentence_takeaway) && isString(value.safe_markdown) &&
    Array.isArray(value.claim_ids) && value.claim_ids.every(isString) &&
    Array.isArray(value.why_this_score) && value.why_this_score.every(isString) &&
    Array.isArray(value.public_analysis_steps) && value.public_analysis_steps.every(isString);
}

function isAvailableResult(value: unknown): value is AvailableResult {
  return isRecord(value) && (value.schema_version === "1.0" || value.schema_version === "1.1" || value.schema_version === "2.0" || value.schema_version === "2.1") && isString(value.as_of) &&
    isString(value.source_mode) && ["synthetic_development_only", "real_development_only", "production_authorized"].includes(value.source_mode) &&
    isString(value.non_production_notice) &&
    (value.claims === undefined || (Array.isArray(value.claims) && value.claims.every(isClaim))) &&
    (value.schema_version !== "2.1" || value.claims === undefined ||
      (Array.isArray(value.claims) && value.claims.every((claim) => isClaim(claim) &&
        [...(claim.evidence ?? []), ...(claim.counter_evidence ?? [])].every((citation) => isFormalCitation(citation.formal_document))))) &&
    (value.sections === undefined || (Array.isArray(value.sections) && value.sections.every(isSection))) &&
    (value.scorecard == null || isScorecard(value.scorecard)) &&
    (value.reader_edition == null || isReaderEdition(value.reader_edition)) &&
    (value.coverage_gaps === undefined || (Array.isArray(value.coverage_gaps) && value.coverage_gaps.every(isGap)));
}

function isReport(value: unknown): value is Report {
  if (!isRecord(value)) return false;
  const baseValid = (
    isString(value.report_id) &&
    isString(value.session_id) &&
    isString(value.instrument_id) &&
    isString(value.instrument_name) &&
    isString(value.as_of) &&
    isString(value.coverage_start) &&
    isString(value.coverage_end) &&
    isString(value.generated_at) &&
    isString(value.expires_at) &&
    isString(value.non_production_notice) &&
    value.paper_only === true &&
    Array.isArray(value.sections) &&
    value.sections.every(isSection) &&
    Array.isArray(value.claims) &&
    value.claims.every(isClaim) &&
    Array.isArray(value.coverage_gaps) && value.coverage_gaps.every(isGap)
  );
  if (!baseValid) return false;
  if (value.schema_version !== "1.2" && value.schema_version !== "2.0" && value.schema_version !== "2.1" && value.schema_version !== "2.2") return false;
  if (value.schema_version === "2.2" && (!Array.isArray(value.claims) ||
      !value.claims.every((claim) => isClaim(claim) &&
        [...(claim.evidence ?? []), ...(claim.counter_evidence ?? [])].every((citation) => isFormalCitation(citation.formal_document))))) return false;
  return (
    isReaderEdition(value.reader_edition) && isScorecard(value.scorecard) &&
    Array.isArray(value.skill_executions) &&
    value.skill_executions.length > 0 &&
    isRecord(value.web_retrieval_disclosure) &&
    typeof value.web_retrieval_disclosure.model_assisted_item_count === "number" &&
    typeof value.web_retrieval_disclosure.not_independently_verified_count === "number" &&
    isString(value.web_retrieval_disclosure.warning)
  );
}

function problemFromPayload(status: number, payload: unknown): ApiProblem {
  if (isRecord(payload)) {
    const detail = isString(payload.detail) ? payload.detail : `请求失败 (${status})`;
    const code = isString(payload.code)
      ? payload.code
      : isString(payload.title)
        ? payload.title
        : `HTTP_${status}`;
    return new ApiProblem(status, code, detail);
  }
  return new ApiProblem(status, `HTTP_${status}`, `请求失败 (${status})`);
}

async function pause(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  await new Promise<void>((resolve, reject) => {
    const timer = globalThis.setTimeout(resolve, milliseconds);
    signal?.addEventListener(
      "abort",
      () => {
        globalThis.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

async function requestData<T>(
  config: RuntimeWebConfig,
  accessToken: string,
  path: string,
  validate: (value: unknown) => value is T,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const attempts = init.method === undefined || init.method === "GET" ? 3 : 1;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const timeout = new AbortController();
    let abortedByCaller = false;
    const timeoutId = globalThis.setTimeout(
      () => timeout.abort(new DOMException("请求超时", "TimeoutError")),
      12_000,
    );
    const abort = () => {
      abortedByCaller = true;
      timeout.abort(new DOMException("Aborted", "AbortError"));
    };
    signal?.addEventListener("abort", abort, { once: true });
    try {
      const response = await fetch(`${config.api_base_path}${path}`, {
        ...init,
        cache: "no-store",
        signal: timeout.signal,
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${accessToken}`,
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...init.headers,
        },
      });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        if (attempt + 1 < attempts && [502, 503, 504].includes(response.status)) {
          await pause(300 * 2 ** attempt, signal);
          continue;
        }
        throw problemFromPayload(response.status, payload);
      }
      if (!isRecord(payload) || !validate(payload.data)) {
        throw new ApiProblem(502, "API_CONTRACT_INVALID", "服务返回了无法验证的数据结构");
      }
      return payload.data;
    } catch (caught) {
      if (abortedByCaller || signal?.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      if (timeout.signal.aborted) {
        if (attempt + 1 < attempts) {
          await pause(300 * 2 ** attempt, signal);
          continue;
        }
        throw new ApiProblem(504, "REQUEST_TIMEOUT", "服务响应超时，请稍后再试");
      }
      throw caught;
    } finally {
      globalThis.clearTimeout(timeoutId);
      signal?.removeEventListener("abort", abort);
    }
  }
  throw new ApiProblem(503, "API_RETRY_EXHAUSTED", "服务暂时不可用");
}

function mutationKey(operation: string): string {
  return `${operation}-${crypto.randomUUID()}`;
}

export function searchInstruments(
  config: RuntimeWebConfig,
  accessToken: string,
  query: string,
  signal?: AbortSignal,
): Promise<SearchResult> {
  return requestData(
    config,
    accessToken,
    `/instruments/directory-search?q=${encodeURIComponent(query.trim())}&limit=10`,
    isSearchResult,
    {},
    signal,
  );
}

export function createResearch(
  config: RuntimeWebConfig,
  accessToken: string,
  instrumentId: string,
  skillSelection: SkillSelection = {
    schema_version: "1.0",
    mode: "auto",
    selected_skill_ids: [],
  },
): Promise<SessionAccepted> {
  return requestData(config, accessToken, "/research-sessions", isSessionAccepted, {
    method: "POST",
    headers: { "Idempotency-Key": mutationKey("create") },
    body: JSON.stringify({
      schema_version: "1.0",
      instrument_id: instrumentId,
      skill_selection: skillSelection,
    }),
  });
}

export function getResearchSkills(
  config: RuntimeWebConfig,
  accessToken: string,
  instrumentId: string,
  signal?: AbortSignal,
): Promise<SkillCatalog> {
  return requestData(
    config,
    accessToken,
    `/research-skills?instrument_id=${encodeURIComponent(instrumentId)}`,
    isSkillCatalog,
    {},
    signal,
  );
}

export function getSession(
  config: RuntimeWebConfig,
  accessToken: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<Session> {
  return requestData(
    config,
    accessToken,
    `/research-sessions/${encodeURIComponent(sessionId)}/view-v2.0`,
    isSession,
    {},
    signal,
  );
}

export function listSessions(
  config: RuntimeWebConfig,
  accessToken: string,
  cursor?: string,
  signal?: AbortSignal,
): Promise<SessionPage> {
  const query = cursor ? `?limit=20&cursor=${encodeURIComponent(cursor)}` : "?limit=20";
  return requestData(
    config,
    accessToken,
    `/research-sessions-v2.0${query}`,
    isSessionPage,
    {},
    signal,
  );
}

export function getReport(
  config: RuntimeWebConfig,
  accessToken: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<Report> {
  return requestData(
    config,
    accessToken,
    `/research-sessions/${encodeURIComponent(sessionId)}/report-v2.2`,
    isReport,
    {},
    signal,
  );
}

export function mutateSession(
  config: RuntimeWebConfig,
  accessToken: string,
  sessionId: string,
  operation: "cancel" | "retry" | "delete",
): Promise<MutationResult> {
  const suffix = operation === "delete" ? "" : `/${operation}`;
  return requestData(
    config,
    accessToken,
    `/research-sessions/${encodeURIComponent(sessionId)}${suffix}`,
    isMutationResult,
    {
      method: operation === "delete" ? "DELETE" : "POST",
      headers: { "Idempotency-Key": mutationKey(operation) },
    },
  );
}
