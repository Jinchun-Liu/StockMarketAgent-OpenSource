import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { isAllowedPublicStaticRequest } from "../public-static-network-policy.ts";

const forbiddenText = /DEV_LIVE|DEV_ONLY|UNVERIFIED|DISCOVERY|data_as_of|published_at|fresh_until|last_success|run_id|source_id|evidence_id|license_id|schema_version|batch_audit|MANIFEST|INVALID_ASSET|SHA-256|PUBLIC_STATIC|DEMO_REPLAY|abstained|degraded|not_applicable|deepseek_(?:flash|pro)/i;

function pointerPath(name: string): string {
  return resolve(process.cwd(), ".next/public-static-test-pointers", `${name}.json`);
}

async function guardNetwork(page: Page, pointer?: string, failCurrent = false) {
  const violations: string[] = [];
  const requests: string[] = [];
  await page.route("**/*", async (route) => {
    const request = route.request();
    if (!isAllowedPublicStaticRequest({
      url: request.url(),
      resourceType: request.resourceType(),
      navigation: request.isNavigationRequest(),
    })) {
      violations.push(request.url());
      await route.abort("blockedbyclient");
      return;
    }
    const url = new URL(request.url());
    if (request.resourceType() === "fetch") requests.push(url.pathname);
    if (request.resourceType() === "fetch" && url.pathname === "/data/current.json") {
      if (failCurrent) {
        await route.abort("failed");
        return;
      }
      if (pointer) {
        await route.fulfill({ body: await readFile(pointerPath(pointer), "utf8"), contentType: "application/json", status: 200 });
        return;
      }
    }
    await route.continue();
  });
  return { violations, requests };
}

async function expectPlainDom(page: Page) {
  await expect(page.locator("body")).not.toContainText(forbiddenText);
  expect(await page.content()).not.toMatch(forbiddenText);
}

test("shows the two research meters and no computer terminology", async ({ page }, testInfo) => {
  const network = await guardNetwork(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "先看哪里需要核对，再看交易结构是否承压" })).toBeVisible();
  const meters = page.getByRole("meter");
  await expect(meters).toHaveCount(2);
  await expect(meters.nth(0)).toHaveAttribute("aria-valuenow", "64");
  await expect(meters.nth(1)).toHaveAttribute("aria-valuenow", "78");
  await expect(page.getByRole("heading", { name: "信息风险温度" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "交易结构压力（研究）" })).toBeVisible();
  await expectPlainDom(page);
  expect(network.violations).toEqual([]);
  expect(network.requests.every((path) => path === "/data/current.json" || path.startsWith("/data/snapshots/"))).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("dual-index-overview.png"), fullPage: true });
});

for (const [pointer, score] of [["score-zero", "0"], ["score-hundred", "100"]] as const) {
  test(`renders an exact ${score} without substitution`, async ({ page }) => {
    const network = await guardNetwork(page, pointer);
    await page.goto("/");
    const meters = page.getByRole("meter");
    await expect(meters).toHaveCount(2);
    await expect(meters.nth(0)).toHaveAttribute("aria-valuenow", score);
    await expect(meters.nth(1)).toHaveAttribute("aria-valuenow", score);
    await expect(meters.nth(0).locator("output")).toContainText(score);
    await expectPlainDom(page);
    expect(network.violations).toEqual([]);
  });
}

test("keeps null distinct from zero", async ({ page }) => {
  const network = await guardNetwork(page, "unavailable");
  await page.goto("/");
  const meters = page.getByRole("meter");
  await expect(meters).toHaveCount(2);
  await expect(meters.nth(0)).not.toHaveAttribute("aria-valuenow", /.+/);
  await expect(meters.nth(1)).not.toHaveAttribute("aria-valuenow", /.+/);
  await expect(meters.nth(0)).toHaveAttribute("aria-valuetext", "暂时没有可用分数");
  await expect(page.getByText("暂不评分")).toHaveCount(2);
  await expectPlainDom(page);
  expect(network.violations).toEqual([]);
});

