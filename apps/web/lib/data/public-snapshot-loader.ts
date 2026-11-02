import {
  parseEventEvidenceViewV1_2,
  parsePremarketBriefingViewV1_2OrV1_3,
  type EventEvidenceViewV1_2,
  type PremarketBriefingViewV1_2,
  type PremarketBriefingViewV1_3,
  type PublicEvidenceIndex,
  type PublicRunStatus,
  type PublicRunStatusV1_0,
  type PublicSnapshotAsset,
  type PublicSnapshotManifest,
  type PublicSnapshotPointer,
} from "@/lib/api/contracts";

export type LoadedPublicSnapshot = Readonly<{
  pointer: PublicSnapshotPointer;
  manifest: PublicSnapshotManifest;
  briefing: PremarketBriefingViewV1_2 | PremarketBriefingViewV1_3;
  evidenceIndex: PublicEvidenceIndex;
  evidenceByEventId: Readonly<Record<string, EventEvidenceViewV1_2>>;
  runStatus: PublicRunStatus;
}>;

export type SnapshotLoadErrorCode =
  | "CURRENT_UNAVAILABLE"
  | "CROSS_ORIGIN_RESPONSE"
  | "INVALID_MEDIA_TYPE"
  | "DOCUMENT_TOO_LARGE"
  | "INVALID_JSON"
  | "INVALID_POINTER"
  | "MANIFEST_HASH_MISMATCH"
  | "INVALID_MANIFEST"
  | "ASSET_INVENTORY_MISMATCH"
  | "ASSET_HASH_MISMATCH"
  | "INVALID_ASSET"
  | "BUNDLE_INVARIANT_FAILED";

export class PublicSnapshotLoadError extends Error {
  readonly code: SnapshotLoadErrorCode;

  constructor(code: SnapshotLoadErrorCode) {
    super(code);
    this.name = "PublicSnapshotLoadError";
    this.code = code;
  }
}

const POINTER_MAX_BYTES = 64 * 1024;
const MANIFEST_MAX_BYTES = 2 * 1024 * 1024;
const ASSET_MAX_BYTES = 10 * 1024 * 1024;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256_RE = /^[a-f0-9]{64}$/;
const GIT_SHA_RE = /^[a-f0-9]{40}$/;
const SITE_JSON_PATH_RE = /^[a-zA-Z0-9._/-]+\.json$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  const allowedKeys = new Set(allowed);
  return Object.keys(value).every((key) => allowedKeys.has(key));
}

function isTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value);
}

function isSiteJsonPath(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 1024 || !SITE_JSON_PATH_RE.test(value)) return false;
  const parts = value.split("/");
  return !value.startsWith("/") && parts.every((part) => part !== "" && part !== "." && part !== "..");
}

function parsePointer(value: unknown): PublicSnapshotPointer | null {
  if (!isRecord(value) || !hasOnlyKeys(value, [
    "schema_version", "snapshot_id", "manifest_path", "manifest_sha256", "product_commit", "published_at",
  ])) return null;
  if (
    value.schema_version !== "1.0" || !isUuid(value.snapshot_id) || !isSiteJsonPath(value.manifest_path) ||
    value.manifest_path !== `data/snapshots/${value.snapshot_id}/manifest.json` ||
    typeof value.manifest_sha256 !== "string" || !SHA256_RE.test(value.manifest_sha256) ||
    typeof value.product_commit !== "string" || !GIT_SHA_RE.test(value.product_commit) ||
    !isTimestamp(value.published_at)
  ) return null;
  return {
    schema_version: "1.0",
    snapshot_id: value.snapshot_id,
    manifest_path: value.manifest_path,
    manifest_sha256: value.manifest_sha256,
    product_commit: value.product_commit,
    published_at: value.published_at,
  };
}

