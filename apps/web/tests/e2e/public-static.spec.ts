import { expect, test, type Page } from "@playwright/test";

import { getGaugeDynamics } from "../../app/_components/instrument-gauge";
import {
  BRIEFING_SECTIONS,
  developmentModeBanner,
  hasSourceCoverageGap,
  safeGapReason,
} from "../../app/_components/presentation";

import {
  isAllowedPublicStaticRequest,
  PUBLIC_STATIC_TEST_ORIGIN,
} from "../public-static-network-policy.ts";

const correctionId = "10000000-0000-4000-8000-000000000001";
const contradictionId = "10000000-0000-4000-8000-000000000002";
const sourceIds = [
  "gov_cn",
  "financial_dev",
  "market_dev",
  "company_dev",
  "miit_gov_cn",
] as const;

async function waitForSnapshot(page: Page, phase = "live") {
  await expect(page.locator(`[data-snapshot-phase="${phase}"]`).first()).toBeVisible();
}

async function guardNetwork(page: Page) {
  const violations: string[] = [];
  await page.route("**/*", async (route) => {
    const request = route.request();
    if (
      !isAllowedPublicStaticRequest({
        url: request.url(),
        resourceType: request.resourceType(),
        navigation: request.isNavigationRequest(),
      })
    ) {
      violations.push(request.url());
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  return violations;
}

test("renders the fixed target and five change-count instruments", async ({ page }) => {
  const violations = await guardNetwork(page);
  await page.goto("/");
  await waitForSnapshot(page);
  await expect(page.getByRole("heading", { name: "本次简报，有多少信息真正发生了变化？" })).toBeVisible();
  await expect(page.getByRole("link", { name: "概览", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("link", { name: "数据与审计", exact: true })).toBeVisible();
  await expect(page.getByText("兆易创新").first()).toBeVisible();
  await expect(page.getByText("603986.SH").first()).toBeVisible();
  for (const section of ["法定公告", "财务变化", "行情快照", "公司与行业新闻", "相关宏观快讯"]) {
    await expect(page.getByRole("heading", { name: section })).toBeVisible();
  }
  const gauges = page.locator("[data-change-gauge]");
  await expect(gauges).toHaveCount(5);
  await expect(gauges.nth(0)).toHaveAttribute("data-value", "1");
  await expect(gauges.nth(1)).toHaveAttribute("data-value", "1");
  await expect(gauges.nth(2)).toHaveAttribute("data-value", "0");
  await expect(gauges.nth(3)).toHaveAttribute("data-value", "0");
  await expect(gauges.nth(4)).toHaveAttribute("data-value", "0");
  await expect(page.getByText(/DEV_LIVE \/ DEV_ONLY \/ UNVERIFIED \/ DISCOVERY/).first()).toBeVisible();
  await expect(page.getByText(/不构成证券事实、投资建议/).first()).toBeVisible();
  expect(violations).toEqual([]);
});

test("explains a gauge on hover, focus and tap", async ({ page }, testInfo) => {
  const violations = await guardNetwork(page);
  await page.goto("/");
  await waitForSnapshot(page);
  const explanationButton = page.getByRole("button", { name: "了解法定公告变化条数" });
  const tooltip = page.getByRole("tooltip", { name: /法定公告统计说明/ });
  await expect(explanationButton).toHaveAttribute("aria-describedby", /tooltip/);
  const hitArea = await explanationButton.boundingBox();
  expect(hitArea?.width).toBeGreaterThanOrEqual(44);
  expect(hitArea?.height).toBeGreaterThanOrEqual(44);
  if (testInfo.project.name === "mobile-chrome") {
    await explanationButton.tap();
    await expect(tooltip).toBeVisible();
    const tooltipBounds = await tooltip.boundingBox();
    const viewportWidth = await page.evaluate(() => window.innerWidth);
    expect(tooltipBounds?.x).toBeGreaterThanOrEqual(0);
    expect((tooltipBounds?.x ?? 0) + (tooltipBounds?.width ?? 0)).toBeLessThanOrEqual(viewportWidth);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewportWidth);
    await explanationButton.tap();
    await expect(tooltip).toBeHidden();
  } else {
    await explanationButton.hover();
    await expect(tooltip).toBeVisible();
    await page.mouse.move(0, 0);
    await expect(tooltip).toBeHidden();
    await explanationButton.focus();
    await expect(tooltip).toBeVisible();
    await explanationButton.press("Escape");
    await expect(tooltip).toBeHidden();
    await explanationButton.click();
    await expect(tooltip).toBeVisible();
    await page.getByRole("heading", { name: "五类变化一眼看懂" }).click();
    await expect(tooltip).toBeHidden();
  }
  expect(violations).toEqual([]);
});

test("top-level navigation uses offline document transitions", async ({ page }) => {
  const violations = await guardNetwork(page);
  await page.goto("/");
  await waitForSnapshot(page);
  await page.getByRole("link", { name: "数据与审计", exact: true }).click();
  await expect(page).toHaveURL(/\/details\/$/);
  await waitForSnapshot(page);
  await expect(page.getByRole("heading", { name: "数据、审计与计算依据" })).toBeVisible();
  await page.getByRole("link", { name: "概览", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await waitForSnapshot(page);
  expect(violations).toEqual([]);
});

test("separates detailed data, audit trail and methodology", async ({ page }) => {
  const violations = await guardNetwork(page);
  await page.goto("/details/");
  await waitForSnapshot(page);
  await expect(page.getByRole("link", { name: "数据与审计", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "数据、审计与计算依据" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "五类变化明细" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "来源状态" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "脱敏运行日志" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "数字是怎样得到的" })).toBeVisible();
  await expect(page.locator(".source-status-card")).toHaveCount(5);
  for (const sourceId of sourceIds) {
    await expect(page.getByRole("heading", { name: sourceId, exact: true })).toBeVisible();
  }
  await expect(page.getByText("free_official_unverified", { exact: true })).toHaveCount(2);
  await expect(page.getByText("free_structured_unverified", { exact: true })).toHaveCount(2);
  await expect(page.getByText("dev_web_unverified", { exact: true })).toHaveCount(1);
  await expect(page.locator("#changes").getByText("CORRECTION", { exact: true }).first()).toBeVisible();
  await expect(page.locator("#changes").getByText("CONTRADICTION", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("当前已校验返回 0 条变化。")).toHaveCount(3);
  await expect(page.getByRole("link", { name: /查看同页证据摘要/ }).first()).toHaveAttribute(
    "href",
    `/details/#event-${correctionId}`,
  );
  await expect(page.locator(".audit-ledger")).not.toContainText("cursor");
  await expect(page.locator(".audit-ledger")).not.toContainText("stack");
  expect(violations).toEqual([]);
});

test("serves the static not-found page", async ({ page }) => {
  const violations = await guardNetwork(page);
  const unknownId = "90000000-0000-4000-8000-000000000099";
  const response = await page.goto(`/evidence/${unknownId}/`);
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "未找到这条静态证据" })).toBeVisible();
  expect(violations).toEqual([]);
});

test("mobile viewport remains usable", async ({ page }) => {
  const violations = await guardNetwork(page);
  await page.goto("/");
  await waitForSnapshot(page);
  await expect(page.getByRole("main")).toBeVisible();
  const layout = await page.evaluate(() => ({
    bodyWidth: document.body.scrollWidth,
    documentWidth: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
  }));
  expect(layout.bodyWidth).toBeLessThanOrEqual(layout.viewport);
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewport);
  const gaugeBounds = await page.locator("[data-change-gauge]").evaluateAll((gauges) => gauges.map((gauge) => {
    const bounds = gauge.getBoundingClientRect();
    return { left: bounds.left, right: bounds.right };
  }));
  expect(gaugeBounds.every((bounds) => bounds.left >= 0 && bounds.right <= layout.viewport)).toBe(true);
  const skipLink = page.getByRole("link", { name: "跳到主要内容" });
  await skipLink.focus();
  await expect(skipLink).toBeFocused();
  await page.goto("/details/");
  await waitForSnapshot(page);
  const detailsLayout = await page.evaluate(() => ({
    bodyWidth: document.body.scrollWidth,
    documentWidth: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
  }));
  expect(detailsLayout.bodyWidth).toBeLessThanOrEqual(detailsLayout.viewport);
  expect(detailsLayout.documentWidth).toBeLessThanOrEqual(detailsLayout.viewport);
  await expect(page.getByRole("heading", { name: "数据、审计与计算依据" })).toBeVisible();
  expect(violations).toEqual([]);
});

test("reduced motion keeps values readable and disables needle motion", async ({ page }) => {
  const violations = await guardNetwork(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await waitForSnapshot(page);
  const motionState = await page.locator("[data-change-gauge]").evaluateAll((gauges) =>
    gauges.map((gauge) => {
      const sweep = gauge.querySelector<SVGElement>(".gauge-needle-sweep");
      const jitter = gauge.querySelector<SVGElement>(".gauge-needle-jitter");
      return {
        sweep: sweep ? getComputedStyle(sweep).animationName : null,
        jitter: jitter ? getComputedStyle(jitter).animationName : null,
        transform: sweep ? getComputedStyle(sweep).transform : null,
      };
    }),
  );
  expect(motionState.every((state) => state.sweep === "none" && state.jitter === "none")).toBe(true);
  expect(motionState.every((state) => state.transform !== null && state.transform !== "none")).toBe(true);
  await expect(page.locator("[data-change-gauge]").first().locator(".gauge-reading")).toContainText("1");
  expect(violations).toEqual([]);
});

test("deep links expose correction, contradiction and revision without query state", async ({ page }) => {
  const violations = await guardNetwork(page);
  await page.goto(`/evidence/${correctionId}/`);
  await expect(page).toHaveURL(new RegExp(`/evidence/${correctionId}/$`));
  await expect(page.getByRole("heading", { name: "更正关系" })).toBeVisible();
  await expect(page.getByText(/10000000 更正为 12000000/)).toBeVisible();
  await expect(page.locator(".revision-timeline").getByText("revision 2", { exact: true })).toBeVisible();
  await expect(page.locator(".revision-timeline").getByText("revision 1", { exact: true })).toBeVisible();
  await expect(page.getByText("synthetic-public-demo", { exact: true })).toHaveCount(2);
  await expect(page.getByText("fixture", { exact: true })).toHaveCount(2);
  const evidenceRuntime = page.locator(".evidence-runtime");
  await expect(evidenceRuntime.getByText("PUBLIC_STATIC", { exact: true })).toBeVisible();
  await expect(evidenceRuntime.getByText("DEV_ONLY", { exact: true })).toBeVisible();
  await expect(evidenceRuntime.getByText("NOT_APPLICABLE", { exact: true })).toBeVisible();
  await expect(page.locator(".development-banner")).toContainText("PUBLIC_STATIC");
  await expect(page.getByText(/不可点击/).first()).toBeVisible();
  await page.goto(`/evidence/${contradictionId}/`);
  await expect(page.getByText(/CONTRADICTION/).first()).toBeVisible();
  await expect(page.getByText("8000000 CNY")).toBeVisible();
  await expect(page.getByText("8200000 CNY")).toBeVisible();
  expect(violations).toEqual([]);
});

test("keeps gauge mapping fail-closed", () => {
  const unavailable = getGaugeDynamics(null);
  expect(unavailable.hasNeedle).toBe(false);
  expect(unavailable.integerValue).toBe(0);
  expect(unavailable.needleAngle).toBe(-120);

  const overflow = getGaugeDynamics(14.4);
  expect(overflow.hasNeedle).toBe(true);
  expect(overflow.integerValue).toBe(14);
  expect(overflow.scaleValue).toBe(10);
  expect(overflow.needleAngle).toBe(120);
  expect(overflow.jitterCount).toBe(3);
  expect(overflow.jitterDuration).toBeLessThan(getGaugeDynamics(1).jitterDuration);
});

test("labels the development banner with the actual runtime mode", () => {
  expect(developmentModeBanner("demo_replay")).toContain("DEMO_REPLAY");
  expect(developmentModeBanner("demo_replay")).not.toContain("PUBLIC_STATIC");
  expect(developmentModeBanner("public_static")).toContain("PUBLIC_STATIC");
});

test("treats missing and non-fresh source coverage as a gap", () => {
  const completeCoverage = BRIEFING_SECTIONS.map((section) => ({
    capability: section["key"].split("_").join("."),
    status: "ok",
  }));
  expect(hasSourceCoverageGap(completeCoverage, true)).toBe(false);
  expect(hasSourceCoverageGap(completeCoverage.slice(0, 4), true)).toBe(true);
  expect(hasSourceCoverageGap(
    completeCoverage.map((coverage, index) => index === 2
      ? { ...coverage, status: "unavailable" }
      : coverage),
    true,
  )).toBe(true);
  expect(hasSourceCoverageGap(completeCoverage, false)).toBe(true);
});

test("publishes only approved source-gap reason codes", () => {
  expect(safeGapReason("SYNTHETIC_SOURCE_DELAY")).toBe("SYNTHETIC_SOURCE_DELAY");
  expect(safeGapReason("UNLISTED_REASON_CODE")).toBe("未公开原因");
  expect(safeGapReason(null)).toBe("—");
});

test("network policy allows only the same-origin snapshot data plane", () => {
  const asset = `${PUBLIC_STATIC_TEST_ORIGIN}/_next/static/chunks/app.js`;
  const snapshot = `${PUBLIC_STATIC_TEST_ORIGIN}/data/snapshots/71000000-0000-4000-8000-000000000010/manifest.json`;
  expect(isAllowedPublicStaticRequest({ url: `${PUBLIC_STATIC_TEST_ORIGIN}/`, resourceType: "document", navigation: true })).toBe(true);
  expect(isAllowedPublicStaticRequest({ url: asset, resourceType: "script", navigation: false })).toBe(true);
  expect(isAllowedPublicStaticRequest({ url: `${PUBLIC_STATIC_TEST_ORIGIN}/data/current.json`, resourceType: "fetch", navigation: false })).toBe(true);
  expect(isAllowedPublicStaticRequest({ url: snapshot, resourceType: "fetch", navigation: false })).toBe(true);
  expect(isAllowedPublicStaticRequest({ url: "http://127.0.0.1:4174/", resourceType: "document", navigation: true })).toBe(false);
  expect(isAllowedPublicStaticRequest({ url: `${PUBLIC_STATIC_TEST_ORIGIN}/${["a", "p", "i"].join("")}/v1/briefing`, resourceType: "document", navigation: true })).toBe(false);
  expect(isAllowedPublicStaticRequest({ url: asset, resourceType: "fetch", navigation: false })).toBe(false);
  expect(isAllowedPublicStaticRequest({ url: `${snapshot}?bypass=1`, resourceType: "fetch", navigation: false })).toBe(false);
  expect(isAllowedPublicStaticRequest({ url: `${PUBLIC_STATIC_TEST_ORIGIN}/data/private.json`, resourceType: "fetch", navigation: false })).toBe(false);
  expect(isAllowedPublicStaticRequest({ url: asset, resourceType: "xhr", navigation: false })).toBe(false);
  expect(isAllowedPublicStaticRequest({ url: "https://example.com/app.js", resourceType: "script", navigation: false })).toBe(false);
});
