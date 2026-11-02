import { expect, test, type Page } from "@playwright/test";

const SESSION_ID = "10000000-0000-4000-8000-000000000099";

function researchFixture(expiresAt: string) {
  const now = new Date().toISOString();
  const analysisConfig = {
    schema_version: "1.0",
    primary_years: 5,
    comparison_quarters: 20,
    scan_lifetime_events: true,
  };
  const session = {
    schema_version: "1.0",
    session_id: SESSION_ID,
    job_id: "30000000-0000-4000-8000-000000000099",
    instrument_id: "XSHG:603986",
    instrument_name: "兆易创新",
    as_of: now,
    analysis_config: analysisConfig,
    source_mode: "synthetic_development_only",
    status: "succeeded",
    progress: {
      schema_version: "1.0",
      stage: "completed",
      message: "完整报告已就绪。",
      latest_progress_at: now,
    },
    coverage_gaps: [],
    retryable: false,
    created_at: now,
    updated_at: now,
    completed_at: now,
    expires_at: expiresAt,
  };
  return {
    session,
    report: {
      schema_version: "1.0",
      report_id: "20000000-0000-4000-8000-000000000099",
      session_id: SESSION_ID,
      instrument_id: "XSHG:603986",
      instrument_name: "兆易创新",
      as_of: now,
      analysis_config: analysisConfig,
      source_mode: "synthetic_development_only",
      coverage_start: now,
      coverage_end: now,
      coverage_gaps: [],
      sections: [],
      claims: [],
      non_production_notice: "原创合成数据，仅用于产品链路验证。",
      paper_only: true,
      model_provider: "synthetic",
      model_name: "synthetic-research",
      model_version: "1.0.0",
      generator_version: "1.0.0",
      validator_version: "1.0.0",
      rule_version: "1.0.0",
      data_manifest_sha256: "a".repeat(64),
      generated_at: now,
      expires_at: expiresAt,
    },
  };
}

async function routeHistory(page: Page, session: object, expiresAt: string) {
  await page.route("**/api/v1/research-sessions?limit=20", async (route) => {
    const items = Date.now() < Date.parse(expiresAt) ? [session] : [];
    await route.fulfill({
      contentType: "application/json",
      status: 200,
      body: JSON.stringify({
        data: { schema_version: "1.0", items, next_cursor: null },
      }),
    });
  });
}

test("removes an open report when its retention expires", async ({ page }) => {
  const expiresAt = new Date(Date.now() + 2_500).toISOString();
  const { report, session } = researchFixture(expiresAt);
  await routeHistory(page, session, expiresAt);
  await page.route(`**/api/v1/research-sessions/${SESSION_ID}/report`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 200,
      body: JSON.stringify({ data: report }),
    });
  });

  await page.goto("/research/");
  await page.getByRole("button", { name: /兆易创新/ }).click();
  await expect(page.getByRole("heading", { name: "兆易创新" })).toBeVisible();
  await expect(page.getByText("报告已超过七天保留期，已从页面移除。")).toBeVisible({
    timeout: 7_000,
  });
  await expect(page.getByRole("heading", { name: "兆易创新" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /兆易创新/ })).toHaveCount(0);
});

test("fails closed when a focused page can no longer read the report", async ({ page }) => {
  const expiresAt = new Date(Date.now() + 60_000).toISOString();
  const { report, session } = researchFixture(expiresAt);
  let reportReads = 0;
  await routeHistory(page, session, expiresAt);
  await page.route(`**/api/v1/research-sessions/${SESSION_ID}/report`, async (route) => {
    reportReads += 1;
    if (reportReads === 1) {
      await route.fulfill({
        contentType: "application/json",
        status: 200,
        body: JSON.stringify({ data: report }),
      });
      return;
    }
    await route.fulfill({
      contentType: "application/problem+json",
      status: 410,
      body: JSON.stringify({ detail: "REPORT_EXPIRED" }),
    });
  });

  await page.goto("/research/");
  await page.getByRole("button", { name: /兆易创新/ }).click();
  await expect(page.getByRole("heading", { name: "兆易创新" })).toBeVisible();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByText("报告已不可用，已从页面移除。")).toBeVisible();
  await expect(page.getByRole("heading", { name: "兆易创新" })).toHaveCount(0);
});

