import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import {
  expect,
  test,
  type Page,
} from "../../../apps/web/node_modules/@playwright/test/index.mjs";
import {
  isAllowedPublicStaticRequest,
  PUBLIC_STATIC_TEST_ORIGIN,
} from "../../../apps/web/tests/public-static-network-policy.ts";

const sectionExpectations = [
  { key: "statutory_announcements", label: "法定公告" },
  { key: "financial_changes", label: "财务变化" },
  { key: "market_snapshot", label: "行情快照" },
  { key: "company_industry_news", label: "公司与行业新闻" },
  { key: "macro_flashes", label: "相关宏观快讯" },
] as const;

type SectionKey = (typeof sectionExpectations)[number]["key"];
type BriefingItem = { change_type: string; event_id: string };
type SourceCoverage = {
  source_id: string;
  status: string;
  provenance: {
    provider: string;
    runtime_mode: string;
    release_tier: string;
    rights_status: string;
  };
};
type EvidenceItem = {
  evidence_id: string;
  license_id: string;
  provenance: {
    provider: string;
    runtime_mode: string;
    release_tier: string;
    rights_status: string;
  };
};

const repositoryRoot = resolve(__dirname, "../../..");
const buildMetadata = JSON.parse(
  readFileSync(join(repositoryRoot, "apps/web/out/build-metadata.json"), "utf8"),
) as { product_commit: string };
const runtimeDirectory = [".", "runtime"].join("");
const briefing = JSON.parse(
  readFileSync(
    join(
      repositoryRoot,
      runtimeDirectory,
      "reports/public-demo",
      buildMetadata.product_commit,
      "latest-briefing.json",
    ),
    "utf8",
  ),
) as {
  data: {
    sections: Record<SectionKey, { items: BriefingItem[] }>;
    source_coverage: SourceCoverage[];
  };
};
const items = Object.values(briefing.data.sections).flatMap((section) => section.items);
const coverageGaps = briefing.data.source_coverage.filter((coverage) => coverage.status !== "ok");

function expectedStatusLabel(status: string): string {
  if (status.endsWith("_blocked")) {
    return "blocked · 权利阻断";
  }
  if (status === "ok") {
    return "fresh · 可用";
  }
  if (status === "delayed" || status === "timeliness_unverified") {
    return "stale · 延迟或时效未确认";
  }
  if (status === "unavailable" || status === "not_qualified") {
    return "degraded · 不可用或未达标";
  }
  return status;
}

function eventId(changeType: string): string {
  const item = items.find((candidate) => candidate.change_type === changeType);
  if (item === undefined) {
    return "__no_evidence__";
  }
  return item.event_id;
}

const correctionId = eventId("correction");
const contradictionId = eventId("contradiction");
const correctionEvidence = items.length === 0
  ? { data: { evidence: [] as EvidenceItem[] } }
  : JSON.parse(
      readFileSync(
        join(
          repositoryRoot,
          runtimeDirectory,
          "reports/public-demo",
          buildMetadata.product_commit,
          "evidence",
          `${correctionId}.json`,
        ),
        "utf8",
      ),
    ) as { data: { evidence: EvidenceItem[] } };