function parseAsset(value: unknown): PublicSnapshotAsset | null {
  if (!isRecord(value) || !hasOnlyKeys(value, ["kind", "path", "sha256", "media_type", "size_bytes"])) return null;
  if (
    (value.kind !== "briefing" && value.kind !== "evidence_index" && value.kind !== "evidence" && value.kind !== "run_status") ||
    !isSiteJsonPath(value.path) || typeof value.sha256 !== "string" || !SHA256_RE.test(value.sha256) ||
    value.media_type !== "application/json" || typeof value.size_bytes !== "number" ||
    !Number.isInteger(value.size_bytes) || value.size_bytes < 0 || value.size_bytes > ASSET_MAX_BYTES
  ) return null;
  const parts = value.path.split("/");
  const filename = parts.at(-1);
  if (value.kind === "briefing" && filename !== "briefing.json") return null;
  if (value.kind === "evidence_index" && filename !== "evidence-index.json") return null;
  if (value.kind === "run_status" && filename !== "run-status.json") return null;
  if (value.kind === "evidence" && (parts.at(-2) !== "evidence" || !isUuid(filename?.replace(/\.json$/, "")))) return null;
  return {
    kind: value.kind,
    path: value.path,
    sha256: value.sha256,
    media_type: "application/json",
    size_bytes: value.size_bytes,
  };
}

function parseManifest(value: unknown): PublicSnapshotManifest | null {
  if (!isRecord(value) || !hasOnlyKeys(value, [
    "schema_version", "snapshot_id", "product_commit", "delivery_mode", "origin_mode", "release_tier",
    "data_as_of", "generated_at", "published_at", "fresh_until", "last_successful_run_at", "completeness",
    "disclaimer_code", "assets",
  ])) return null;
  if (
    value.schema_version !== "1.1" || !isUuid(value.snapshot_id) ||
    typeof value.product_commit !== "string" || !GIT_SHA_RE.test(value.product_commit) ||
    value.delivery_mode !== "public_static" ||
    (value.origin_mode !== "dev_live" && value.origin_mode !== "demo_replay") ||
    value.release_tier !== "dev_only" || !isTimestamp(value.data_as_of) ||
    !isTimestamp(value.generated_at) || !isTimestamp(value.published_at) || !isTimestamp(value.fresh_until) ||
    (value.last_successful_run_at !== null && !isTimestamp(value.last_successful_run_at)) ||
    (value.completeness !== "complete" && value.completeness !== "partial" && value.completeness !== "unavailable") ||
    !Array.isArray(value.assets) || value.assets.length < 3 || value.assets.length > 1003
  ) return null;
  const expectedDisclaimer = value.origin_mode === "dev_live"
    ? "DEV_ONLY_UNVERIFIED_SOURCE"
    : "HISTORICAL_DEMO_REPLAY";
  if (value.disclaimer_code !== expectedDisclaimer) return null;
  const assets: PublicSnapshotAsset[] = [];
  for (const item of value.assets) {
    const asset = parseAsset(item);
    if (!asset) return null;
    assets.push(asset);
  }
  const dataAsOf = Date.parse(value.data_as_of);
  const generatedAt = Date.parse(value.generated_at);
  const publishedAt = Date.parse(value.published_at);
  const freshUntil = Date.parse(value.fresh_until);
  if (!(dataAsOf <= generatedAt && generatedAt <= publishedAt && publishedAt < freshUntil)) return null;
  if (value.last_successful_run_at !== null && Date.parse(value.last_successful_run_at) > publishedAt) return null;
  const prefix = `data/snapshots/${value.snapshot_id}/`;
  if (assets.some((asset) => !asset.path.startsWith(prefix))) return null;
  if (new Set(assets.map((asset) => asset.path)).size !== assets.length) return null;
  for (const [kind, filename] of [
    ["briefing", "briefing.json"],
    ["evidence_index", "evidence-index.json"],
    ["run_status", "run-status.json"],
  ] as const) {
    const matching = assets.filter((asset) => asset.kind === kind);
    if (matching.length !== 1 || matching[0]?.path !== `${prefix}${filename}`) return null;
  }
  return {
    schema_version: "1.1",
    snapshot_id: value.snapshot_id,
    product_commit: value.product_commit,
    delivery_mode: "public_static",
    origin_mode: value.origin_mode,
    release_tier: "dev_only",
    data_as_of: value.data_as_of,
    generated_at: value.generated_at,
    published_at: value.published_at,
    fresh_until: value.fresh_until,
    last_successful_run_at: value.last_successful_run_at,
    completeness: value.completeness,
    disclaimer_code: expectedDisclaimer,
    assets,
  };
}

