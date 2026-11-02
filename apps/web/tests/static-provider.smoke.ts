import assert from "node:assert/strict";
import { cp, copyFile, mkdir, mkdtemp, readFile, symlink, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

import { serverConfig } from "../lib/config.ts";
import { StaticReplayDataProvider } from "../lib/data/provider.ts";
import { parseStaticEvidenceIndex } from "../lib/data/static-contracts.ts";

const snapshotDir = serverConfig.publicSnapshotDir;
if (!snapshotDir) throw new Error("PUBLIC_STATIC_SNAPSHOT_DIR_REQUIRED");
const validatedSnapshotDir: string = snapshotDir;

const provider = new StaticReplayDataProvider(snapshotDir);
const [activeTarget, briefing, eventIds] = await Promise.all([
  provider.getActiveTarget(),
  provider.getPremarketBriefing(),
  provider.listEvidenceEventIds(),
]);
assert.equal(activeTarget.data?.active_target?.instrument.instrument_id, "XSHG:603986");
assert.equal(briefing.data?.schema_version, "1.2");
assert.equal(briefing.data && "runtime_mode" in briefing.data ? briefing.data.runtime_mode : null, "public_static");
assert.deepEqual(eventIds, [
  "10000000-0000-4000-8000-000000000001",
  "10000000-0000-4000-8000-000000000002",
]);

const correction = await provider.getEventEvidence(eventIds[0] ?? "");
const contradiction = await provider.getEventEvidence(eventIds[1] ?? "");
assert.equal(correction.data?.change_type, "correction");
assert.equal(contradiction.data?.change_type, "contradiction");
assert.equal((await provider.getEventEvidence("90000000-0000-4000-8000-000000000099")).error?.code, "not_found");

assert.equal(parseStaticEvidenceIndex({ schema_version: "1.0", events: [] })?.events.length, 0);
assert.equal(
  parseStaticEvidenceIndex({
    schema_version: "1.0",
    events: [{ event_id: eventIds[0], path: "../outside.json" }],
  }),
  null,
);

const artifactRoot = resolve("test-results/static-provider-smoke");
await mkdir(artifactRoot, { recursive: true });

const symlinkFileRoot = await mkdtemp(join(artifactRoot, "file-symlink-"));
await copyFile(join(snapshotDir, "build-metadata.json"), join(symlinkFileRoot, "build-metadata.json"));
await symlink(join(snapshotDir, "active-target.json"), join(symlinkFileRoot, "active-target.json"));
const symlinkFileProvider = new StaticReplayDataProvider(symlinkFileRoot);
await assert.rejects(
  symlinkFileProvider.getActiveTarget(),
  /PUBLIC_STATIC_SNAPSHOT_SYMLINK_FORBIDDEN/,
);

const symlinkRootParent = await mkdtemp(join(artifactRoot, "root-symlink-"));
const symlinkRoot = join(symlinkRootParent, "snapshot");
await symlink(snapshotDir, symlinkRoot, "dir");
assert.throws(
  () => new StaticReplayDataProvider(symlinkRoot),
  /PUBLIC_STATIC_SNAPSHOT_ROOT_SYMLINK_FORBIDDEN/,
);

async function clonedSnapshot(prefix: string): Promise<string> {
  const parent = await mkdtemp(join(artifactRoot, prefix));
  const clone = join(parent, "snapshot");
  await cp(validatedSnapshotDir, clone, { recursive: true });
  return clone;
}

async function mutateActiveTarget(
  root: string,
  mutate: (target: Record<string, unknown>) => void,
): Promise<void> {
  const path = join(root, "active-target.json");
  const payload = JSON.parse(await readFile(path, "utf8")) as {
    data: { active_target: Record<string, unknown> };
  };
  mutate(payload.data.active_target);
  await writeFile(path, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

async function mutateJson(
  root: string,
  relativePath: string,
  mutate: (payload: Record<string, unknown>) => void,
): Promise<void> {
  const path = join(root, relativePath);
  const payload = JSON.parse(await readFile(path, "utf8")) as Record<string, unknown>;
  mutate(payload);
  await writeFile(path, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

const mismatchedTargetRoot = await clonedSnapshot("target-mismatch-");
await mutateActiveTarget(mismatchedTargetRoot, (target) => {
  const instrument = target.instrument as Record<string, unknown>;
  instrument.symbol = "000001.SZ";
});
assert.equal(
  (await new StaticReplayDataProvider(mismatchedTargetRoot).getPremarketBriefing()).error?.code,
  "invalid_payload",
);

const futureActivationRoot = await clonedSnapshot("future-activation-");
await mutateActiveTarget(futureActivationRoot, (target) => {
  target.activated_at = "2026-08-01T00:00:00Z";
});
assert.equal(
  (await new StaticReplayDataProvider(futureActivationRoot).getPremarketBriefing()).error?.code,
  "invalid_payload",
);

const invalidMetadataRoot = await clonedSnapshot("invalid-metadata-");
await mutateJson(invalidMetadataRoot, "build-metadata.json", (metadata) => {
  metadata.runtime_mode = "RUNTIME_API";
});
assert.equal(
  (await new StaticReplayDataProvider(invalidMetadataRoot).getActiveTarget()).error?.code,
  "invalid_payload",
);

const invalidProvenanceRoot = await clonedSnapshot("invalid-provenance-");
await mutateJson(invalidProvenanceRoot, "latest-briefing.json", (payload) => {
  const data = payload.data as Record<string, unknown>;
  const progress = data.ingestion_progress as Record<string, unknown>;
  const provenance = progress.provenance as Record<string, unknown>;
  provenance.quality_tier = "free_structured";
});
assert.equal(
  (await new StaticReplayDataProvider(invalidProvenanceRoot).getPremarketBriefing()).error?.code,
  "invalid_payload",
);

console.log("static provider smoke passed");
