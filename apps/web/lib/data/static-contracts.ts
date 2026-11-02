import type { EventEvidenceResponse } from "../api/contracts.ts";

export type StaticEvidenceIndexItem = {
  eventId: string;
  path: `evidence/${string}.json`;
};

export type StaticEvidenceIndex = {
  schemaVersion: "1.0";
  events: StaticEvidenceIndexItem[];
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseStaticEvidenceIndex(value: unknown): StaticEvidenceIndex | null {
  if (!isRecord(value) || value.schema_version !== "1.0" || !Array.isArray(value.events)) {
    return null;
  }
  const seen = new Set<string>();
  const events: StaticEvidenceIndexItem[] = [];
  for (const item of value.events) {
    if (!isRecord(item) || typeof item.event_id !== "string" || !UUID_RE.test(item.event_id)) {
      return null;
    }
    const expectedPath = `evidence/${item.event_id}.json` as const;
    if (item.path !== expectedPath || seen.has(item.event_id)) {
      return null;
    }
    seen.add(item.event_id);
    events.push({ eventId: item.event_id, path: expectedPath });
  }
  return { schemaVersion: "1.0", events };
}

export function isPublicStaticEvidence(response: EventEvidenceResponse): boolean {
  return response.data.schema_version === "1.2" && response.data.runtime_mode === "public_static";
}