test("supports quick, deep and rule fallback explanations without model requests", async ({ page }) => {
  const network = await guardNetwork(page);
  await page.goto("/");
  await page.getByRole("button", { name: "深入解读" }).click();
  await expect(page.getByRole("heading", { name: "两个指数同时偏高，但含义不同" })).toBeVisible();
  await expect(page.getByText(/内部状态 资料不足，暂不评分 也必须转成白话/)).toBeVisible();
  expect(network.requests.every((path) => !path.includes("model") && !path.includes("chat"))).toBe(true);

  const fallbackNetwork = await guardNetwork(page, "flash-fallback");
  await page.goto("/");
  await expect(page.getByText("本次使用固定规则整理，核心分数与原因不受影响。")).toBeVisible();
  await expect(page.getByRole("button", { name: "深入解读" })).toHaveCount(0);
  expect(network.violations).toEqual([]);
  expect(fallbackNetwork.violations).toEqual([]);
});

test("explains categories, facets, dimensions, history and update summary", async ({ page }) => {
  const network = await guardNetwork(page);
  await page.goto("/details/");
  await expect(page.getByRole("heading", { name: "为什么得到这个结果" })).toBeVisible();
  for (const label of ["法定公告", "财务变化", "行情变化", "公司与行业", "相关宏观信息"]) {
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  }
  for (const label of ["成交活跃偏离", "位置与拥挤", "流动性承压", "波动与尾部压力", "个股与行业偏离"]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(page.locator(".structure-grid > article")).toHaveCount(6);
  await expect(page.getByRole("heading", { name: "历史相似情景" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "本次更新情况" })).toBeVisible();
  await expect(page.getByText("本次资料检查已完成。")).toBeVisible();
  await expectPlainDom(page);
  expect(network.violations).toEqual([]);
});

test("keeps the older snapshot readable without inventing scores", async ({ page }) => {
  const network = await guardNetwork(page, undefined, true);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "这份资料仍可阅读，但暂时没有双指数" })).toBeVisible();
  await expect(page.getByRole("meter")).toHaveCount(2);
  await expect(page.getByRole("meter").first()).not.toHaveAttribute("aria-valuenow", /.+/);
  await expectPlainDom(page);
  expect(network.violations).toEqual([]);
});

test("uses plain-language evidence pages", async ({ page }) => {
  const network = await guardNetwork(page);
  await page.goto("/evidence/10000000-0000-4000-8000-000000000001/");
  await expect(page.getByRole("heading", { name: "这条信息后来有更正" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "相关证据" })).toBeVisible();
  await expect(page.getByText("历史内容不会被后来修订覆盖。")).toBeVisible();
  await expectPlainDom(page);
  expect(network.violations).toEqual([]);
});

for (const width of [390, 768, 1440]) {
  test(`has no horizontal scroll at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const network = await guardNetwork(page);
    for (const path of ["/", "/details/"]) {
      await page.goto(path);
      await expect(page.getByRole("main")).toBeVisible();
      const layout = await page.evaluate(() => ({
        viewport: window.innerWidth,
        document: document.documentElement.scrollWidth,
        body: document.body.scrollWidth,
      }));
      expect(layout.document).toBeLessThanOrEqual(layout.viewport);
      expect(layout.body).toBeLessThanOrEqual(layout.viewport);
    }
    expect(network.violations).toEqual([]);
  });
}

test("keeps meters readable with reduced motion and focus navigation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const network = await guardNetwork(page);
  await page.goto("/");
  await expect(page.getByRole("meter").first()).toHaveCSS("overflow", "hidden");
  const skip = page.getByRole("link", { name: "跳到主要内容" });
  await skip.focus();
  await expect(skip).toBeFocused();
  const deep = page.getByRole("button", { name: "深入解读" });
  await deep.focus();
  await expect(deep).toBeFocused();
  await deep.click();
  await expect(page.getByRole("heading", { name: "两个指数同时偏高，但含义不同" })).toBeVisible();
  expect(network.violations).toEqual([]);
});