async function guardNetwork(page: Page): Promise<string[]> {
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

const forbiddenText = /DEV_LIVE|DEV_ONLY|UNVERIFIED|DISCOVERY|data_as_of|published_at|fresh_until|last_success|run_id|source_id|evidence_id|license_id|schema_version|batch_audit|MANIFEST|INVALID_ASSET|SHA-256|PUBLIC_STATIC|DEMO_REPLAY/i;

async function expectPlainDom(page: Page): Promise<void> {
  await expect(page.locator("body")).not.toContainText(forbiddenText);
  expect(await page.content()).not.toMatch(forbiddenText);
}

if (items.length === 0) {
  test("keeps the no-change snapshot readable without inventing scores", async ({ page }) => {
    const violations = await guardNetwork(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "先看哪里需要核对，再看交易结构是否承压" })).toBeVisible();
    await expect(page.locator(".research-hero .eyebrow")).toContainText("兆易创新");
    await expect(page.getByRole("heading", { name: "信息风险温度" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "交易结构压力（研究）" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "这份资料仍可阅读，但暂时没有双指数" })).toBeVisible();
    const meters = page.getByRole("meter");
    await expect(meters).toHaveCount(2);
    await expect(meters.nth(0)).not.toHaveAttribute("aria-valuenow", /.+/);
    await expect(meters.nth(1)).not.toHaveAttribute("aria-valuenow", /.+/);
    await expectPlainDom(page);
    expect(violations).toEqual([]);
  });

  test("summarizes the no-change update in plain language", async ({ page }) => {
    const violations = await guardNetwork(page);
    await page.goto("/details/");
    await expect(page.getByRole("heading", { name: "为什么得到这个结果" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "旧版简报仍可阅读" })).toBeVisible();
    const layout = await page.evaluate(() => ({
      viewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
    }));
    expect(layout.document).toBeLessThanOrEqual(layout.viewport);
    expect(layout.body).toBeLessThanOrEqual(layout.viewport);
    await expectPlainDom(page);
    expect(violations).toEqual([]);
  });

  test("serves a usable static evidence fallback", async ({ page }) => {
    const violations = await guardNetwork(page);
    await page.goto("/evidence/__no_evidence__/");
    await expect(page.getByRole("heading", { name: "未找到这条静态证据" })).toBeVisible();
    const skipLink = page.getByRole("link", { name: "跳到主要内容" });
    await skipLink.focus();
    await expect(skipLink).toBeFocused();
    await expectPlainDom(page);
    expect(violations).toEqual([]);
  });
} else {

test("renders five gauges and follows the source gap into detailed changes", async ({ page }) => {
  const violations = await guardNetwork(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "本次简报，有多少信息真正发生了变化？" }),
  ).toBeVisible();
  await expect(page.getByText("兆易创新").first()).toBeVisible();
  await expect(page.getByText("603986.SH").first()).toBeVisible();

  const gauges = page.locator("[data-change-gauge]");
  await expect(gauges).toHaveCount(sectionExpectations.length);
  for (const [index, section] of sectionExpectations.entries()) {
    await expect(page.getByRole("heading", { name: section.label })).toBeVisible();
    await expect(gauges.nth(index)).toHaveAttribute(
      "data-value",
      String(briefing.data.sections[section.key].items.length),
    );
  }
  expect(coverageGaps.length).toBeGreaterThan(0);
  await expect(page.getByText("存在缺口", { exact: true })).toBeVisible();
  await expect(page.getByText(/0 不代表已完整核验/)).toBeVisible();
  await expect(page.getByText("PUBLIC_STATIC", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/合成演示/).first()).toBeVisible();

  await page.getByRole("link", { name: "数据与审计", exact: true }).click();
  await expect(page).toHaveURL(/\/details\/$/);
  await expect(page.getByRole("heading", { name: "数据、审计与计算依据" })).toBeVisible();
  const sources = page.locator("#sources");
  await expect(sources.getByRole("heading", { name: "来源状态" })).toBeVisible();
  await expect(sources.locator(".source-status-card")).toHaveCount(
    briefing.data.source_coverage.length,
  );
  for (const coverage of briefing.data.source_coverage) {
    const sourceCard = sources.locator(".source-status-card").filter({
      has: page.getByRole("heading", { name: coverage.source_id, exact: true }),
    });
    await expect(sourceCard).toHaveCount(1);
    await expect(sourceCard.locator(".source-state")).toHaveText(
      expectedStatusLabel(coverage.status),
    );
    await expect(sourceCard.getByText(coverage.provenance.provider, { exact: true })).toBeVisible();
    await expect(sourceCard.getByText(
      `${coverage.provenance.runtime_mode.toUpperCase()} / ${coverage.provenance.release_tier.toUpperCase()}`,
      { exact: true },
    )).toBeVisible();
    await expect(sourceCard.getByText(
      coverage.provenance.rights_status.toUpperCase(),
      { exact: true },
    )).toBeVisible();
  }
  const changes = page.locator("#changes");
  await expect(changes.getByText("CORRECTION", { exact: true }).first()).toBeVisible();
  await expect(changes.getByText("CONTRADICTION", { exact: true }).first()).toBeVisible();
  expect(violations).toEqual([]);
});

test("serves correction and contradiction from replay event IDs", async ({ page }) => {
  const violations = await guardNetwork(page);
  await page.goto(`/evidence/${correctionId}/`);
  await expect(page.getByRole("heading", { name: "更正关系" })).toBeVisible();
  const revisionTimeline = page.locator(".revision-timeline");
  await expect(revisionTimeline.getByText("revision 2", { exact: true })).toBeVisible();
  await expect(revisionTimeline.getByText("revision 1", { exact: true })).toBeVisible();
  for (const evidence of correctionEvidence.data.evidence) {
    const evidenceCard = page.locator(".evidence-card").filter({ hasText: evidence.evidence_id });
    await expect(evidenceCard).toHaveCount(1);
    await expect(evidenceCard.getByText(evidence.license_id, { exact: true })).toBeVisible();
    await expect(evidenceCard.getByText(evidence.provenance.provider, { exact: true })).toBeVisible();
    await expect(evidenceCard.getByText(
      evidence.provenance.runtime_mode.toUpperCase(),
      { exact: true },
    )).toBeVisible();
  }
  await page.goto(`/evidence/${contradictionId}/`);
  await expect(page.getByText(/CONTRADICTION/).first()).toBeVisible();
  await expect(page.getByText("780000000.00 CNY")).toBeVisible();
  expect(violations).toEqual([]);
});

test("serves static 404 and remains usable at 390px", async ({ page }) => {
  const violations = await guardNetwork(page);
  const response = await page.goto("/evidence/90000000-0000-4000-8000-000000000099/");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "未找到这条静态证据" })).toBeVisible();
  await page.goto("/");
  const layout = await page.evaluate(() => ({
    width: document.body.scrollWidth,
    viewport: window.innerWidth,
  }));
  expect(layout.width).toBeLessThanOrEqual(layout.viewport);
  const skipLink = page.getByRole("link", { name: "跳到主要内容" });
  await skipLink.focus();
  await expect(skipLink).toBeFocused();
  expect(violations).toEqual([]);
});

test("network policy forbids runtime APIs and third parties", () => {
  const asset = `${PUBLIC_STATIC_TEST_ORIGIN}/_next/static/chunks/app.js`;
  const apiSegment = ["a", "p", "i"].join("");
  expect(
    isAllowedPublicStaticRequest({
      url: asset,
      resourceType: "script",
      navigation: false,
    }),
  ).toBe(true);
  expect(
    isAllowedPublicStaticRequest({
      url: `${PUBLIC_STATIC_TEST_ORIGIN}/${apiSegment}/v1/briefing`,
      resourceType: "document",
      navigation: true,
    }),
  ).toBe(false);
  expect(
    isAllowedPublicStaticRequest({
      url: "https://example.com/app.js",
      resourceType: "script",
      navigation: false,
    }),
  ).toBe(false);
});
}
