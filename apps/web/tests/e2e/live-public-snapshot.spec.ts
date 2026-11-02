import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import {
  isAllowedPublicStaticRequest,
  PUBLIC_STATIC_TEST_ORIGIN,
} from "../public-static-network-policy.ts";

type Scenario = "complete" | "partial" | "stale" | "unavailable" | "all-degraded";
type InvalidScenario =
  | "invalid-nested-extra"
  | "invalid-missing-required"
  | "invalid-pattern"
  | "invalid-limit"
  | "invalid-format"
  | "invalid-date-time"
  | "invalid-uri";
type GuardOptions = Readonly<{
  scenario?: Scenario | InvalidScenario;
  delayCurrentMs?: number;
  failCurrentOnce?: boolean;
  tamperManifestHash?: boolean;
}>;

function scenarioPointerPath(scenario: Scenario | InvalidScenario): string {
  return resolve(
    process.cwd(),
    ".next/public-static-test-pointers",
    `${scenario}.json`,
  );
}

async function guardNetwork(page: Page, options: GuardOptions = {}) {
  const violations: string[] = [];
  const dataRequests: string[] = [];
  let currentRequests = 0;
  await page.route("**/*", async (route) => {
    const request = route.request();
    const descriptor = {
      url: request.url(),
      resourceType: request.resourceType(),
      navigation: request.isNavigationRequest(),
    };
    if (!isAllowedPublicStaticRequest(descriptor)) {
      violations.push(request.url());
      await route.abort("blockedbyclient");
      return;
    }
    const url = new URL(request.url());
    if (request.resourceType() === "fetch" && url.pathname.startsWith("/data/")) {
      dataRequests.push(url.pathname);
    }
    if (url.pathname === "/data/current.json" && request.resourceType() === "fetch") {
      currentRequests += 1;
      if (options.failCurrentOnce && currentRequests === 1) {
        await route.abort("failed");
        return;
      }
      if (options.delayCurrentMs) {
        await new Promise((resolveDelay) => setTimeout(resolveDelay, options.delayCurrentMs));
      }
      if (options.scenario || options.tamperManifestHash) {
        const pointerPath = scenarioPointerPath(options.scenario ?? "complete");
        if (options.tamperManifestHash) {
          const pointer = JSON.parse(await readFile(pointerPath, "utf8")) as Record<string, unknown>;
          pointer.manifest_sha256 = "0".repeat(64);
          await route.fulfill({
            body: `${JSON.stringify(pointer)}\n`,
            contentType: "application/json",
            status: 200,
          });
          return;
        }
        await route.fulfill({
          path: pointerPath,
          contentType: "application/json",
          status: 200,
        });
        return;
      }
    }
    await route.continue();
  });
  return { violations, dataRequests };
}

async function waitForPhase(page: Page, phase: Scenario | "live" | "error") {
  await expect(page.locator(`[data-snapshot-phase="${phase}"]`).first()).toBeVisible();
}

async function expectGaugeReadingsClearNeedleCaps(page: Page) {
  const measurements = await page.locator("[data-change-gauge]").evaluateAll((gauges) =>
    gauges.flatMap((gauge) => {
      const cap = gauge.querySelector<SVGCircleElement>(".gauge-needle-cap");
      const reading = gauge.querySelector<HTMLOutputElement>(".gauge-reading");
      if (!cap || !reading) return [];
      const capBounds = cap.getBoundingClientRect();
      const readingBounds = reading.getBoundingClientRect();
      const readingStyle = getComputedStyle(reading);
      return [{
        gap: readingBounds.top - capBounds.bottom,
        capRadius: cap.getAttribute("r"),
        background: readingStyle.backgroundColor,
        fontWeight: Number(readingStyle.fontWeight),
      }];
    }),
  );
  expect(measurements.length).toBeGreaterThan(0);
  expect(measurements.every((item) => item.gap >= 4)).toBe(true);
  expect(measurements.every((item) => item.capRadius === "6")).toBe(true);
  expect(measurements.every((item) => item.background !== "rgba(0, 0, 0, 0)")).toBe(true);
  expect(measurements.every((item) => item.fontWeight >= 600)).toBe(true);
}

