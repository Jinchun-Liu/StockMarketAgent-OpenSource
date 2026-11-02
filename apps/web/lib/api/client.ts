import { serverConfig } from "../config.ts";
import { apiErrorCode, type ApiErrorCode, type ApiResult } from "./errors.ts";
import {
  parseActiveTargetResponse,
  parseEventEvidenceResponse,
  parseHealthResponse,
  parsePremarketBriefingResponse,
  R1_PROVENANCE_MEDIA_TYPE,
  R1_PROVENANCE_MEDIA_TYPE_V1_2,
  R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3,
  type ActiveTargetState,
  type EventEvidenceView,
  type HealthResponse,
  type PremarketBriefingView,
} from "./contracts.ts";

function errorResult<T>(code: ApiErrorCode, status: number | null): ApiResult<T> {
  return { data: null, error: { code, status } };
}

async function fetchJson(
  url: string,
  accept: string,
): Promise<{ payload: unknown; mediaType: string | null; status: number } | { error: ApiErrorCode; status: number | null }> {
  try {
    const response = await fetch(url, {
      cache: "no-store",
      headers: { Accept: accept },
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) {
      return { error: apiErrorCode(response.status), status: response.status };
    }
    const mediaType = response.headers.get("content-type");
    const normalized = mediaType?.split(";", 1)[0]?.trim().toLowerCase() ?? null;
    if (normalized !== accept.toLowerCase()) {
      return { error: "invalid_payload", status: response.status };
    }
    return {
      payload: await response.json(),
      mediaType,
      status: response.status,
    };
  } catch {
    return { error: "network_error", status: null };
  }
}

export async function getHealth(): Promise<HealthResponse | null> {
  try {
    const response = await fetch(`${serverConfig.apiInternalUrl}/api/v1/health/live`, {
      cache: "no-store",
      signal: AbortSignal.timeout(1500),
    });
    if (!response.ok) {
      return null;
    }
    const payload: unknown = await response.json();
    return parseHealthResponse(payload);
  } catch {
    return null;
  }
}

export async function getActiveTarget(): Promise<ApiResult<ActiveTargetState>> {
  const result = await fetchJson(`${serverConfig.apiInternalUrl}/api/v1/active-target`, "application/json");
  if ("error" in result) {
    return errorResult(result.error, result.status);
  }
  const parsed = parseActiveTargetResponse(result.payload);
  return parsed ? { data: parsed.data, error: null } : errorResult("invalid_payload", result.status);
}

export async function getPremarketBriefing(): Promise<ApiResult<PremarketBriefingView>> {
  const url = `${serverConfig.apiInternalUrl}/api/v1/briefings/premarket/latest`;
  const research = await fetchJson(url, R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3);
  if (!("error" in research)) {
    const parsedResearch = parsePremarketBriefingResponse(research.payload, research.mediaType);
    return parsedResearch ? { data: parsedResearch.data, error: null } : errorResult("invalid_payload", research.status);
  }
  if (research.error !== "provenance_unavailable") {
    return errorResult(research.error, research.status);
  }
  const preferred = await fetchJson(url, R1_PROVENANCE_MEDIA_TYPE_V1_2);
  if ("error" in preferred) {
    if (preferred.error !== "provenance_unavailable") {
      return errorResult(preferred.error, preferred.status);
    }
    const v1 = await fetchJson(url, R1_PROVENANCE_MEDIA_TYPE);
    if (!("error" in v1)) {
      const parsedV1 = parsePremarketBriefingResponse(v1.payload, v1.mediaType);
      return parsedV1 ? { data: parsedV1.data, error: null } : errorResult("invalid_payload", v1.status);
    }
    if (v1.error !== "provenance_unavailable") {
      return errorResult(v1.error, v1.status);
    }
    const legacy = await fetchJson(url, "application/json");
    if ("error" in legacy) {
      return errorResult(legacy.error, legacy.status);
    }
    const parsedLegacy = parsePremarketBriefingResponse(legacy.payload, legacy.mediaType);
    return parsedLegacy ? { data: parsedLegacy.data, error: null } : errorResult("invalid_payload", legacy.status);
  }
  const parsed = parsePremarketBriefingResponse(preferred.payload, preferred.mediaType);
  return parsed ? { data: parsed.data, error: null } : errorResult("invalid_payload", preferred.status);
}

export async function getEventEvidence(
  eventId: string,
  asOf: string,
): Promise<ApiResult<EventEvidenceView>> {
  const query = new URLSearchParams({ as_of: asOf });
  const url = `${serverConfig.apiInternalUrl}/api/v1/events/${encodeURIComponent(eventId)}/evidence?${query}`;
  const preferred = await fetchJson(url, R1_PROVENANCE_MEDIA_TYPE_V1_2);
  if ("error" in preferred) {
    if (preferred.error !== "provenance_unavailable") {
      return errorResult(preferred.error, preferred.status);
    }
    const v1 = await fetchJson(url, R1_PROVENANCE_MEDIA_TYPE);
    if (!("error" in v1)) {
      const parsedV1 = parseEventEvidenceResponse(v1.payload, v1.mediaType);
      return parsedV1 ? { data: parsedV1.data, error: null } : errorResult("invalid_payload", v1.status);
    }
    if (v1.error !== "provenance_unavailable") {
      return errorResult(v1.error, v1.status);
    }
    const legacy = await fetchJson(url, "application/json");
    if ("error" in legacy) {
      return errorResult(legacy.error, legacy.status);
    }
    const parsedLegacy = parseEventEvidenceResponse(legacy.payload, legacy.mediaType);
    return parsedLegacy ? { data: parsedLegacy.data, error: null } : errorResult("invalid_payload", legacy.status);
  }
  const parsed = parseEventEvidenceResponse(preferred.payload, preferred.mediaType);
  return parsed ? { data: parsed.data, error: null } : errorResult("invalid_payload", preferred.status);
}
