import { lstatSync, realpathSync } from "node:fs";
import { lstat, readFile, realpath, stat } from "node:fs/promises";
import { resolve, sep } from "node:path";

import {
  getActiveTarget,
  getEventEvidence,
  getHealth,
  getPremarketBriefing,
} from "../api/client.ts";
import {
  parseActiveTargetResponse,
  parseEventEvidenceResponse,
  parsePremarketBriefingResponse,
  R1_PROVENANCE_MEDIA_TYPE_V1_2,
  type ActiveTargetState,
  type EventEvidenceView,
  type HealthResponse,
  type PremarketBriefingView,
} from "../api/contracts.ts";
import type { ApiResult } from "../api/errors.ts";
import { isPublicStaticBuild, serverConfig } from "../config.ts";
import { isPublicStaticEvidence, parseStaticEvidenceIndex } from "./static-contracts.ts";

const MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024;
const FIXED_INSTRUMENT_ID = "XSHG:603986";
const FIXED_SYMBOL = "603986.SH";

function isSameInstrument(
  left: NonNullable<ActiveTargetState["active_target"]>["instrument"],
  right: PremarketBriefingView["instrument"],
): boolean {
  return (
    left.instrument_id === right.instrument_id &&
    left.symbol === right.symbol &&
    left.exchange === right.exchange &&
    left.market === right.market &&
    left.asset_class === right.asset_class &&
    left.currency === right.currency
  );
}

export interface R1DataProvider {
  readonly kind: "runtime" | "public_static";
  getHealth(): Promise<HealthResponse | null>;
  getActiveTarget(): Promise<ApiResult<ActiveTargetState>>;
  getPremarketBriefing(): Promise<ApiResult<PremarketBriefingView>>;
  getEventEvidence(eventId: string): Promise<ApiResult<EventEvidenceView>>;
  listEvidenceEventIds(): Promise<readonly string[]>;
}

function apiError<T>(code: "not_found" | "invalid_payload"): ApiResult<T> {
  return { data: null, error: { code, status: code === "not_found" ? 404 : null } };
}

export class RuntimeApiDataProvider implements R1DataProvider {
  readonly kind = "runtime" as const;

  getHealth(): Promise<HealthResponse | null> {
    return getHealth();
  }

  getActiveTarget(): Promise<ApiResult<ActiveTargetState>> {
    return getActiveTarget();
  }

  getPremarketBriefing(): Promise<ApiResult<PremarketBriefingView>> {
    return getPremarketBriefing();
  }

  async getEventEvidence(eventId: string): Promise<ApiResult<EventEvidenceView>> {
    const briefing = await getPremarketBriefing();
    if (!briefing.data) {
      return briefing;
    }
    return getEventEvidence(eventId, briefing.data.as_of);
  }

  async listEvidenceEventIds(): Promise<readonly string[]> {
    return [];
  }
}

export class StaticReplayDataProvider implements R1DataProvider {
  readonly kind = "public_static" as const;
  readonly #snapshotDir: string;

  constructor(snapshotDir: string) {
    const requestedRoot = resolve(snapshotDir);
    if (lstatSync(requestedRoot).isSymbolicLink()) {
      throw new Error("PUBLIC_STATIC_SNAPSHOT_ROOT_SYMLINK_FORBIDDEN");
    }
    const canonicalRoot = realpathSync(requestedRoot);
    if (canonicalRoot !== requestedRoot) {
      throw new Error("PUBLIC_STATIC_SNAPSHOT_ROOT_SYMLINK_FORBIDDEN");
    }
    this.#snapshotDir = canonicalRoot;
  }

  async #readJson(relativePath: string): Promise<unknown> {
    const requestedPath = resolve(this.#snapshotDir, relativePath);
    if (!requestedPath.startsWith(`${this.#snapshotDir}${sep}`)) {
      throw new Error("PUBLIC_STATIC_PATH_OUTSIDE_SNAPSHOT");
    }
    const linkMetadata = await lstat(requestedPath);
    if (linkMetadata.isSymbolicLink()) {
      throw new Error("PUBLIC_STATIC_SNAPSHOT_SYMLINK_FORBIDDEN");
    }
    const canonicalPath = await realpath(requestedPath);
    if (
      canonicalPath !== requestedPath ||
      !canonicalPath.startsWith(`${this.#snapshotDir}${sep}`)
    ) {
      throw new Error("PUBLIC_STATIC_PATH_OUTSIDE_SNAPSHOT");
    }
    const metadata = await stat(canonicalPath);
    if (!metadata.isFile() || metadata.size > MAX_SNAPSHOT_BYTES) {
      throw new Error("PUBLIC_STATIC_SNAPSHOT_INVALID_SIZE");
    }
    return JSON.parse(await readFile(canonicalPath, "utf8")) as unknown;
  }

