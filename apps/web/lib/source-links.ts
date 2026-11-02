import type { components } from "./api/openapi.generated";

type SourceProvenance =
  | components["schemas"]["SourceProvenance"]
  | components["schemas"]["SourceProvenanceV1_2"];

export function safeExternalSourceUrl(
  value: string,
  provenance: SourceProvenance | null,
): string | null {
  if (
    provenance?.runtime_mode !== "authorized" ||
    provenance.release_tier !== "authorized" ||
    provenance.rights_status !== "approved"
  ) {
    return null;
  }
  try {
    const url = new URL(value);
    const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
    if (hostname === "invalid" || hostname.endsWith(".invalid")) {
      return null;
    }
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}
