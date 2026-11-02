import { createRequire } from "node:module";
import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2020 from "@redocly/ajv/dist/2020.js";
import { _ } from "@redocly/ajv/dist/compile/codegen/index.js";
import standaloneCode from "@redocly/ajv/dist/standalone/index.js";

const EXPECTED_AJV_VERSION = "8.11.2";
const GENERATED_HEADER = "// Generated from canonical Pydantic JSON Schemas. DO NOT EDIT.\n";
const scriptRoot = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptRoot, "..");
const repositoryRoot = resolve(webRoot, "../..");
const generatedRoot = resolve(webRoot, "lib/api/generated");
const modulePath = resolve(generatedRoot, "public-v12-validators.generated.cjs");
const declarationPath = resolve(generatedRoot, "public-v12-validators.generated.d.cts");
const formatsModulePath = resolve(generatedRoot, "public-v12-formats.generated.cjs");
const formatsDeclarationPath = resolve(generatedRoot, "public-v12-formats.generated.d.cts");
const require = createRequire(import.meta.url);

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

const RFC3339_DATE_TIME_RE =
  /^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[Zz]|([+-])(\d{2}):(\d{2}))$/;
const RFC3986_SCHEME_RE = /^[A-Za-z][A-Za-z0-9+.-]*$/;
const RFC3986_SEGMENT_RE =
  /^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2}|[!$&'()*+,;=:@])*$/;
const RFC3986_SEGMENT_NZ_RE =
  /^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2}|[!$&'()*+,;=:@])+$/;
const RFC3986_QUERY_OR_FRAGMENT_RE =
  /^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2}|[!$&'()*+,;=:@]|[/?])*$/;
const RFC3986_USERINFO_RE =
  /^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2}|[!$&'()*+,;=:])*$/;
const RFC3986_REG_NAME_RE =
  /^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2}|[!$&'()*+,;=])*$/;
const RFC3986_PORT_RE = /^[0-9]*$/;
const RFC3986_H16_RE = /^[0-9A-Fa-f]{1,4}$/;
const RFC3986_IPVFUTURE_RE =
  /^[Vv][0-9A-Fa-f]+\.(?:[A-Za-z0-9._~-]|[!$&'()*+,;=]|:)+$/;

function isRfc3339LeapYear(year) {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function rfc3339DaysInMonth(year, month) {
  const days = [0, 31, isRfc3339LeapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return days[month] ?? 0;
}

function validateRfc3339DateTime(value) {
  if (typeof value !== "string") return false;
  const match = RFC3339_DATE_TIME_RE.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = Number(match[8] ?? 0);
  const offsetMinute = Number(match[9] ?? 0);
  if (month < 1 || month > 12 || day < 1 || day > rfc3339DaysInMonth(year, month)) {
    return false;
  }
  if (hour > 23 || minute > 59 || offsetHour > 23 || offsetMinute > 59 || second > 60) {
    return false;
  }
  if (second < 60) return true;

  // RFC 3339 permits 60 only for a real UTC June/December leap-second position.
  const instant = new Date(0);
  instant.setUTCFullYear(year, month - 1, day);
  instant.setUTCHours(hour, minute, 59, 0);
  const offsetSign = match[7] === "-" ? -1 : 1;
  const offsetMinutes = match[7] ? offsetSign * (offsetHour * 60 + offsetMinute) : 0;
  instant.setTime(instant.getTime() - offsetMinutes * 60_000);
  return (
    instant.getUTCHours() === 23 &&
    instant.getUTCMinutes() === 59 &&
    (
      (instant.getUTCMonth() === 5 && instant.getUTCDate() === 30) ||
      (instant.getUTCMonth() === 11 && instant.getUTCDate() === 31)
    )
  );
}

function validateRfc3986Ipv4Address(value) {
  const octets = value.split(".");
  if (octets.length !== 4) return false;
  return octets.every((octet) => {
    if (!/^(?:0|[1-9][0-9]{0,2})$/.test(octet)) return false;
    return Number(octet) <= 255;
  });
}

function rfc3986Ipv6UnitCount(side, allowIpv4AtEnd) {
  if (side === "") return 0;
  const pieces = side.split(":");
  let units = 0;
  for (let index = 0; index < pieces.length; index += 1) {
    const piece = pieces[index];
    if (piece.includes(".")) {
      if (
        !allowIpv4AtEnd ||
        index !== pieces.length - 1 ||
        !validateRfc3986Ipv4Address(piece)
      ) {
        return -1;
      }
      units += 2;
    } else {
      if (!RFC3986_H16_RE.test(piece)) return -1;
      units += 1;
    }
  }
  return units;
}

function validateRfc3986Ipv6Address(value) {
  const compressionIndex = value.indexOf("::");
  if (compressionIndex === -1) {
    return rfc3986Ipv6UnitCount(value, true) === 8;
  }
  if (value.indexOf("::", compressionIndex + 2) !== -1) return false;

  const leftUnits = rfc3986Ipv6UnitCount(value.slice(0, compressionIndex), false);
  const rightUnits = rfc3986Ipv6UnitCount(value.slice(compressionIndex + 2), true);
  return leftUnits >= 0 && rightUnits >= 0 && leftUnits + rightUnits < 8;
}

function validateRfc3986Authority(value) {
  const atIndex = value.indexOf("@");
  let hostAndPort = value;
  if (atIndex !== -1) {
    if (atIndex !== value.lastIndexOf("@")) return false;
    if (!RFC3986_USERINFO_RE.test(value.slice(0, atIndex))) return false;
    hostAndPort = value.slice(atIndex + 1);
  }

  if (hostAndPort.startsWith("[")) {
    const closeIndex = hostAndPort.indexOf("]");
    if (closeIndex === -1 || hostAndPort.indexOf("]", closeIndex + 1) !== -1) {
      return false;
    }
    const ipLiteral = hostAndPort.slice(1, closeIndex);
    if (
      !validateRfc3986Ipv6Address(ipLiteral) &&
      !RFC3986_IPVFUTURE_RE.test(ipLiteral)
    ) {
      return false;
    }
    const portSuffix = hostAndPort.slice(closeIndex + 1);
    return portSuffix === "" || (
      portSuffix.startsWith(":") && RFC3986_PORT_RE.test(portSuffix.slice(1))
    );
  }

  if (hostAndPort.includes("[") || hostAndPort.includes("]")) return false;
  const colonIndex = hostAndPort.indexOf(":");
  if (colonIndex !== -1 && colonIndex !== hostAndPort.lastIndexOf(":")) return false;
  const host = colonIndex === -1 ? hostAndPort : hostAndPort.slice(0, colonIndex);
  const port = colonIndex === -1 ? "" : hostAndPort.slice(colonIndex + 1);
  return RFC3986_REG_NAME_RE.test(host) && RFC3986_PORT_RE.test(port);
}

function validateRfc3986HierPart(value) {
  if (value.startsWith("//")) {
    const pathIndex = value.indexOf("/", 2);
    const authority = pathIndex === -1 ? value.slice(2) : value.slice(2, pathIndex);
    const path = pathIndex === -1 ? "" : value.slice(pathIndex);
    if (!validateRfc3986Authority(authority)) return false;
    if (path === "") return true;
    return path.slice(1).split("/").every((segment) => RFC3986_SEGMENT_RE.test(segment));
  }

  if (value === "") return true;
  if (value.startsWith("/")) {
    if (value === "/") return true;
    const segments = value.slice(1).split("/");
    return (
      RFC3986_SEGMENT_NZ_RE.test(segments[0]) &&
      segments.slice(1).every((segment) => RFC3986_SEGMENT_RE.test(segment))
    );
  }

  const segments = value.split("/");
  return (
    RFC3986_SEGMENT_NZ_RE.test(segments[0]) &&
    segments.slice(1).every((segment) => RFC3986_SEGMENT_RE.test(segment))
  );
}

function validateAbsoluteRfc3986Uri(value) {
  if (typeof value !== "string") return false;
  const schemeEnd = value.indexOf(":");
  if (schemeEnd === -1 || !RFC3986_SCHEME_RE.test(value.slice(0, schemeEnd))) return false;

  let remainder = value.slice(schemeEnd + 1);
  const fragmentIndex = remainder.indexOf("#");
  if (fragmentIndex !== -1) {
    if (remainder.indexOf("#", fragmentIndex + 1) !== -1) return false;
    if (!RFC3986_QUERY_OR_FRAGMENT_RE.test(remainder.slice(fragmentIndex + 1))) return false;
    remainder = remainder.slice(0, fragmentIndex);
  }

  const queryIndex = remainder.indexOf("?");
  if (queryIndex !== -1) {
    if (!RFC3986_QUERY_OR_FRAGMENT_RE.test(remainder.slice(queryIndex + 1))) return false;
    remainder = remainder.slice(0, queryIndex);
  }
  return validateRfc3986HierPart(remainder);
}

const ajvPackage = await readJson(require.resolve("@redocly/ajv/package.json"));
if (ajvPackage.version !== EXPECTED_AJV_VERSION) {
  throw new Error(
    `PUBLIC_V12_AJV_VERSION_MISMATCH:${String(ajvPackage.version)}:${EXPECTED_AJV_VERSION}`,
  );
}

const briefingSchema = await readJson(resolve(
  repositoryRoot,
  "schemas/briefings/premarket-view.v1.2.schema.json",
));
const evidenceSchema = await readJson(resolve(
  repositoryRoot,
  "schemas/events/event-evidence-view.v1.2.schema.json",
));
if (typeof briefingSchema.$id !== "string" || typeof evidenceSchema.$id !== "string") {
  throw new Error("PUBLIC_V12_CANONICAL_SCHEMA_ID_REQUIRED");
}

const ajv = new Ajv2020({
  allErrors: true,
  code: {
    source: true,
    lines: true,
    formats: _`require("./public-v12-formats.generated.cjs")`,
  },
  discriminator: true,
  strict: true,
  // Pydantic's discriminator branches omit redundant object type annotations.
  strictTypes: false,
  validateFormats: true,
});
for (const annotation of [
  { keyword: "x-contract-model", schemaType: "string" },
  { keyword: "x-runtime-invariants", schemaType: "array" },
  { keyword: "x-schema-version", schemaType: "string" },
]) {
  ajv.addKeyword({ ...annotation, valid: true });
}
ajv.addFormat("date-time", validateRfc3339DateTime);
ajv.addFormat(
  "uuid",
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
);
ajv.addFormat("uri", validateAbsoluteRfc3986Uri);
ajv.addSchema(briefingSchema);
ajv.addSchema(evidenceSchema);

const rawStandaloneModule = standaloneCode(ajv, {
  validatePremarketBriefingV1_2: briefingSchema.$id,
  validateEventEvidenceV1_2: evidenceSchema.$id,
});
const upstreamRuntimeSpecifier = 'require("ajv/dist/runtime/ucs2length")';
if (!rawStandaloneModule.includes(upstreamRuntimeSpecifier)) {
  throw new Error("PUBLIC_V12_AJV_STANDALONE_RUNTIME_GUARD_FAILED");
}
// The locked Redocly fork emits its upstream package name in standalone code.
// Repoint that one guarded helper to the package that is actually installed.
const generatedModuleBody = rawStandaloneModule.replaceAll(
  upstreamRuntimeSpecifier,
  'require("@redocly/ajv/dist/runtime/ucs2length")',
).trimEnd();
const generatedModule = `${GENERATED_HEADER}${generatedModuleBody}\n`;
const generatedDeclaration = `${GENERATED_HEADER}import type { components } from "../openapi.generated";

export type PublicV12SchemaValidationError = Readonly<{
  instancePath: string;
  schemaPath: string;
  keyword: string;
  params: Readonly<Record<string, unknown>>;
  message?: string;
}>;

export interface PublicV12SchemaValidator<T> {
  (value: unknown): value is T;
  errors?: readonly PublicV12SchemaValidationError[] | null;
}

export const validatePremarketBriefingV1_2: PublicV12SchemaValidator<
  components["schemas"]["PremarketBriefingViewV1_2"]
>;
export const validateEventEvidenceV1_2: PublicV12SchemaValidator<
  components["schemas"]["EventEvidenceViewV1_2"]
>;
`;
const generatedFormatsModule = `${GENERATED_HEADER}"use strict";
const RFC3339_DATE_TIME_RE = ${RFC3339_DATE_TIME_RE};
const RFC3986_SCHEME_RE = ${RFC3986_SCHEME_RE};
const RFC3986_SEGMENT_RE = ${RFC3986_SEGMENT_RE};
const RFC3986_SEGMENT_NZ_RE = ${RFC3986_SEGMENT_NZ_RE};
const RFC3986_QUERY_OR_FRAGMENT_RE = ${RFC3986_QUERY_OR_FRAGMENT_RE};
const RFC3986_USERINFO_RE = ${RFC3986_USERINFO_RE};
const RFC3986_REG_NAME_RE = ${RFC3986_REG_NAME_RE};
const RFC3986_PORT_RE = ${RFC3986_PORT_RE};
const RFC3986_H16_RE = ${RFC3986_H16_RE};
const RFC3986_IPVFUTURE_RE = ${RFC3986_IPVFUTURE_RE};

${isRfc3339LeapYear}

${rfc3339DaysInMonth}

${validateRfc3339DateTime}

${validateRfc3986Ipv4Address}

${rfc3986Ipv6UnitCount}

${validateRfc3986Ipv6Address}

${validateRfc3986Authority}

${validateRfc3986HierPart}

${validateAbsoluteRfc3986Uri}

module.exports = Object.freeze({
  "date-time": validateRfc3339DateTime,
  uri: validateAbsoluteRfc3986Uri,
});
`;
const generatedFormatsDeclaration = `${GENERATED_HEADER}export type PublicV12FormatValidator = (
  value: string,
) => boolean;

declare const publicV12Formats: Readonly<{
  "date-time": PublicV12FormatValidator;
  uri: PublicV12FormatValidator;
}>;

export = publicV12Formats;
`;

const outputs = [
  [modulePath, generatedModule],
  [declarationPath, generatedDeclaration],
  [formatsModulePath, generatedFormatsModule],
  [formatsDeclarationPath, generatedFormatsDeclaration],
];
if (process.argv.slice(2).includes("--check")) {
  const drifted = [];
  for (const [path, expected] of outputs) {
    let actual = null;
    try {
      actual = await readFile(path, "utf8");
    } catch {
      // Missing generated output is reported as drift below.
    }
    if (actual !== expected) drifted.push(path.slice(webRoot.length + 1));
  }
  if (drifted.length > 0) {
    throw new Error(`PUBLIC_V12_VALIDATOR_DRIFT:${drifted.join(",")}`);
  }
  console.log(`public V1.2 validator drift check passed with @redocly/ajv ${EXPECTED_AJV_VERSION}`);
} else {
  await Promise.all(outputs.map(([path, content]) => writeFile(path, content, "utf8")));
  console.log(`generated public V1.2 validators with @redocly/ajv ${EXPECTED_AJV_VERSION}`);
}
