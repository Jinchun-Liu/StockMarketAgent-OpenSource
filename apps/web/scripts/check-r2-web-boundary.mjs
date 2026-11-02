import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const projectRoot = resolve(import.meta.dirname, "..");
const entrypoints = [
  "app/layout.tsx",
  "app/page.tsx",
  "app/research/page.tsx",
  "app/history/page.tsx",
  "app/auth/callback/page.tsx",
  "app/not-found.tsx",
  "app/details/page.tsx",
  "app/evidence/current/page.tsx",
  "app/evidence/[eventId]/page.tsx",
];
const forbidden = [
  "_components/",
  "globals.css",
  "redesign.css",
  "research.css",
  "research-workspace",
];

const violations = [];
for (const entrypoint of entrypoints) {
  const source = readFileSync(resolve(projectRoot, entrypoint), "utf8");
  for (const marker of forbidden) {
    if (source.includes(marker)) violations.push(`${entrypoint}: ${marker}`);
  }
}

if (violations.length > 0) {
  throw new Error(`R2 Web boundary violation:\n${violations.join("\n")}`);
}

console.log("R2 Web entrypoints are isolated from legacy UI and CSS");