function parseEvidenceIndex(value: unknown): PublicEvidenceIndex | null {
  if (!isRecord(value) || !hasOnlyKeys(value, ["schema_version", "events"]) ||
      value.schema_version !== "1.0" || !Array.isArray(value.events) || value.events.length > 1000) return null;
  const events: PublicEvidenceIndex["events"] = [];
  for (const item of value.events) {
    if (!isRecord(item) || !hasOnlyKeys(item, ["event_id", "path"]) || !isUuid(item.event_id) ||
        item.path !== `evidence/${item.event_id}.json`) return null;
    events.push({ event_id: item.event_id, path: item.path });
  }
  if (new Set(events.map((item) => item.event_id)).size !== events.length ||
      new Set(events.map((item) => item.path)).size !== events.length) return null;
  return { schema_version: "1.0", events };
}

function parseRunStatus(value: unknown): PublicRunStatus | null {
  if (!isRecord(value) || !hasOnlyKeys(value, [
    "schema_version", "run_id", "snapshot_id", "started_at", "completed_at", "status", "capabilities",
  ]) || (value.schema_version !== "1.0" && value.schema_version !== "1.1") ||
      !isUuid(value.run_id) || !isUuid(value.snapshot_id) ||
      !isTimestamp(value.started_at) || !isTimestamp(value.completed_at) ||
      (value.status !== "succeeded" && value.status !== "degraded" && value.status !== "failed") ||
      !Array.isArray(value.capabilities) || value.capabilities.length < 1 || value.capabilities.length > 100 ||
      Date.parse(value.completed_at) < Date.parse(value.started_at)) return null;
  const capabilities: PublicRunStatus["capabilities"] = [];
  for (const item of value.capabilities) {
    if (!isRecord(item) || !hasOnlyKeys(item, ["source_id", "capability", "source_grade", "state", "safe_reason_code"]) ||
        typeof item.source_id !== "string" || !/^[a-z][a-z0-9_-]{2,99}$/.test(item.source_id) ||
        typeof item.capability !== "string" || !/^[a-z][a-z0-9_.-]{1,99}$/.test(item.capability) ||
        (item.source_grade !== "free_official_unverified" && item.source_grade !== "free_structured_unverified" &&
          item.source_grade !== "dev_web_unverified" && item.source_grade !== "demo_replay") ||
        (item.state !== "succeeded" && item.state !== "no_change" && item.state !== "gap" &&
          item.state !== "blocked" && item.state !== "failed" && item.state !== "stale")) return null;
    const healthy = item.state === "succeeded" || item.state === "no_change";
    let safeReasonCode: string | null;
    if (healthy) {
      if (item.safe_reason_code !== null) return null;
      safeReasonCode = null;
    } else {
      if (typeof item.safe_reason_code !== "string" || !/^[A-Z][A-Z0-9_]{1,63}$/.test(item.safe_reason_code)) return null;
      safeReasonCode = item.safe_reason_code;
    }
    capabilities.push({
      source_id: item.source_id,
      capability: item.capability,
      source_grade: item.source_grade,
      state: item.state,
      safe_reason_code: safeReasonCode,
    });
  }
  if (new Set(capabilities.map((item) => `${item.source_id}\u0000${item.capability}`)).size !== capabilities.length) return null;
  const healthyCount = capabilities.filter((item) => item.state === "succeeded" || item.state === "no_change").length;
  const hardProblem = capabilities.some((item) => item.state === "gap" || item.state === "blocked" || item.state === "failed");
  if (value.status === "succeeded" && healthyCount !== capabilities.length) return null;
  if (value.status === "failed" && (healthyCount !== 0 || !hardProblem)) return null;
  if (value.status === "degraded") {
    const allStale = capabilities.every((item) => item.state === "stale");
    if (healthyCount === capabilities.length ||
        (value.schema_version === "1.0" && healthyCount === 0 && !allStale)) return null;
  }
  const common: Omit<PublicRunStatusV1_0, "schema_version"> = {
    run_id: value.run_id,
    snapshot_id: value.snapshot_id,
    started_at: value.started_at,
    completed_at: value.completed_at,
    status: value.status,
    capabilities,
  };
  return value.schema_version === "1.0"
    ? { schema_version: "1.0", ...common }
    : { schema_version: "1.1", ...common };
}