test("shows typed international event impact paths at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const expiresAt = new Date(Date.now() + 60_000).toISOString();
  const fixture = researchFixture(expiresAt);
  const evidence = {
    evidence_id: "40000000-0000-4000-8000-000000000099",
    source_provider_id: "r2_synthetic_policy",
    independent_source_group: "synthetic_policy_group",
    source_role: "international_official_event",
    source_label: "原创合成国际技术管制更正公告",
    published_at: fixture.session.as_of,
    first_seen_at: fixture.session.as_of,
    revision_known_at: fixture.session.as_of,
    locator: "synthetic://r2/global-events/technology-control",
  };
  const counterEvidence = {
    ...evidence,
    evidence_id: "40000000-0000-4000-8000-000000000100",
    source_provider_id: "r2_synthetic_review",
    independent_source_group: "synthetic_review_group",
    source_label: "原创合成复核决定",
  };
  const report = {
    ...fixture.report,
    sections: [
      {
        kind: "major_events_and_macro",
        title: "重大事件、国际事件与宏观暴露",
        summary: "只展示有证据的零跳或一跳影响路径。",
        claim_ids: ["50000000-0000-4000-8000-000000000099"],
      },
    ],
    claims: [
      {
        schema_version: "1.0",
        claim_id: "50000000-0000-4000-8000-000000000099",
        claim_type: "system_inference",
        section: "major_events_and_macro",
        statement: "该影响由确定性暴露规则生成，不构成价格预测。",
        evidence: [evidence],
        counter_evidence: [counterEvidence],
        falsification_conditions: [],
        uncertainty: "仅在当前事件修订和一跳暴露范围内成立。",
        calculation: {
          formula: "conditional_impact = event_revision + exposure_path + channel",
          rule_version: "r2-global-event-match.v1",
          cutoff: fixture.session.as_of,
          inputs: [
            {
              name: "event_revision",
              value: "event-1@2",
              evidence_ids: [evidence.evidence_id],
            },
            {
              name: "exposure_directness",
              value: "one_hop_evidence",
              evidence_ids: [evidence.evidence_id],
            },
            {
              name: "impact_channel",
              value: "technology_control",
              evidence_ids: [evidence.evidence_id],
            },
            {
              name: "impact_direction",
              value: "mixed",
              evidence_ids: [evidence.evidence_id],
            },
            {
              name: "evidence_path",
              value: "event-1 -> industry:semiconductor -> XSHG:603986",
              evidence_ids: [evidence.evidence_id],
            },
          ],
        },
        publication_eligible: true,
      },
    ],
  };
  await routeHistory(page, fixture.session, expiresAt);
  await page.route(`**/api/v1/research-sessions/${SESSION_ID}/report`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 200,
      body: JSON.stringify({ data: report }),
    });
  });

  await page.goto("/research/");
  await page.getByRole("button", { name: /兆易创新/ }).click();
  await expect(
    page.getByRole("heading", { name: "重大事件、国际事件与宏观暴露" }),
  ).toBeVisible();
  await page.getByText("查看证据与计算口径").click();
  await expect(page.getByText("支持证据")).toBeVisible();
  await expect(page.getByText("反证", { exact: true })).toBeVisible();
  await expect(page.getByText("exposure_directness")).toBeVisible();
  await expect(page.getByText("one_hop_evidence")).toBeVisible();
  await expect(page.getByText("technology_control")).toBeVisible();
  await expect(page.getByText("开启监控")).toHaveCount(0);
  await expect(page.getByText("添加关注")).toHaveCount(0);
});