test("loads a verified same-origin bundle and exposes its public freshness", async ({ page }, testInfo) => {
  const network = await guardNetwork(page);
  await page.goto("/");
  await waitForPhase(page, "live");

  await expect(page.getByText("最新快照可用", { exact: true })).toBeVisible();
  await expect(page.getByText(/DEV_LIVE \/ DEV_ONLY \/ UNVERIFIED \/ DISCOVERY/).first()).toBeVisible();
  await expect(page.locator(".snapshot-metadata").getByText("free_official_unverified / free_structured_unverified / dev_web_unverified")).toBeVisible();
  for (const label of ["data_as_of", "published_at", "fresh_until", "last_success", "来源等级", "运行状态"]) {
    await expect(page.locator(".snapshot-metadata").getByText(label, { exact: true })).toBeVisible();
  }
  expect(network.dataRequests[0]).toBe("/data/current.json");
  expect(network.dataRequests).toContain(
    "/data/snapshots/71000000-0000-4000-8000-000000000010/manifest.json",
  );
  expect(network.dataRequests.some((path) => path.endsWith("/briefing.json"))).toBe(true);
  expect(network.dataRequests.some((path) => path.endsWith("/run-status.json"))).toBe(true);
  expect(network.dataRequests.filter((path) => path.includes("/evidence/"))).toHaveLength(2);
  expect(network.violations).toEqual([]);

  const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute("content");
  expect(csp).toContain("connect-src 'self'");
  expect(csp).not.toMatch(/https?:/);
  await expectGaugeReadingsClearNeedleCaps(page);
  await page.screenshot({ path: testInfo.outputPath("overview.png"), fullPage: true });
});

test("keeps the historical replay visible while the current pointer is loading", async ({ page }) => {
  const network = await guardNetwork(page, { delayCurrentMs: 500 });
  await page.goto("/");
  await expect(page.locator('[data-snapshot-phase="loading"]').first()).toBeVisible();
  await expect(page.getByText(/历史演示数据：五类结果来自/).first()).toBeVisible();
  await waitForPhase(page, "live");
  expect(network.violations).toEqual([]);
});

for (const scenario of ["partial", "stale", "unavailable"] as const) {
  test(`renders the ${scenario} snapshot state without inventing data`, async ({ page }) => {
    const network = await guardNetwork(page, { scenario });
    await page.goto("/");
    await waitForPhase(page, scenario);
    const gauges = page.locator("[data-change-gauge]");
    await expect(gauges).toHaveCount(5);

    if (scenario === "partial") {
      await expect(page.getByText("部分数据可用", { exact: true })).toBeVisible();
      await expect(gauges.nth(0)).toHaveAttribute("data-value", "1");
      await expect(gauges.nth(4)).toHaveAttribute("data-value", "unavailable");
    } else if (scenario === "stale") {
      await expect(page.getByText("数据已标旧", { exact: true })).toBeVisible();
      await expect(gauges.nth(0)).toHaveAttribute("data-value", "1");
      await expect(gauges.nth(4)).toHaveAttribute("data-value", "0");
    } else {
      await expect(page.getByText("当前五类均失败 · 历史体验可用", { exact: true })).toBeVisible();
      for (const [index, value] of [1, 1, 0, 0, 0].entries()) {
        await expect(gauges.nth(index)).toHaveAttribute("data-value", String(value));
      }
      await expect(page.getByText(/DEV_ONLY 完整体验/).first()).toBeVisible();
    }
    expect(network.violations).toEqual([]);
  });
}

test("accepts a v1.1 all-failed degraded run without inventing healthy sources", async ({ page }) => {
  const network = await guardNetwork(page, { scenario: "all-degraded" });
  await page.goto("/");
  await waitForPhase(page, "partial");
  await expect(page.getByText("当前五类均失败 · 历史体验可用", { exact: true })).toBeVisible();
  await expect(page.locator(".snapshot-metadata").getByText("degraded", { exact: true })).toBeVisible();
  await expect(page.locator("[data-snapshot-phase=\"partial\"]").first()).toContainText(
    "当前来源没有形成可用结果",
  );
  const currentDataAsOf = await page.locator(".snapshot-metadata > div").filter({ hasText: "data_as_of" }).locator("time").getAttribute("datetime");
  const currentLastSuccess = await page.locator(".snapshot-metadata > div").filter({ hasText: "last_success" }).locator("time").getAttribute("datetime");
  expect(currentDataAsOf).not.toBeNull();
  expect(currentLastSuccess).not.toBeNull();
  expect(Date.parse(currentLastSuccess ?? "")).toBeLessThan(Date.parse(currentDataAsOf ?? ""));
  await expect(page.locator("[data-historical-experience-fallback]")).toBeVisible();
  const gauges = page.locator("[data-change-gauge]");
  for (const [index, value] of [1, 1, 0, 0, 0].entries()) {
    await expect(gauges.nth(index)).toHaveAttribute("data-value", String(value));
  }
  await expect(page.getByText(/五类仪表已切换为完整历史体验/)).toBeVisible();

  await page.goto("/details/");
  await waitForPhase(page, "partial");
  await expect(page.getByText(/本区为同一份历史演示数据/)).toBeVisible();
  await expect(page.locator("#changes .change-group")).toHaveCount(5);
  const historicalDataAsOf = await page.locator("#changes .section-intro time").last().getAttribute("datetime");
  expect(historicalDataAsOf).not.toBe(currentDataAsOf);
  await expect(page.locator("#sources .source-state")).toHaveCount(6);
  await expect(page.locator("#sources .source-state")).toHaveText([
    "运行失败",
    "运行失败",
    "运行失败",
    "运行失败",
    "运行失败",
    "运行失败",
  ]);
  await expect(page.locator("#sources").getByText("SOURCE_UNAVAILABLE", { exact: true })).toHaveCount(6);
  await expect(page.locator("#sources").getByText("暂无成功记录", { exact: true })).toHaveCount(0);
  await expect(page.locator("#sources").getByText("70000000-0000-4000-8000-000000000014", { exact: true })).toHaveCount(6);
  await expect(page.getByText("历史演示在该窗口返回 0 条变化。")).toHaveCount(3);
  await page.getByRole("link", { name: /查看历史回放证据/ }).first().click();
  await expect(page).toHaveURL(/\/evidence\/10000000-0000-4000-8000-000000000001\/$/);
  await expect(page.getByRole("heading", { name: "更正关系" })).toBeVisible();
  expect(network.dataRequests.filter((path) => path.includes("/evidence/")).length).toBe(0);
  expect(network.violations).toEqual([]);
});

