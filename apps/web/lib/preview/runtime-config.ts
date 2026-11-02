import type { components } from "@/lib/api/openapi.generated";

export type RuntimeWebConfig = components["schemas"]["RuntimeWebConfig"] | components["schemas"]["RuntimeRealWebConfig"];

export const RUNTIME_CONFIG_PATH = "/runtime-config.json";

let configPromise: Promise<RuntimeWebConfig> | null = null;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function httpsUrl(value: unknown): URL | null {
  if (typeof value !== "string") return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && !parsed.username && !parsed.password ? parsed : null;
  } catch {
    return null;
  }
}

function isRuntimeWebConfig(value: unknown): value is RuntimeWebConfig {
  if (!isRecord(value)) return false;
  const issuer = httpsUrl(value.issuer);
  const authorization = httpsUrl(value.authorization_endpoint);
  const token = httpsUrl(value.token_endpoint);
  const endSession = httpsUrl(value.end_session_endpoint);
  if (!issuer || !authorization || !token || !endSession) return false;
  const issuerHost = /^cognito-idp\.([a-z0-9-]+)\.amazonaws\.com$/.exec(issuer.hostname);
  if (!issuerHost) return false;
  const region = issuerHost[1];
  const poolPattern = new RegExp(`^/${region}_[A-Za-z0-9]+$`);
  const hostedDomain = new RegExp(
    `^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.auth\\.${region}\\.amazoncognito\\.com$`,
  );
  const identityEndpointsAreBound =
    poolPattern.test(issuer.pathname) &&
    !issuer.search &&
    !issuer.hash &&
    hostedDomain.test(authorization.hostname) &&
    authorization.origin === token.origin &&
    authorization.origin === endSession.origin &&
    authorization.pathname === "/oauth2/authorize" &&
    token.pathname === "/oauth2/token" &&
    endSession.pathname === "/logout" &&
    [authorization, token, endSession].every((endpoint) => !endpoint.search && !endpoint.hash);
  return (
    (value.schema_version === "1.0" || value.schema_version === "1.1") &&
    identityEndpointsAreBound &&
    typeof value.client_id === "string" &&
    /^[A-Za-z0-9]{1,128}$/.test(value.client_id) &&
    typeof value.scope === "string" &&
    value.scope.startsWith("openid") &&
    value.api_base_path === "/api/v2" &&
    typeof value.product_sha === "string" &&
    /^[a-f0-9]{40}$/.test(value.product_sha) &&
    ((value.schema_version === "1.0" && value.environment_label === "私有合成 R2 预览" && value.synthetic_data === true) ||
     (value.schema_version === "1.1" && value.environment_label === "真实资料私人研究 · DEV_ONLY / UNVERIFIED" && value.synthetic_data === false))
  );
}

async function fetchRuntimeConfig(): Promise<RuntimeWebConfig> {
  const response = await fetch(RUNTIME_CONFIG_PATH, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("RUNTIME_CONFIG_UNAVAILABLE");
  const payload: unknown = await response.json();
  if (!isRuntimeWebConfig(payload)) throw new Error("RUNTIME_CONFIG_INVALID");
  return Object.freeze(payload);
}

export function loadRuntimeConfig(): Promise<RuntimeWebConfig> {
  configPromise ??= fetchRuntimeConfig();
  return configPromise;
}

export function resetRuntimeConfigForTests(): void {
  configPromise = null;
}
