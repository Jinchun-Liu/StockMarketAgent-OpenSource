import { isAbsolute, resolve } from "node:path";

const apiInternalUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
const publicStaticBuild = process.env.STOCK_ASSISTANT_PUBLIC_STATIC === "1";
const r2PreviewStaticBuild = process.env.STOCK_ASSISTANT_R2_PREVIEW_STATIC === "1";
const runtimeMode = process.env.NEXT_PUBLIC_RUNTIME_MODE ?? "RUNTIME_API";
const configuredSnapshotDir = process.env.STOCK_ASSISTANT_PUBLIC_SNAPSHOT_DIR;

if (publicStaticBuild && runtimeMode !== "PUBLIC_STATIC") {
  throw new Error("PUBLIC_STATIC_MODE_REQUIRED");
}
if (r2PreviewStaticBuild && runtimeMode !== "R2_PRIVATE_PREVIEW") {
  throw new Error("R2_PRIVATE_PREVIEW_MODE_REQUIRED");
}
if (publicStaticBuild && r2PreviewStaticBuild) {
  throw new Error("STATIC_BUILD_MODES_MUTUALLY_EXCLUSIVE");
}
if (!publicStaticBuild && runtimeMode === "PUBLIC_STATIC") {
  throw new Error("PUBLIC_STATIC_BUILD_REQUIRED");
}
if (publicStaticBuild && (!configuredSnapshotDir || !isAbsolute(configuredSnapshotDir))) {
  throw new Error("PUBLIC_STATIC_SNAPSHOT_DIR_ABSOLUTE_REQUIRED");
}

export const isPublicStaticBuild = publicStaticBuild;
export const isR2PreviewStaticBuild = r2PreviewStaticBuild;

export const serverConfig = Object.freeze({
  apiInternalUrl: apiInternalUrl.replace(/\/$/, ""),
  runtimeMode,
  publicSnapshotDir: configuredSnapshotDir ? resolve(configuredSnapshotDir) : null,
});