  async #metadataIsValid(): Promise<boolean> {
    const value = await this.#readJson("build-metadata.json");
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      return false;
    }
    const metadata = value as Record<string, unknown>;
    return (
      metadata.schema_version === "1.0" &&
      typeof metadata.product_commit === "string" &&
      /^[a-f0-9]{40}$/.test(metadata.product_commit) &&
      typeof metadata.generated_at === "string" &&
      Number.isFinite(Date.parse(metadata.generated_at)) &&
      metadata.runtime_mode === "PUBLIC_STATIC" &&
      metadata.release_tier === "DEV_ONLY" &&
      metadata.data_mode === "DEMO_REPLAY"
    );
  }

  async #activeTarget() {
    const parsed = parseActiveTargetResponse(await this.#readJson("active-target.json"));
    const target = parsed?.data.active_target;
    if (
      !parsed?.data.found ||
      !target ||
      target.instrument.instrument_id !== FIXED_INSTRUMENT_ID ||
      target.instrument.symbol !== FIXED_SYMBOL
    ) {
      return null;
    }
    return parsed;
  }

  async #briefing() {
    const [metadataIsValid, activeTarget, parsed] = await Promise.all([
      this.#metadataIsValid(),
      this.#activeTarget(),
      this.#readJson("latest-briefing.json").then((value) =>
        parsePremarketBriefingResponse(value, R1_PROVENANCE_MEDIA_TYPE_V1_2),
      ),
    ]);
    const target = activeTarget?.data.active_target;
    if (
      !metadataIsValid ||
      !target ||
      !parsed ||
      parsed.data.schema_version !== "1.2" ||
      parsed.data.runtime_mode !== "public_static" ||
      parsed.data.instrument.instrument_id !== FIXED_INSTRUMENT_ID ||
      parsed.data.instrument.symbol !== FIXED_SYMBOL ||
      !isSameInstrument(target.instrument, parsed.data.instrument) ||
      Date.parse(target.activated_at) > Date.parse(parsed.data.as_of)
    ) {
      return null;
    }
    return parsed;
  }

  async #index() {
    const parsed = parseStaticEvidenceIndex(await this.#readJson("evidence-index.json"));
    if (!parsed) {
      throw new Error("PUBLIC_STATIC_EVIDENCE_INDEX_INVALID");
    }
    return parsed;
  }

  async getHealth(): Promise<HealthResponse | null> {
    return null;
  }

  async getActiveTarget(): Promise<ApiResult<ActiveTargetState>> {
    const [metadataIsValid, parsed] = await Promise.all([
      this.#metadataIsValid(),
      this.#activeTarget(),
    ]);
    if (!metadataIsValid) {
      return apiError("invalid_payload");
    }
    return parsed ? { data: parsed.data, error: null } : apiError("invalid_payload");
  }

  async getPremarketBriefing(): Promise<ApiResult<PremarketBriefingView>> {
    const parsed = await this.#briefing();
    if (!parsed) return apiError("invalid_payload");
    return { data: parsed.data, error: null };
  }

  async getEventEvidence(eventId: string): Promise<ApiResult<EventEvidenceView>> {
    const entry = (await this.#index()).events.find((item) => item.eventId === eventId);
    if (!entry) {
      return apiError("not_found");
    }
    const parsed = parseEventEvidenceResponse(
      await this.#readJson(entry.path),
      R1_PROVENANCE_MEDIA_TYPE_V1_2,
    );
    const briefing = await this.#briefing();
    return parsed &&
      briefing &&
      isPublicStaticEvidence(parsed) &&
      parsed.data.event_id === eventId &&
      parsed.data.instrument_ids.length === 1 &&
      parsed.data.instrument_ids[0] === FIXED_INSTRUMENT_ID &&
      parsed.data.as_of === briefing.data.as_of
      ? { data: parsed.data, error: null }
      : apiError("invalid_payload");
  }

  async listEvidenceEventIds(): Promise<readonly string[]> {
    if (!(await this.#metadataIsValid())) {
      throw new Error("PUBLIC_STATIC_BUILD_METADATA_INVALID");
    }
    return (await this.#index()).events.map((item) => item.eventId);
  }
}

export function getR1DataProvider(): R1DataProvider {
  if (!isPublicStaticBuild) {
    return new RuntimeApiDataProvider();
  }
  if (!serverConfig.publicSnapshotDir) {
    throw new Error("PUBLIC_STATIC_SNAPSHOT_DIR_ABSOLUTE_REQUIRED");
  }
  return new StaticReplayDataProvider(serverConfig.publicSnapshotDir);
}