test("rejects a tampered manifest and safely retries the last historical view", async ({ page }) => {
  const network = await guardNetwork(page, { tamperManifestHash: true });
  await page.goto("/");
  await waitForPhase(page, "error");
  await expect(page.getByText("MANIFEST_HASH_MISMATCH", { exact: true })).toBeVisible();
  await expect(page.getByText(/历史演示数据：五类结果来自/).first()).toBeVisible();
  await expect(page.locator("[data-change-gauge]").first()).toHaveAttribute("data-value", "1");
  expect(network.violations).toEqual([]);
});

for (const scenario of [
  "invalid-nested-extra",
  "invalid-missing-required",
  "invalid-pattern",
  "invalid-limit",
  "invalid-format",
  "invalid-date-time",
  "invalid-uri",
] as const) {
  test(`rejects the hash-valid ${scenario} bundle at the contract boundary`, async ({ page }) => {
    const network = await guardNetwork(page, { scenario });
    await page.goto("/");
    await waitForPhase(page, "error");
    await expect(page.getByText("INVALID_ASSET", { exact: true })).toBeVisible();
    await expect(page.getByText(/历史演示数据：五类结果来自/).first()).toBeVisible();
    await expect(page.locator("[data-change-gauge]").first()).toHaveAttribute("data-value", "1");
    expect(network.dataRequests.some((path) => path.endsWith("/manifest.json"))).toBe(true);
    expect(network.dataRequests.some((path) => path.endsWith("/briefing.json"))).toBe(true);
    expect(network.violations).toEqual([]);
  });
}

test("recovers after a transient current pointer failure", async ({ page }) => {
  const network = await guardNetwork(page, { failCurrentOnce: true });
  await page.goto("/");
  await waitForPhase(page, "error");
  await expect(page.getByText("CURRENT_UNAVAILABLE", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新核验最新快照" }).click();
  await waitForPhase(page, "live");
  expect(network.violations).toEqual([]);
});

test("keeps overview and detail layouts inside a 390px viewport", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const network = await guardNetwork(page);
  for (const path of ["/", "/details/"]) {
    await page.goto(path);
    await waitForPhase(page, "live");
    const widths = await page.evaluate(() => ({
      body: document.body.scrollWidth,
      document: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
    }));
    expect(widths.body).toBeLessThanOrEqual(widths.viewport);
    expect(widths.document).toBeLessThanOrEqual(widths.viewport);
    if (path === "/") {
      await expectGaugeReadingsClearNeedleCaps(page);
      await page.screenshot({ path: testInfo.outputPath("overview-390.png"), fullPage: true });
    }
  }
  await page.screenshot({ path: testInfo.outputPath("details-390.png"), fullPage: true });
  expect(network.violations).toEqual([]);
});

test("never reaches third parties or an application API", async ({ page }) => {
  const network = await guardNetwork(page);
  await page.goto("/details/");
  await waitForPhase(page, "live");
  expect(network.violations).toEqual([]);
  expect(network.dataRequests.every((path) =>
    path === "/data/current.json" || path.startsWith("/data/snapshots/"),
  )).toBe(true);
  expect(network.dataRequests.every((path) => !path.split("/").includes("api"))).toBe(true);
  expect(network.dataRequests.length).toBeGreaterThan(2);
  expect(new URL(page.url()).origin).toBe(PUBLIC_STATIC_TEST_ORIGIN);
});