function decodeJson(bytes: Uint8Array, code: SnapshotLoadErrorCode): unknown {
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } catch {
    throw new PublicSnapshotLoadError(code);
  }
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const input = new Uint8Array(bytes.byteLength);
  input.set(bytes);
  const digest = await crypto.subtle.digest("SHA-256", input.buffer);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function fetchBytes(path: string, maxBytes: number, cache: RequestCache): Promise<Uint8Array> {
  if (!isSiteJsonPath(path)) throw new PublicSnapshotLoadError("BUNDLE_INVARIANT_FAILED");
  const expected = new URL(`/${path}`, window.location.origin);
  let response: Response;
  try {
    response = await fetch(expected.pathname, {
      cache,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      redirect: "error",
    });
  } catch {
    throw new PublicSnapshotLoadError(path === "data/current.json" ? "CURRENT_UNAVAILABLE" : "INVALID_ASSET");
  }
  if (!response.ok) throw new PublicSnapshotLoadError(path === "data/current.json" ? "CURRENT_UNAVAILABLE" : "INVALID_ASSET");
  const finalUrl = new URL(response.url);
  if (finalUrl.origin !== window.location.origin || finalUrl.pathname !== expected.pathname) {
    throw new PublicSnapshotLoadError("CROSS_ORIGIN_RESPONSE");
  }
  if (response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() !== "application/json") {
    throw new PublicSnapshotLoadError("INVALID_MEDIA_TYPE");
  }
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) throw new PublicSnapshotLoadError("DOCUMENT_TOO_LARGE");
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > maxBytes) throw new PublicSnapshotLoadError("DOCUMENT_TOO_LARGE");
  return bytes;
}

function assetsOfKind(
  manifest: PublicSnapshotManifest,
  kind: PublicSnapshotAsset["kind"],
): PublicSnapshotAsset[] {
  return manifest.assets.filter((asset) => asset.kind === kind);
}

function requiredDocument(documents: ReadonlyMap<string, Uint8Array>, path: string): Uint8Array {
  const value = documents.get(path);
  if (!value) throw new PublicSnapshotLoadError("ASSET_INVENTORY_MISMATCH");
  return value;
}

