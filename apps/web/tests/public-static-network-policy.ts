export const PUBLIC_STATIC_TEST_ORIGIN = "http://127.0.0.1:4173";

export type StaticRequestDescriptor = {
  url: string;
  resourceType: string;
  navigation: boolean;
};

const LOCAL_ASSET_TYPES = new Set(["font", "image", "script", "stylesheet"]);
const SNAPSHOT_DOCUMENT_PATH = /^\/data\/snapshots\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/(?:manifest|briefing|evidence-index|run-status)\.json$/i;
const SNAPSHOT_EVIDENCE_PATH = /^\/data\/snapshots\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/evidence\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json$/i;

function isAllowedSnapshotFetch(url: URL): boolean {
  if (url.search !== "" || url.hash !== "") return false;
  return (
    url.pathname === "/data/current.json" ||
    SNAPSHOT_DOCUMENT_PATH.test(url.pathname) ||
    SNAPSHOT_EVIDENCE_PATH.test(url.pathname)
  );
}

export function isAllowedPublicStaticRequest(request: StaticRequestDescriptor): boolean {
  const url = new URL(request.url);
  if (url.origin !== PUBLIC_STATIC_TEST_ORIGIN) return false;
  if (url.pathname.split("/").includes(["a", "p", "i"].join(""))) return false;
  if (request.resourceType === "xhr") return false;
  if (request.resourceType === "fetch") return !request.navigation && isAllowedSnapshotFetch(url);
  if (request.navigation) return request.resourceType === "document";
  return LOCAL_ASSET_TYPES.has(request.resourceType) && url.pathname.startsWith("/_next/static/");
}