export async function loadCurrentPublicSnapshot(): Promise<LoadedPublicSnapshot> {
  const pointerBytes = await fetchBytes("data/current.json", POINTER_MAX_BYTES, "no-store");
  const pointer = parsePointer(decodeJson(pointerBytes, "INVALID_JSON"));
  if (!pointer) throw new PublicSnapshotLoadError("INVALID_POINTER");

  const manifestBytes = await fetchBytes(pointer.manifest_path, MANIFEST_MAX_BYTES, "force-cache");
  if (await sha256(manifestBytes) !== pointer.manifest_sha256) {
    throw new PublicSnapshotLoadError("MANIFEST_HASH_MISMATCH");
  }
  const manifest = parseManifest(decodeJson(manifestBytes, "INVALID_JSON"));
  if (!manifest) throw new PublicSnapshotLoadError("INVALID_MANIFEST");
  if (
    pointer.snapshot_id !== manifest.snapshot_id ||
    pointer.product_commit !== manifest.product_commit ||
    pointer.published_at !== manifest.published_at
  ) throw new PublicSnapshotLoadError("BUNDLE_INVARIANT_FAILED");

  const documents = new Map<string, Uint8Array>();
  await Promise.all(manifest.assets.map(async (asset) => {
    const bytes = await fetchBytes(asset.path, ASSET_MAX_BYTES, "force-cache");
    if (bytes.byteLength !== asset.size_bytes || await sha256(bytes) !== asset.sha256) {
      throw new PublicSnapshotLoadError("ASSET_HASH_MISMATCH");
    }
    documents.set(asset.path, bytes);
  }));
  if (documents.size !== manifest.assets.length) {
    throw new PublicSnapshotLoadError("ASSET_INVENTORY_MISMATCH");
  }

  const briefingAsset = assetsOfKind(manifest, "briefing")[0];
  const indexAsset = assetsOfKind(manifest, "evidence_index")[0];
  const runStatusAsset = assetsOfKind(manifest, "run_status")[0];
  if (!briefingAsset || !indexAsset || !runStatusAsset) {
    throw new PublicSnapshotLoadError("ASSET_INVENTORY_MISMATCH");
  }
  const briefing = parsePremarketBriefingViewV1_2OrV1_3(
    decodeJson(requiredDocument(documents, briefingAsset.path), "INVALID_JSON"),
  );
  const evidenceIndex = parseEvidenceIndex(
    decodeJson(requiredDocument(documents, indexAsset.path), "INVALID_JSON"),
  );
  const runStatus = parseRunStatus(
    decodeJson(requiredDocument(documents, runStatusAsset.path), "INVALID_JSON"),
  );
  if (!briefing || !evidenceIndex || !runStatus) {
    throw new PublicSnapshotLoadError("INVALID_ASSET");
  }

  const prefix = `data/snapshots/${manifest.snapshot_id}/`;
  const evidenceAssets = assetsOfKind(manifest, "evidence");
  const indexedPaths = new Set(evidenceIndex.events.map((entry) => `${prefix}${entry.path}`));
  if (
    indexedPaths.size !== evidenceAssets.length ||
    evidenceAssets.some((asset) => !indexedPaths.has(asset.path))
  ) throw new PublicSnapshotLoadError("ASSET_INVENTORY_MISMATCH");
  const evidenceByEventId: Record<string, EventEvidenceViewV1_2> = {};
  for (const entry of evidenceIndex.events) {
    const path = `${prefix}${entry.path}`;
    const evidence = parseEventEvidenceViewV1_2(
      decodeJson(requiredDocument(documents, path), "INVALID_JSON"),
    );
    if (!evidence || evidence.event_id !== entry.event_id) {
      throw new PublicSnapshotLoadError("INVALID_ASSET");
    }
    evidenceByEventId[entry.event_id] = evidence;
  }

  const expectedStatus = {
    complete: "succeeded",
    partial: "degraded",
    unavailable: "failed",
  } as const;
  const grades = new Set(runStatus.capabilities.map((item) => item.source_grade));
  if (
    runStatus.snapshot_id !== manifest.snapshot_id ||
    Date.parse(runStatus.completed_at) > Date.parse(manifest.generated_at) ||
    runStatus.status !== expectedStatus[manifest.completeness] ||
    briefing.runtime_mode !== manifest.origin_mode ||
    briefing.as_of !== manifest.data_as_of ||
    Object.values(evidenceByEventId).some(
      (item) => item.runtime_mode !== manifest.origin_mode || item.as_of !== manifest.data_as_of,
    ) ||
    (manifest.origin_mode === "demo_replay"
      ? grades.size !== 1 || !grades.has("demo_replay")
      : grades.has("demo_replay"))
  ) throw new PublicSnapshotLoadError("BUNDLE_INVARIANT_FAILED");

  return Object.freeze({
    pointer,
    manifest,
    briefing,
    evidenceIndex,
    evidenceByEventId: Object.freeze(evidenceByEventId),
    runStatus,
  });
}
