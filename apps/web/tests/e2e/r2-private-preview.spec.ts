import { readFile } from "node:fs/promises";

import { expect, test, type Page, type Route } from "@playwright/test";

import type { components } from "../../lib/api/openapi.generated";
import runtimeConfig from "../../public/runtime-config.json";

const SESSION_ID = "10000000-0000-4000-8000-000000000099";
const RUNNING_SESSION_ID = "10000000-0000-4000-8000-000000000100";
const FAILED_SESSION_ID = "10000000-0000-4000-8000-000000000101";
const REPORT_ID = "20000000-0000-4000-8000-000000000099";
const CLAIM_ID = "50000000-0000-4000-8000-000000000099";
const SECOND_CLAIM_ID = "50000000-0000-4000-8000-000000000100";
const OIDC_STORAGE =
  "oidc.user:https://cognito-idp.ap-northeast-1.amazonaws.com/ap-northeast-1_LOCALPLACEHOLDER:localplaceholder";

const sectionKinds = [
  "target_and_coverage",
  "business_model",
  "financial_quality",
  "valuation_context",
  "major_events_and_macro",
  "bull_case",
  "bear_case",
  "conflicts_and_unknowns",
  "falsification_and_follow_up",
  "evidence_and_versions",
] as const;
const sectionTitles: Record<(typeof sectionKinds)[number], string> = {
  target_and_coverage: "研究目标与覆盖范围",
  business_model: "商业模式",
  financial_quality: "财务质量",
  valuation_context: "估值背景",
  major_events_and_macro: "重大事件、国际事件与宏观暴露",
  bull_case: "多方论点",
  bear_case: "空方论点",
  conflicts_and_unknowns: "冲突与未知",
  falsification_and_follow_up: "证伪条件与后续跟踪",
  evidence_and_versions: "证据与版本",
};
type ResearchReportV1_2 = components["schemas"]["ResearchReportV1_2"];
type ResearchEvidenceCitation = components["schemas"]["stock_assistant__contracts__research__ResearchEvidenceCitation"];
type ResearchSkillExecution = components["schemas"]["ResearchSkillExecution"];
type ResearchSkillId = components["schemas"]["ResearchSkillId"];
const skillIds = [
  "bottleneck-hunter", "deep-company-series", "dyp-ask", "earnings-review",
  "earnings-team", "era-alpha", "financial-data", "income-investment",
  "industry-funnel", "industry-research", "investment-checklist", "investment-research",
  "investment-team", "management-deep-dive", "news-pulse", "portfolio-review",
  "private-company-research", "quality-screen", "thesis-drift", "thesis-tracker",
  "investment-memo-craft",
] as const satisfies readonly ResearchSkillId[];

function buildSkillExecutions(evidenceId: string): ResearchSkillExecution[] {
  const executions = skillIds.map((skill_id) => ({
    schema_version: "1.0" as const,
    skill_id,
    status: "applied" as const,
    evidence_ids: [evidenceId],
    claim_ids: [CLAIM_ID],
    template_version: "ai-berkshire-fd83d06-stockassistant-1.0",
    detail: null,
  }));
  expect(executions).toHaveLength(21);
  expect(new Set(executions.map((execution) => execution.skill_id)).size).toBe(21);
  return executions;
}

function seedIdentity(page: Page) {
  return page.addInitScript(({ storageName, expiresAt }) => {
    window.sessionStorage.setItem(
      storageName,
      JSON.stringify({
        session_state: null,
        access_token: "synthetic-browser-bearer",
        token_type: "Bearer",
        scope: "openid",
        profile: { sub: "synthetic-owner" },
        expires_at: expiresAt,
      }),
    );
  }, { storageName: OIDC_STORAGE, expiresAt: Math.floor(Date.now() / 1000) + 3600 });
}

function assertBearer(route: Route) {
  expect(route.request().headers().authorization).toBe("Bearer synthetic-browser-bearer");
}

function fixture() {
  const now = new Date().toISOString();
  const analysisConfig: ResearchReportV1_2["analysis_config"] = {
    schema_version: "1.0",
    primary_years: 5,
    comparison_quarters: 20,
    scan_listing_history: true,
    stalled_after_minutes: 30,
    report_retention_days: 7,
    skill_selection: { schema_version: "1.0", mode: "auto", selected_skill_ids: [] },
  };
  const coverageGaps: ResearchReportV1_2["coverage_gaps"] = [
    {
      area: "business.core_products",
      reason_code: "WEB_DISCOVERY_LIMITED",
      detail: "联网候选均未通过当前证据规则，核心产品仍需补充核对。",
    },
    {
      area: "business.product_structure_change",
      reason_code: "WEB_PIT_NOT_ELIGIBLE",
      detail: "现有网页资料不满足本次分析截点，产品结构调整方向仍未知。",
    },
  ];
  const session: components["schemas"]["stock_assistant__contracts__research__ResearchSessionView"] = {
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
      stage: "completed",
      message: "完整报告已就绪。",
      latest_progress_at: now,
      checkpoint: "report-published",
    },
    coverage_gaps: coverageGaps,
    retryable: false,
    created_at: now,
    updated_at: now,
    completed_at: now,
    expires_at: new Date(Date.now() + 7 * 86_400_000).toISOString(),
  };
  const evidence: ResearchEvidenceCitation = {
    evidence_id: "40000000-0000-4000-8000-000000000099",
    source_provider_id: "r2_synthetic_policy",
    independent_source_group: "synthetic_policy_group",
    source_role: "international_official_event",
    source_label: "原创合成国际技术管制更正公告",
    published_at: now,
    first_seen_at: now,
    revision_known_at: now,
    locator: "synthetic://r2/global-events/technology-control",
    acquisition_mode: "model_assisted_web",
    source_level: "company_official",
    verification_status: "single_source",
    reliability_factor: "0.90",
    content_sha256: "b".repeat(64),
    rights_snapshot_sha256: "c".repeat(64),
  };
  const report: ResearchReportV1_2 = {
    schema_version: "1.2",
    report_id: REPORT_ID,
    session_id: SESSION_ID,
    instrument_id: "XSHG:603986",
    instrument_name: "兆易创新",
    as_of: now,
    analysis_config: analysisConfig,
    source_mode: "synthetic_development_only",
    coverage_start: now,
    coverage_end: now,
    coverage_gaps: coverageGaps,
    sections: sectionKinds.map((kind, index) => ({
      kind,
      title: sectionTitles[kind],
      summary: index === 4 ? "只展示已有证据支持的直接或间接影响路径。" : "原创合成研究摘要。",
      claim_ids:
        index === 1 ? [SECOND_CLAIM_ID] : index === 4 ? [CLAIM_ID] : [],
    })),
    claims: [
      {
        schema_version: "1.0",
        claim_id: CLAIM_ID,
        claim_type: "system_inference",
        section: "major_events_and_macro",
        statement: "该影响由确定性暴露规则生成，不构成价格预测。",
        evidence: [evidence],
        counter_evidence: [{
          ...evidence,
          evidence_id: "40000000-0000-4000-8000-000000000100",
          source_label: "原创合成复核决定",
          acquisition_mode: "built_in_api",
          source_level: "synthetic",
          verification_status: "independently_verified",
          reliability_factor: "1.0",
          content_sha256: null,
          rights_snapshot_sha256: null,
        }],
        falsification_conditions: ["公司披露确认该暴露已消失"],
        uncertainty: "仅在当前事件版本与已验证暴露范围内成立。",
        calculation: {
          formula: "conditional_impact = event_revision + exposure_path + channel",
          rule_version: "r2-global-event-match.v1",
          cutoff: now,
          inputs: [
            { name: "event_revision", value: "event-1@2", evidence_ids: [evidence.evidence_id] },
            { name: "exposure_directness", value: "one_hop_evidence", evidence_ids: [evidence.evidence_id] },
            { name: "impact_channel", value: "technology_control", evidence_ids: [evidence.evidence_id] },
            { name: "impact_direction", value: "mixed", evidence_ids: [evidence.evidence_id] },
          ],
        },
        publication_eligible: true,
      },
      {
        schema_version: "1.0",
        claim_id: SECOND_CLAIM_ID,
        claim_type: "fact",
        section: "business_model",
        statement: "当前证据只确认公司业务结构仍需进一步核对。",
        evidence: [
          {
            ...evidence,
            evidence_id: "40000000-0000-4000-8000-000000000101",
            source_label: "原创合成业务结构记录",
            acquisition_mode: "built_in_api",
            source_level: "synthetic",
            verification_status: "independently_verified",
            reliability_factor: "1.0",
            content_sha256: null,
            rights_snapshot_sha256: null,
          },
        ],
        counter_evidence: [],
        falsification_conditions: [],
        uncertainty: "未发现可核验的产品结构调整说明。",
        calculation: null,
        publication_eligible: true,
      },
    ],
    reader_edition: {
      schema_version: "1.0",
      one_sentence_takeaway: "这是合成研究，仅用于验证研究流程。",
      how_the_company_makes_money: [
        "核心产品或服务: 当前证据未说明具体产品或服务。",
        "客户到收入链: 当前证据未说明客户如何转化为收入和利润。",
        "产品结构调整: 当前证据未说明具体产品或调整方向。",
      ],
      why_this_score: [
        "总体结论: 本报告使用合成资料，仅验证研究流程，不评价真实公司质量。",
        "最强支撑: 合成资料不能作为真实公司的评分支撑。",
        "主要拖累或未知: 真实业务、财务和风险证据尚未接入，因此不发布数字分。",
      ],
      main_strengths: ["业务基础可从证据中核验。"],
      main_risks: ["国际事件影响仍有不确定性。"],
      public_analysis_steps: ["确认证券", "检查数据", "理解业务", "比较表现", "寻找反证", "计算评分", "列出未知"],
      unknowns: ["未来需求变化仍未知。"],
      glossary: [{ term: "PIT", explanation: "只使用分析截点当时已经可见的资料。" }],
      claim_ids: [CLAIM_ID],
      safe_markdown: "# 兆易创新研究简明版\n\n**综合质量: 暂不评分**  \n**证据可信度: 合成资料不计分**\n\n## 公司靠什么赚钱\n\n- 核心产品或服务: 当前证据未说明具体产品或服务。\n- 客户到收入链: 当前证据未说明客户如何转化为收入和利润。\n- 产品结构调整: 当前证据未说明具体产品或调整方向。\n\n## 主要风险\n\n- 国际事件影响仍有不确定性。",
      compiler_version: "safe-reader-markdown.v3",
    },
    scorecard: {
      schema_version: "1.0",
      scoring_basis: "real_evidence",
      status: "not_scored",
      overall_score: null,
      score_band: null,
      score_meaning: "合成资料只验证研究流程，不评价真实公司质量，因此不发布数字分。",
      evidence_confidence: "0.0",
      evidence_confidence_meaning: "合成资料的证据可信度固定为零，不代表真实来源覆盖。",
      dimensions: ([
        ["business_quality", "商业模式与业务质量", "0.20"],
        ["financial_quality", "财务质量与持续性", "0.20"],
        ["competitive_durability", "竞争优势与耐久性", "0.15"],
        ["management_and_capital_allocation", "管理层与资本配置", "0.10"],
        ["valuation_context", "历史估值位置", "0.15"],
        ["risk_resilience", "风险韧性与可证伪性", "0.20"],
      ] as const).map(([dimension, display_name, weight]) => ({
        schema_version: "1.0", dimension, display_name, weight, evidence_grade: null,
        raw_score: null, reliability_factor: null, adjusted_score: null,
        reasons: [], evidence_ids: [], counter_evidence_ids: [],
        unscored_reason: "合成资料不用于真实公司评分。",
      })),
      scoring_rule_version: "company-quality-score.v2.synthetic-not-scored",
      recommendation_notice: "公司质量评分不构成投资建议, 也不表示推荐概率。",
    },
    skill_executions: buildSkillExecutions(evidence.evidence_id),
    web_retrieval_disclosure: {
      schema_version: "1.0", model_assisted_item_count: 1,
      not_independently_verified_count: 1, source_levels: ["company_official"],
      receipt_ids: ["60000000-0000-4000-8000-000000000099"],
      warning: "本报告有 1 条信息由模型辅助联网取得，其中 1 条未完成独立交叉验证，可能存在误差；相关评分已向中性值收缩。",
    },
    non_production_notice: "原创合成数据，仅用于产品链路验证。",
    paper_only: true,
    artifact_object_key: `reports/${SESSION_ID}/${REPORT_ID}.json`,
    model_provider: "deepseek",
    model_name: "deepseek-v4-pro",
    model_version: "deepseek-openai-compatible.v1.15",
    generator_version: "r2-readable-research-generator.v1.8",
    validator_version: "r2-publication-validator.v2.6",
    rule_version: "r2-evidence-rules.v4",
    data_manifest_sha256: "a".repeat(64),
    generated_at: now,
    expires_at: session.expires_at,
  };
  report.reader_edition.safe_markdown = [
    "# 兆易创新研究报告",
    "## 核心结论", report.reader_edition.one_sentence_takeaway,
    "## 关键事实", "### 公司靠什么赚钱", ...report.reader_edition.how_the_company_makes_money,
    ...report.sections.slice(0, 5).map((section) => `### ${section.title}\n\n${section.summary}`),
    "## 看多分析（Bull）", "合成业务改善只在需求恢复与客户验收均成立时支持看多判断。",
    `支持论点。[查看依据](#claim-${SECOND_CLAIM_ID})`,
    "## 看空分析（Bear）", "反证是客户验证进度可能晚于收入确认；不能把潜在需求当成已实现收入。",
    "## 综合判断", "支持与反证并存，当前结果取决于需求和验证进度。",
    "## 风险与待验证", "如果公司披露确认暴露已消失，应重新评估原判断。",
    "## 来源与方法", `核对事件原句与修订。[查看依据](#claim-${CLAIM_ID})`,
    "[未知引用](#claim-unknown) [外部页面](https://example.invalid/research)",
  ].join("\n\n");
  return { report, session };
}

function applySyntheticScores(report: ResearchReportV1_2, missingDimensions: boolean) {
  const values = ["75.0", "50.0", "75.0", "50.0", "50.0", "75.0"];
  const evidenceId = report.claims?.[1]?.evidence?.[0]?.evidence_id;
  const counterId = report.claims?.[0]?.counter_evidence?.[0]?.evidence_id;
  if (!evidenceId || !counterId) throw new Error("Synthetic score fixture requires retained evidence");
  report.scorecard = {
    ...report.scorecard, schema_version: "1.1", scoring_basis: "synthetic_scenario",
    status: missingDimensions ? "not_scored" : "scored",
    overall_score: missingDimensions ? null : "63.8", score_band: missingDimensions ? null : "基础稳健",
    score_meaning: "评分仅描述当前合成场景，不评价真实公司，也不代表推荐概率。",
    evidence_confidence_meaning: "真实来源核验：不适用（合成）。场景计算系数为一，不代表真实来源可信度。",
    scoring_rule_version: "company-quality-score.v3.synthetic-scenario",
    dimensions: report.scorecard.dimensions.map((dimension, index) => {
      const missing = missingDimensions && [2, 3].includes(index);
      const score = values[index];
      if (score === undefined) throw new Error("Synthetic score fixture requires six dimensions");
      return { ...dimension,
        evidence_grade: missing ? null : Number(score) / 25,
        raw_score: missing ? null : score, adjusted_score: missing ? null : score,
        reliability_factor: missing ? null : "1.0",
        reasons: missing ? [] : ["合成场景的正面事实与限制并存，按既定分档整理。"],
        evidence_ids: missing ? [] : [evidenceId], counter_evidence_ids: missing ? [] : [counterId],
        unscored_reason: missing ? "当前合成快照缺少本维度事实，不能补分。" : null,
      };
    }),
  };
  const scoreText = [
    `**模拟总分：${report.scorecard.overall_score ?? "未评分"}${missingDimensions ? "" : " / 100"}**`,
    report.scorecard.score_meaning, "真实来源核验：不适用（合成）",
    ...report.scorecard.dimensions.map((dimension) => [
      `### ${dimension.display_name}：${dimension.adjusted_score ?? "未评分"}`,
      ...(dimension.reasons ?? []), dimension.unscored_reason ?? "",
      ...(dimension.evidence_ids?.length ? [`支持依据：[查看依据](#claim-${SECOND_CLAIM_ID})`, `反证依据：[查看依据](#claim-${CLAIM_ID})`] : []),
    ].join("\n\n")),
    "主要问题与限制：合成资料不代表真实公司；核心客户验收与需求恢复仍需检验。",
  ].join("\n\n");
  report.reader_edition.safe_markdown = report.reader_edition.safe_markdown
    .replace("# 兆易创新研究报告", "# 兆易创新研究报告 · 合成模拟\n\n研究质量：完整")
    .replace("## 关键事实", `${scoreText}\n\n## 关键事实`);
}

type RouteApiOptions = {
  sessionDelayMs?: number;
  runningSession?: boolean;
  failCancel?: boolean;
  holdSecondSessionRead?: boolean;
  failSecondSessionRead?: boolean;
  disconnectSecondSessionRead?: boolean;
  mixedHistory?: boolean;

  reportIntegrityFailure?: boolean;
  withoutCoverageGaps?: boolean;
  coverageGapCount?: number;
  resultCompleteness?: "partial" | "insufficient_evidence" | "execution_failed";
  limitedCompletion?: boolean;
  invalidAvailableCitation?: boolean;
  longTitle?: boolean;
  syntheticScore?: "complete" | "missing_dimensions";
  readableAvailable?: boolean;
  invalidScoreBasis?: boolean;
  reportV2?: boolean;
  realReport?: boolean;
  formalReport?: boolean;
  missingFormalProof?: boolean;
  sharedScoreSource?: boolean;
};

async function routeApi(page: Page, options: RouteApiOptions = {}) {
  const { report, session } = fixture();
  if (options.realReport) {
    session.source_mode = "real_development_only";
    report.reader_edition.safe_markdown += "\n\n## 资料审核与原文位置\n\nDEV_ONLY / UNVERIFIED\n\n独立资料审核通过；PDF page 12；期间 2025年。\n\n未通过的资料不进入评分。";
  }
  if (options.syntheticScore) applySyntheticScores(report, options.syntheticScore === "missing_dimensions");
  if (options.sharedScoreSource) {
    const first = report.claims?.[0];
    const second = report.claims?.[1];
    const source = second?.evidence?.[0];
    if (!first || !second || !source) throw new Error("Missing source fixture");
    first.claim_type = "fact";
    first.calculation = null;
    first.statement = "原创身份记录，同源而非唯一评分依据。";
    first.evidence = [source];
    second.statement = "原创主营业务记录，与身份共用一份来源。";
    report.reader_edition.safe_markdown += `\n\n支持来源：[查看来源](#source-${source.evidence_id})\n\n[未知来源](#source-unknown)\n\n[准确事实](#claim-${SECOND_CLAIM_ID})`;
  }
  let sessionReadCount = 0;
  let markHeldSessionReadStarted: () => void = () => undefined;
  let releaseHeldSessionRead: () => void = () => undefined;
  const heldSessionReadStarted = new Promise<void>((resolve) => {
    markHeldSessionReadStarted = resolve;
  });
  const heldSessionRead = new Promise<void>((resolve) => {
    releaseHeldSessionRead = resolve;
  });
  if (options.runningSession) {
    session.status = "running";
    session.retryable = false;
    session.progress = {
      ...session.progress,
      stage: "collecting_history",
      message: "正在整理证据。",
      checkpoint: "evidence-building",
    };
  }
  if (options.resultCompleteness) {
    session.status = "failed";
    session.result_completeness = options.resultCompleteness;
    session.retryable = options.resultCompleteness === "execution_failed";
    session.progress.stage = "validating_report";
    session.progress.message = "本次未形成完整报告，已保存通过验证的内容。";
    session.available_result = {
      schema_version: "1.0", source_mode: report.source_mode, as_of: report.as_of,
      non_production_notice: report.non_production_notice,
      claims: report.claims ?? [], sections: report.sections.slice(0, 2), coverage_gaps: report.coverage_gaps ?? [],
    };
    if (options.sharedScoreSource) session.available_result.scorecard = report.scorecard;
    if (options.readableAvailable) {
      session.available_result.schema_version = "1.1";
      session.available_result.sections = report.sections;
      session.available_result.scorecard = report.scorecard;
      session.available_result.reader_edition = {
        ...report.reader_edition,
        safe_markdown: report.reader_edition.safe_markdown.replace("研究质量：完整", "研究质量：部分完成，核心论点仍有证据缺口。"),
      };
    }
    if (options.invalidAvailableCitation && session.available_result?.claims?.[0]?.evidence?.[0]) {
      session.available_result.claims[0].evidence[0].content_sha256 = null;
    }
  }
  if (options.longTitle) report.instrument_name = "用于长标题换行验证的原创合成科技与产业研究公司（非真实研究对象）";
  if (options.limitedCompletion) {
    session.status = "succeeded";
    session.result_completeness = "partial";
    session.failure_reason_code = null;
    session.retryable = false;
    session.progress.stage = "completed";
    session.progress.message = "研究流程已结束，分析受限，未形成完整报告。";
  }
  if (options.withoutCoverageGaps) {
    report.coverage_gaps = [];
  }
  if (options.coverageGapCount !== undefined) {
    report.coverage_gaps = Array.from({ length: options.coverageGapCount }, (_, index) => ({
      area: `business.fixture_${index + 1}`,
      reason_code: "WEB_FACT_VALIDATION_FAILED",
      detail: `第 ${index + 1} 项证据缺口仍需补充核对。`,
    }));
  }
  if (options.reportV2) {
    for (const claim of report.claims ?? []) claim.counter_evidence = [];
    report.reader_edition.safe_markdown += "\n\n当前资料未找到直接反证；这不表示不存在风险或反对意见。";
    if (session.available_result) {
      session.available_result.schema_version = "2.0";
      session.available_result.claims = (report.claims ?? []).map((claim) => ({ ...claim, schema_version: "2.0" }));
      if (session.available_result.reader_edition) {
        session.available_result.reader_edition.safe_markdown += "\n\n当前资料未找到直接反证；这不表示不存在风险或反对意见。";
      }
    }
  }
  session.coverage_gaps = report.coverage_gaps ?? [];
  const historySessions = options.mixedHistory
    ? [
        session,
        {
          ...session,
          session_id: RUNNING_SESSION_ID,
          job_id: "30000000-0000-4000-8000-000000000100",
          instrument_id: "XSHE:000001",
          instrument_name: "运行中的合成研究",
          status: "running",
          retryable: false,
          completed_at: null,
          progress: {
            ...session.progress,
            stage: "collecting_history",
            message: "正在整理证据。",
            checkpoint: "evidence-building",
          },
        },
        {
          ...session,
          session_id: FAILED_SESSION_ID,
          job_id: "30000000-0000-4000-8000-000000000101",
          instrument_id: "XSHG:600000",
          instrument_name: "失败的合成研究",
          status: "failed",
          failure_reason_code: "REPORT_VALIDATION_FAILED",
          retryable: true,
          completed_at: null,
          progress: {
            ...session.progress,
            stage: "validating_report",
            message: "研究失败，可安全重试。",
            checkpoint: null,
          },
        },
      ]
    : [session];
  await page.route("**/api/v2/**", async (route) => {
    assertBearer(route);
    const request = route.request();
    const url = new URL(request.url());
    if (options.invalidScoreBasis && url.pathname === `/api/v2/research-sessions/${SESSION_ID}/view-v2.0`) {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: {
        ...session, available_result: { ...session.available_result,
          scorecard: { ...report.scorecard, scoring_basis: "unrecognized_scenario" },
        },
      } }) });
      return;
    }
    if (url.pathname === "/api/v2/instruments/directory-search") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          data: {
            schema_version: "1.1",
            query: url.searchParams.get("q"),
            searched_at: session.as_of,
            as_of: session.as_of,
            matches: [
              {
                schema_version: "1.1",
                instrument_id: "XSHG:603986",
                symbol: "603986",
                exchange: "XSHG",
                short_name: "兆易创新",
                legal_name: "北京兆易创新科技股份有限公司",
                legal_name_verified: true,
                listing_status: "active",
                match_type: "exact_short_name",
                revision_known_at: session.as_of,
              },
            ],
          },
        }),
      });
      return;
    }
    if (url.pathname === "/api/v2/research-skills") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ data: {
          schema_version: "1.0", instrument_id: "XSHG:603986",
          method_library_version: "ai-berkshire-fd83d06-stockassistant-1.0",
          items: skillIds.map((skill_id, index) => ({
            schema_version: "1.0", skill_id, display_name: `研究方法 ${index + 1}`,
            plain_language_purpose: "用证据检查公司质量。", group: index < 9 ? "core" : "conditional",
            is_core: index < 9, availability: "available", disabled_reason: null,
            method_version: "ai-berkshire-fd83d06-stockassistant-1.0",
          })),
        } }),
      });
      return;
    }
    if (url.pathname === "/api/v2/research-sessions" && request.method() === "POST") {
      expect(request.headers()["idempotency-key"]).toBeTruthy();
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          data: {
            schema_version: "1.0",
            session_id: SESSION_ID,
            job_id: session.job_id,
            status: "queued",
            accepted_at: session.as_of,
            idempotent_replay: false,
          },
        }),
      });
      return;
    }
    if (url.pathname === "/api/v2/research-sessions-v2.0" && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ data: { schema_version: "1.0", as_of: session.as_of, items: historySessions, has_more: false, next_cursor: null } }),
      });
      return;
    }
    if (url.pathname === `/api/v2/research-sessions/${SESSION_ID}/report-v2.2`) {
      if (options.reportIntegrityFailure) {
        await route.fulfill({
          status: 412,
          contentType: "application/problem+json",
          body: JSON.stringify({
            code: "PRECONDITION_FAILED",
            detail: "REPORT_COMPATIBILITY_PROJECTION_FAILED",
          }),
        });
        return;
      }
      const modernReport = options.reportV2 || options.realReport || options.formalReport
        ? { ...report, schema_version: options.formalReport ? "2.2" : options.realReport ? "2.1" : "2.0",
            source_mode: options.realReport ? "real_development_only" : report.source_mode,
            non_production_notice: options.realReport ? "DEV_ONLY / UNVERIFIED 真实资料私人研究" : report.non_production_notice,
            claims: (report.claims ?? []).map((claim) => ({ ...claim, schema_version: "2.0" })) }
        : report;
      if (options.formalReport) {
        for (const claim of modernReport.claims ?? []) {
          for (const citation of [...(claim.evidence ?? []), ...(claim.counter_evidence ?? [])]) {
            const url = "https://www.sse.com.cn/fixture/annual.pdf";
            citation.locator = url;
            Object.assign(citation, { formal_document: {
              document_id: REPORT_ID, evidence_id: citation.evidence_id,
              retrieval_receipt_id: SESSION_ID, extraction_scope: "selected_pages",
              fact_locations: { "business.core_products": {
                field: "normalized_original", start: 125, end: 190, page_number: 12,
              } },
              verification: {
                identity: { document_kind: "annual", issuer_name: "兆易创新",
                  instrument_id: "XSHG:603986", title: "2025 年年度报告",
                  native_document_id: "synthetic-annual-2025", canonical_url: url,
                  binding_basis: "document_fields", identity_anchors: [
                    { field: "normalized_original", start: 0, end: 4, page_number: 1 },
                  ], },
                publication_evidence: [{ kind: "document_disclosure", raw_value: citation.published_at,
                  published_at: citation.published_at, precision: "instant", timezone: "UTC",
                  canonical_url: url, location: { field: "发布日期", start: 40, end: 50 },
                  observation_sha256: "b".repeat(64), receipt_id: SESSION_ID }],
                published_at: citation.published_at, source_id: citation.source_provider_id,
                normalized_original_sha256: citation.content_sha256,
                rights_snapshot_sha256: citation.rights_snapshot_sha256,
                first_seen_at: citation.first_seen_at, pit_cutoff: citation.first_seen_at,
                media_type: "application/pdf",
              },
            } });
            if (options.missingFormalProof) Object.assign(citation, { formal_document: null });
          }
        }
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: modernReport }) });
      return;
    }
    if (
      options.failCancel &&
      url.pathname === `/api/v2/research-sessions/${SESSION_ID}/cancel` &&
      request.method() === "POST"
    ) {
      await route.fulfill({
        status: 503,
        contentType: "application/problem+json",
        body: JSON.stringify({ code: "CANCEL_TEMPORARILY_UNAVAILABLE", detail: "取消暂时不可用" }),
      });
      return;
    }
    if (url.pathname === `/api/v2/research-sessions/${SESSION_ID}/view-v2.0`) {
      sessionReadCount += 1;
      if (options.disconnectSecondSessionRead && sessionReadCount === 2) {
        await route.abort("failed");
        return;
      }
      if (options.failSecondSessionRead && sessionReadCount >= 2 && sessionReadCount <= 4) {
        await route.fulfill({
          status: 504,
          contentType: "application/problem+json",
          body: JSON.stringify({ code: "REQUEST_TIMEOUT", detail: "服务响应超时，请稍后再试" }),
        });
        return;
      }
      if ((options.failSecondSessionRead && sessionReadCount >= 5)
        || (options.disconnectSecondSessionRead && sessionReadCount >= 3)) {
        session.progress.message = "连接已恢复，继续核对资料";
      }
      if (options.holdSecondSessionRead && sessionReadCount === 2) {
        markHeldSessionReadStarted();
        await heldSessionRead;
        session.progress.message = "迟到的轮询响应已到达";
      }
      if ((options.sessionDelayMs ?? 0) > 0) {
        await new Promise((resolve) => setTimeout(resolve, options.sessionDelayMs));
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: session }) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/problem+json", body: JSON.stringify({ code: "NOT_FOUND", detail: "fixture route missing" }) });
  });
  return {
    waitForHeldSessionRead: () => heldSessionReadStarted,
    releaseHeldSessionRead,
  };
}

for (const mode of ["real", "synthetic", "unavailable"] as const) {
  test(`preview shell labels follow ${mode} runtime config`, async ({ page }) => {
    await page.route("**/runtime-config.json", async (route) => {
      if (mode === "unavailable") {
        await route.fulfill({ status: 503, body: "Unavailable" });
        return;
      }
      await route.fulfill({ json: mode === "real" ? {
        ...runtimeConfig,
        schema_version: "1.1",
        environment_label: "真实资料私人研究 · DEV_ONLY / UNVERIFIED",
        synthetic_data: false,
      } : runtimeConfig });
    });
    await page.goto("/");
    const label = mode === "real" ? "真实资料私人研究 · DEV_ONLY / UNVERIFIED"
      : mode === "synthetic" ? "私有合成 R2 预览" : "私人研究预览 · 配置待确认";
    if (mode === "unavailable") await expect(page.getByRole("button", { name: "认证不可用" })).toBeVisible();
    await expect(page.locator(".environment-chip")).toHaveText(label);
    await expect(page.locator(".site-footer")).toContainText(mode === "synthetic" ? "合成测试数据" : label);
    await expect(page.locator(".site-footer")).toContainText("不构成投资建议");
    if (mode !== "synthetic") await expect(page.locator(".site-footer")).not.toContainText("合成测试数据");
  });
}

test("requires a private login before research", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "使用用户名和密码登录" })).toBeVisible();
  await expect(page.getByRole("search")).toHaveCount(0);
});

test("real source audit text stays identical in screen download and print", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { realReport: true });
  await page.goto(`/research/?session=${SESSION_ID}`);
  const article = page.locator(".markdown-reader");
  await expect(article.getByRole("heading", { name: "资料审核与原文位置" })).toBeVisible();
  await expect(article).toContainText("PDF page 12");
  const pending = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 .md" }).click();
  const path = await (await pending).path();
  if (!path) throw new Error("Audit document download was not saved");
  const markdown = await readFile(path, "utf8");
  for (const text of ["DEV_ONLY / UNVERIFIED", "PDF page 12", "期间 2025年", "未通过的资料不进入评分"]) {
    expect(markdown).toContain(text);
    await expect(article).toContainText(text);
  }
  await page.emulateMedia({ media: "print" });
  await expect(article).toBeVisible();
  await expect(article).toContainText("独立资料审核通过");
});

test("searches, confirms, opens the readable report and drills into professional evidence", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page);
  await page.goto("/");

  await expect(page.getByText(/覆盖沪深北当前 A 股与沪深历史退市目录/)).toBeVisible();
  const search = page.getByRole("combobox", { name: "证券代码、简称或公司全称" });
  await search.fill("兆易创新");
  await page.getByRole("button", { name: "搜索 A 股" }).click();
  await page.getByRole("option", { name: /兆易创新/ }).click();
  await expect(page.getByRole("heading", { name: "兆易创新", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "创建深度研究" }).click();

  await expect(page).toHaveURL(new RegExp(`/research/?\\?session=${SESSION_ID}`));
  await expect(page.getByRole("heading", { name: "兆易创新", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "方法与来源" }).click();
  const scoreOverview = page.locator("[class~=score-overview]");
  await expect(scoreOverview.getByText("暂不评分", { exact: true })).toBeVisible();
  await expect(scoreOverview.getByText("这份历史合成报告没有保存模拟评分，继续保留原有结果。", { exact: true })).toBeVisible();
  await expect(scoreOverview.locator(".confidence-score")).toHaveCount(0);
  if ((page.viewportSize()?.width ?? 0) <= 820) {
    const mobileColumns = await scoreOverview.evaluate((element) =>
      getComputedStyle(element).gridTemplateColumns.split(" ").filter(Boolean),
    );
    expect(mobileColumns).toHaveLength(1);
  }
  const dimensions = page.locator("[class~=dimension-grid]");
  await expect(dimensions.getByText("合成资料不用于真实公司评分。", { exact: true })).toHaveCount(6);
  await expect(dimensions.locator("[class=dimension-track], b")).toHaveCount(0);
  await expect(dimensions.locator("button")).toHaveCount(0);
  await expect(dimensions.locator("[class~=dimension-row]")).toHaveCount(6);
  const dimensionOpacity = await dimensions
    .locator("[class~=dimension-row]")
    .first()
    .evaluate((element) => getComputedStyle(element).opacity);
  expect(dimensionOpacity).toBe("1");
  await page.getByRole("button", { name: "易读版" }).click();
  await expect(page.getByRole("heading", { name: "公司靠什么赚钱" })).toBeVisible();
  await expect(
    page.getByText("核心产品或服务: 当前证据未说明具体产品或服务。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("客户到收入链: 当前证据未说明客户如何转化为收入和利润。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("产品结构调整: 当前证据未说明具体产品或调整方向。", { exact: true }),
  ).toBeVisible();
  const coverageStatus = page.getByRole("region", { name: "证据缺口与补充状态" });
  const evidenceRecords = page.getByRole("region", { name: "可核对的证据记录" });
  await expect(coverageStatus.locator(".reader-gap-item")).toHaveCount(2);
  await expect(
    coverageStatus.getByText("联网候选均未通过当前证据规则，核心产品仍需补充核对。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    coverageStatus.getByText("business.core_products · WEB_DISCOVERY_LIMITED", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(evidenceRecords).toBeVisible();
  await expect(evidenceRecords.locator(".claim-button")).toHaveCount(2);
  await expect(
    evidenceRecords.getByRole("button", {
      name: /当前证据只确认公司业务结构仍需进一步核对/,
    }),
  ).toBeVisible();
  await expect(page.getByText(/模型辅助联网取得/)).toBeVisible();
  const readableEvidence = page.locator(".reader-evidence .claim-button").first();
  await expect(readableEvidence).toBeVisible();
  await readableEvidence.click();
  const readableLens = page.getByRole("dialog");
  await expect(readableLens).toBeVisible();
  await expect(readableLens.getByText("这条信息由模型辅助联网取得", { exact: true })).toBeVisible();
  await expect(readableLens.getByText("核验状态：尚未独立交叉验证", { exact: true })).toBeVisible();
  await expect(readableLens.getByText("评分可靠性系数：0.90", { exact: true })).toBeVisible();
  await readableLens.getByRole("button", { name: "关闭" }).click();
  await expect(readableEvidence).toBeFocused();
  const secondReadableEvidence = page.locator(".reader-evidence .claim-button").nth(1);
  await secondReadableEvidence.focus();
  await secondReadableEvidence.press("Enter");
  const secondReadableLens = page.getByRole("dialog", {
    name: /当前证据只确认公司业务结构仍需进一步核对/,
  });
  await expect(secondReadableLens).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(secondReadableLens).toHaveCount(0);
  await expect(secondReadableEvidence).toBeFocused();
  await page.getByRole("button", { name: "专业十区块" }).click();
  await expect(page.locator(".report-section")).toHaveCount(10);
  await expect(
    page.getByText("本区块暂无可核对的独立证据记录；当前摘要仅说明范围或方法。", {
      exact: true,
    }),
  ).toHaveCount(8);
  await expect(page.getByRole("heading", { name: "重大事件、国际事件与宏观暴露" })).toBeVisible();
  if ((page.viewportSize()?.width ?? 1440) <= 820) {
    const tocToggle = page.getByRole("button", { name: /报告目录/ });
    await expect(tocToggle).toHaveAttribute("aria-expanded", "false");
    await tocToggle.click();
    await expect(tocToggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("navigation", { name: "报告十区块目录" }).getByRole("link"))
      .toHaveCount(10);
  }
  const professionalEvidence = page.getByRole("button", {
    name: /该影响由确定性暴露规则生成/,
  });
  await professionalEvidence.focus();
  await professionalEvidence.press("Enter");
  const lens = page.getByRole("dialog", { name: /该影响由确定性暴露规则生成/ });
  await expect(lens).toBeVisible();
  const closeLens = lens.getByRole("button", { name: "关闭" });
  await expect(closeLens).toBeFocused();
  await closeLens.press("Tab");
  await expect(closeLens).toBeFocused();
  await closeLens.press("Shift+Tab");
  await expect(closeLens).toBeFocused();
  await expect(lens.getByText("one_hop_evidence")).toBeVisible();
  await expect(lens.getByText("technology_control")).toBeVisible();
  await expect(lens.getByText("反证 · 1")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(lens).toHaveCount(0);
  await expect(professionalEvidence).toBeFocused();
  await expect(page.getByText(/Buy|Sell|目标价|开启监控|添加关注/)).toHaveCount(0);

  const layout = await page.evaluate(() => ({
    viewport: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
  }));
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewport);
});

test("shows formal file proof and exact original locations without claiming complete extraction", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { formalReport: true });
  await page.goto(`/research/?session=${SESSION_ID}`);
  await page.locator(".markdown-reader").getByRole("button", { name: "查看依据" }).first().click();
  const lens = page.getByRole("dialog");
  await expect(lens.getByRole("heading", { name: "正式披露原文" }).first()).toBeVisible();
  await expect(lens.getByText("2025 年年度报告").first()).toBeVisible();
  await expect(lens.getByText(/第 12 页.*原文字符 125–190/).first()).toBeVisible();
  await expect(lens.getByText(/部分提取或范围未确认/).first()).toBeVisible();
  await expect(lens.getByRole("link", { name: "打开正式文件" }).first()).toHaveAttribute(
    "href", "https://www.sse.com.cn/fixture/annual.pdf",
  );
  await expect(lens.getByText(/不代表原 PDF 文件字节/).first()).toBeVisible();
});

test("rejects a new formal report when its proof is missing", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { formalReport: true, missingFormalProof: true });
  await page.goto(`/research/?session=${SESSION_ID}`);
  await expect(page.getByRole("alert").filter({ hasText: "服务返回了无法验证的数据结构" })).toBeVisible();
  await expect(page.locator(".markdown-reader")).toHaveCount(0);
});

test("opens a completed report from non-empty history without presenting unfinished work as complete", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { mixedHistory: true });
  await page.goto("/history/");

  const completedRow = page.locator(".history-row").filter({ hasText: "兆易创新" });
  const runningRow = page.locator(".history-row").filter({ hasText: "运行中的合成研究" });
  const failedRow = page.locator(".history-row").filter({ hasText: "失败的合成研究" });
  await expect(completedRow.getByRole("link", { name: "打开研究结果" })).toBeVisible();
  await expect(runningRow.getByRole("link", { name: "查看进度" })).toBeVisible();
  await expect(failedRow.getByRole("link", { name: "查看并恢复" })).toBeVisible();
  await expect(failedRow.getByRole("button", { name: "重试" })).toBeVisible();
  await expect(runningRow.getByRole("link", { name: "打开研究结果" })).toHaveCount(0);
  await expect(failedRow.getByRole("link", { name: "打开研究结果" })).toHaveCount(0);

  await Promise.all([
    page.waitForResponse((response) => (
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === `/api/v2/research-sessions/${SESSION_ID}/report-v2.2`
    )),
    completedRow.getByRole("link", { name: "打开研究结果" }).click(),
  ]);
  await expect(page).toHaveURL(new RegExp(`/research/?\\?session=${SESSION_ID}`));
  await expect(page.getByRole("button", { name: "易读版" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("heading", { name: "公司靠什么赚钱" })).toBeVisible();
  await page.getByRole("button", { name: "专业十区块" }).click();
  await expect(page.locator(".report-section")).toHaveCount(10);
});

test("reads the full article, opens inline evidence and exports without regenerating content", async ({ page }, testInfo) => {
  await seedIdentity(page);
  await routeApi(page, { longTitle: true });
  const reportRequests: string[] = [];
  page.on("request", (request) => { if (request.url().includes("/api/v2/")) reportRequests.push(request.url()); });
  await page.goto(`/research/?session=${SESSION_ID}`);
  const article = page.locator(".markdown-reader");
  await expect(article.getByRole("heading", { level: 2 })).toHaveCount(7);
  for (const text of ["客户验收均成立", "不能把潜在需求当成已实现收入", "应重新评估原判断"]) {
    await expect(article).toContainText(text);
  }
  const toc = page.getByRole("navigation", { name: "易读报告目录" });
  await toc.getByRole("link", { name: "看空分析（Bear）" }).click();
  await expect(article.getByRole("heading", { name: "看空分析（Bear）" })).toBeInViewport();
  const inline = article.getByRole("button", { name: "查看依据" }).last();
  await inline.scrollIntoViewIfNeeded();
  await inline.focus();
  const readingPosition = await page.evaluate(() => window.scrollY);
  await inline.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "关闭", exact: true }).press("Escape");
  await expect(inline).toBeFocused();
  expect(Math.abs(await page.evaluate(() => window.scrollY) - readingPosition)).toBeLessThan(3);
  await page.getByRole("button", { name: "方法与来源" }).click();
  await expect(page.getByText("解读来源：系统根据已验证报告整理", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "专业十区块" }).click();
  await expect(page.locator(".report-section")).toHaveCount(10);
  await page.getByRole("button", { name: "易读版" }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 .md" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("XSHG_603986-research.md");
  const downloadedPath = await download.path();
  if (!downloadedPath) throw new Error("Markdown download did not create an artifact");
  const downloadedText = await readFile(downloadedPath, "utf8");
  expect(downloadedText).toContain("不能把潜在需求当成已实现收入");
  expect(downloadedText).toContain(`#claim-${CLAIM_ID}`);
  await expect(article.getByRole("link")).toHaveCount(0);
  await expect(article.getByRole("button", { name: "未知引用" })).toHaveCount(0);
  expect(reportRequests.filter((url) => url.includes("report-v2.2"))).toHaveLength(1);
  expect(reportRequests.some((url) => /model|chat/.test(url))).toBe(false);
  await page.emulateMedia({ media: "print" });
  await expect(page.locator(".report-tabs")).toBeHidden();
  await expect(article.getByText(/不能把潜在需求当成已实现收入/)).toBeVisible();
  await page.emulateMedia({ media: "screen" });
  if ((page.viewportSize()?.width ?? 0) < 500) await page.setViewportSize({ width: 360, height: 844 });
  const layout = await page.evaluate(() => ({ width: window.innerWidth, scroll: document.documentElement.scrollWidth }));
  expect(layout.scroll).toBeLessThanOrEqual(layout.width);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath("longform-reader.png"), fullPage: true });
});

for (const partial of [false, true]) {
  test(`reads, exports and prints the ${partial ? "partial" : "complete"} synthetic scored document`, async ({ page }, testInfo) => {
    await seedIdentity(page);
    await routeApi(page, partial
      ? { syntheticScore: "complete", resultCompleteness: "partial", readableAvailable: true, reportV2: true }
      : { syntheticScore: "complete", reportV2: true });
    const requests: string[] = [];
    page.on("request", (request) => { if (request.url().includes("/api/v2/")) requests.push(request.url()); });
    await page.goto(`/research/?session=${SESSION_ID}`);
    const article = page.locator(".markdown-reader");
    await expect(article.getByRole("heading", { level: 2 })).toHaveCount(7);
    await expect(page.locator(".reader-masthead .eyebrow")).toHaveText("研究报告 · 合成模拟");
    const quality = page.locator(".reader-report > .report-notice strong");
    await expect(quality).toContainText(partial ? "部分完成" : "研究质量：完整");
    await expect(article).toContainText("模拟总分：63.8 / 100");
    await expect(article).toContainText("真实来源核验：不适用（合成）");
    await expect(article).toContainText("主要问题与限制");
    if (partial) {
      await expect(page.locator(".status-chip")).toContainText("部分完成");
      await expect(article).toContainText("核心论点仍有证据缺口");
      await expect(page.getByRole("button", { name: "恢复研究" })).toHaveCount(0);
      await expect(page.getByRole("list", { name: "研究阶段" })).toBeHidden();
      const progress = page.getByText("查看研究执行进度", { exact: true });
      await progress.click();
      await expect(page.getByRole("list", { name: "研究阶段" })).toBeVisible();
      await progress.click();
    }
    await page.getByRole("button", { name: "方法与来源" }).click();
    const score = page.getByRole("region", { name: "模拟评分", exact: true });
    await expect(score.locator(".score-primary > strong")).toHaveText("63.8");
    await expect(score).toContainText("真实来源核验：不适用（合成）");
    await expect(score.locator(".confidence-score")).toHaveCount(0);
    const dimensions = page.getByRole("region", { name: "六项评分" });
    await expect(dimensions.locator(".dimension-row")).toHaveCount(6);
    await expect(dimensions.locator(".dimension-row > b")).toHaveText(["75.0", "50.0", "75.0", "50.0", "50.0", "75.0"]);
    const dimensionEvidence = dimensions.getByRole("button").first();
    await dimensionEvidence.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(dimensionEvidence).toBeFocused();
    await page.getByRole("button", { name: "专业十区块" }).click();
    await expect(page.locator(".report-section")).toHaveCount(10);
    await expect(page.getByRole("region", { name: "模拟评分", exact: true }).locator(".score-primary > strong")).toHaveText("63.8");
    await expect(quality).toContainText(partial ? "部分完成" : "研究质量：完整");
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载 .md" }).click();
    const download = await downloadPromise;
    const path = await download.path();
    if (!path) throw new Error("Scored Markdown download was not saved");
    const markdown = await readFile(path, "utf8");
    expect(markdown).toContain("研究报告 · 合成模拟");
    expect(markdown).toContain("模拟总分：63.8 / 100");
    expect(markdown).toContain("当前资料未找到直接反证");
    expect(markdown).toContain(partial ? "研究质量：部分完成" : "研究质量：完整");
    expect(markdown).toContain(`#claim-${SECOND_CLAIM_ID}`);
    expect(markdown.match(/^## /gm)).toHaveLength(7);
    await page.emulateMedia({ media: "print" });
    await expect(page.locator(".professional-report")).toBeHidden();
    await expect(article).toBeVisible();
    await expect(article.getByText("模拟总分：63.8 / 100", { exact: true })).toBeVisible();
    await expect(quality).toBeVisible();
    await page.emulateMedia({ media: "screen" });
    await page.getByRole("button", { name: "易读版" }).click();
    await expect(article.getByRole("heading", { level: 2 })).toHaveCount(7);
    expect(requests.filter((url) => url.includes("report-v2.2"))).toHaveLength(partial ? 0 : 1);
    expect(requests.some((url) => /model|chat/.test(url))).toBe(false);
    const layout = await page.evaluate(() => ({ width: window.innerWidth, scroll: document.documentElement.scrollWidth }));
    expect(layout.scroll).toBeLessThanOrEqual(layout.width);
    await page.screenshot({ path: testInfo.outputPath("synthetic-scored-document.png"), fullPage: true });
  });
}

test("retains four scored dimensions when the synthetic snapshot cannot support a total", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { syntheticScore: "missing_dimensions", resultCompleteness: "partial", readableAvailable: true });
  await page.goto(`/research/?session=${SESSION_ID}`);
  await expect(page.locator(".markdown-reader")).toContainText("模拟总分：未评分");
  await page.getByRole("button", { name: "方法与来源" }).click();
  await expect(page.getByRole("region", { name: "模拟评分", exact: true }).locator(".score-primary > strong")).toHaveText("暂不评分");
  const dimensions = page.getByRole("region", { name: "六项评分" });
  await expect(dimensions.locator(".dimension-row > b")).toHaveText(["75.0", "50.0", "未评分", "未评分", "50.0", "75.0"]);
  await expect(dimensions.locator(".dimension-track")).toHaveCount(4);
  await expect(dimensions.getByText("当前合成快照缺少本维度事实，不能补分。", { exact: true })).toHaveCount(2);
});

test("rejects an unknown scoring basis in a retained document", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { syntheticScore: "complete", resultCompleteness: "partial", readableAvailable: true, invalidScoreBasis: true });
  await page.goto(`/research/?session=${SESSION_ID}`);
  await expect(page.locator(".error-banner[role=alert]")).toContainText("无法验证的数据结构");
  await expect(page.locator(".reader-report")).toHaveCount(0);
});

for (const [completeness, label] of [
  ["partial", "部分完成"], ["insufficient_evidence", "证据不足"], ["execution_failed", "执行失败"],
] as const) {
  test(`retains validated ${completeness} content without declaring a complete report`, async ({ page }) => {
    await seedIdentity(page);
    await routeApi(page, { resultCompleteness: completeness });
    await page.goto(`/research/?session=${SESSION_ID}`);
    await expect(page.locator(".status-chip")).toContainText(label);
    await expect(page.getByRole("heading", { name: "可用结果" })).toBeVisible();
    await expect(page.getByText("未完成 · 已保留的研究内容", { exact: true })).toBeVisible();
    await expect(page.locator(".available-result")).toContainText("合成测试数据");
    await expect(page.getByRole("button", { name: "易读版" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "恢复研究" })).toHaveCount(completeness === "execution_failed" ? 1 : 0);
    const evidence = page.locator(".available-result .claim-button").first();
    await evidence.click();
    await expect(page.getByRole("dialog")).toContainText("反证 · 1");
    await page.getByRole("button", { name: "关闭", exact: true }).press("Escape");
    await expect(evidence).toBeFocused();
  });
}

test("rejects a retained result whose web citation lacks its content identity", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { resultCompleteness: "partial", invalidAvailableCitation: true });
  await page.goto(`/research/?session=${SESSION_ID}`);
  await expect(page.locator(".error-banner[role=alert]")).toContainText("无法验证的数据结构");
  await expect(page.getByRole("heading", { name: "可用结果" })).toHaveCount(0);
});

test("shows a clear empty gap state", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { withoutCoverageGaps: true });

  await page.goto(`/research/?session=${SESSION_ID}`);

  const coverageStatus = page.getByRole("region", { name: "证据缺口与补充状态" });
  await expect(
    coverageStatus.getByText("当前未记录额外证据缺口", { exact: true }),
  ).toBeVisible();
  await expect(coverageStatus.locator(".reader-gap-item")).toHaveCount(0);
});

test("shows every gap even when markdown summarizes after twenty", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { coverageGapCount: 21 });

  await page.goto(`/research/?session=${SESSION_ID}`);

  const coverageStatus = page.getByRole("region", { name: "证据缺口与补充状态" });
  await expect(coverageStatus.locator(".reader-gap-item")).toHaveCount(21);
  await expect(
    coverageStatus.getByText("第 21 项证据缺口仍需补充核对。", { exact: true }),
  ).toBeVisible();
});

test("explains a retained report integrity failure in Chinese", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { mixedHistory: true, reportIntegrityFailure: true });
  await page.goto("/history/");

  const completedRow = page.locator(".history-row").filter({ hasText: "兆易创新" });
  await completedRow.getByRole("link", { name: "打开研究结果" }).click();

  await expect(page.locator(".error-banner[role=alert]")).toContainText(
    "这份研究成品未通过完整性校验，暂时无法打开；原始记录未被改写。",
  );
});

test("reduced motion removes visible transitions", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await seedIdentity(page);
  await routeApi(page);
  await page.goto("/");
  await expect(page.locator(".page-enter")).toHaveCSS(
    "animation-duration",
    /^(?:0\.001ms|1e-06s)$/,
  );
});

test("navigation cancellation never exposes a raw browser abort message", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { sessionDelayMs: 1_000 });

  await page.goto(`/research/?session=${SESSION_ID}`);
  await page.goto("/history/");

  await expect(page.getByRole("heading", { name: "七日研究历史" })).toBeVisible();
  await expect(page.getByText(/signal is aborted|AbortError|TimeoutError/i)).toHaveCount(0);
});

test("a recovered poll removes the stale read error", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { runningSession: true, failSecondSessionRead: true });
  await page.goto("/research/?session=" + SESSION_ID);
  await expect(page.getByRole("alert").filter({ hasText: "服务响应超时" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("连接已恢复，继续核对资料")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("alert").filter({ hasText: "服务响应超时" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "取消研究" })).toBeVisible();
});

test("a recovered network failure removes the plain-language read warning", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { runningSession: true, disconnectSecondSessionRead: true });
  await page.goto("/research/?session=" + SESSION_ID);
  const warning = page.getByRole("alert").filter({ hasText: "网络连接暂时中断" });
  await expect(warning).toHaveText("网络连接暂时中断，请稍后查看研究进度。", { timeout: 10_000 });
  await expect(page.getByText("连接已恢复，继续核对资料")).toBeVisible({ timeout: 15_000 });
  await expect(warning).toHaveCount(0);
});

test("a late successful poll never hides a failed session operation", async ({ page }) => {
  await seedIdentity(page);
  const api = await routeApi(page, {
    runningSession: true,
    failCancel: true,
    holdSecondSessionRead: true,
  });
  await page.goto(`/research/?session=${SESSION_ID}`);
  const cancel = page.getByRole("button", { name: "取消研究" });
  await expect(cancel).toBeVisible();

  await api.waitForHeldSessionRead();
  await cancel.click();
  await expect(page.getByRole("alert").filter({ hasText: "取消暂时不可用" })).toBeVisible();
  api.releaseHeldSessionRead();
  await expect(page.getByText("迟到的轮询响应已到达")).toBeVisible();

  await expect(page.getByRole("alert").filter({ hasText: "取消暂时不可用" })).toBeVisible();
});

for (const retained of [false, true]) {
  test(`score source lens preserves shared-source facts ${retained ? "retained" : "complete"}`, async ({ page }) => {
    await seedIdentity(page);
    await routeApi(page, { syntheticScore: "complete", sharedScoreSource: true,
      ...(retained ? { resultCompleteness: "partial", readableAvailable: true } as const : {}),
    });
    await page.goto(`/research/?session=${SESSION_ID}`);
    const sourceButton = page.getByRole("button", { name: "查看来源", exact: true });
    await sourceButton.click();
    const lens = page.getByRole("dialog");
    await expect(lens.getByRole("heading", { name: "引用来源", exact: true })).toBeVisible();
    await expect(lens.getByText("原创身份记录，同源而非唯一评分依据。", { exact: true })).toBeVisible();
    await expect(lens.getByText("原创主营业务记录，与身份共用一份来源。", { exact: true })).toBeVisible();
    await expect(lens.locator(".citation")).toHaveCount(1);
    await page.getByRole("button", { name: "关闭", exact: true }).press("Escape");
    await expect(sourceButton).toBeFocused();
    await expect(page.getByRole("button", { name: "未知来源", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "准确事实", exact: true }).click();
    await expect(lens.getByRole("heading", { level: 2 })).toHaveText("原创主营业务记录，与身份共用一份来源。");
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    for (const tab of ["方法与来源", "专业十区块"]) {
      await page.getByRole("button", { name: tab, exact: true }).click();
      const dimension = page.locator(".dimension-row").first();
      const support = dimension.getByRole("button", { name: "支持依据 1", exact: true });
      await support.click();
      await expect(lens.getByRole("heading", { level: 2 })).toContainText("支持来源");
      await expect(lens.getByRole("heading", { name: "同源事实（供核对）", exact: true })).toBeVisible();
      await expect(lens).toContainText("原创主营业务记录，与身份共用一份来源。");
      await page.getByRole("button", { name: "关闭", exact: true }).click();
      await expect(support).toBeFocused();
      await dimension.getByRole("button", { name: "反证依据 1", exact: true }).click();
      await expect(lens.getByRole("heading", { level: 2 })).toContainText("反证来源");
      await expect(lens.locator(".citation.is-counter")).toHaveCount(1);
      await expect(lens).toContainText("40000000-0000-4000-8000-000000000100");
      await page.getByRole("button", { name: "关闭", exact: true }).click();
    }
  });
}

test("score source lens also works before a retained reader exists", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, { syntheticScore: "complete", sharedScoreSource: true, resultCompleteness: "partial" });
  await page.goto(`/research/?session=${SESSION_ID}`);
  await expect(page.getByRole("button", { name: "易读版", exact: true })).toHaveCount(0);
  const button = page.locator(".dimension-row").first().getByRole("button", { name: "支持依据 1", exact: true });
  await button.click();
  const lens = page.getByRole("dialog");
  await expect(lens.getByRole("heading", { level: 2 })).toContainText("支持来源");
  await expect(lens).toContainText("原创身份记录，同源而非唯一评分依据。");
  await expect(lens).toContainText("原创主营业务记录，与身份共用一份来源。");
  await page.getByRole("button", { name: "关闭", exact: true }).press("Escape");
  await expect(button).toBeFocused();
});


test("distinguishes a newly completed limited analysis from a complete report", async ({ page }) => {
  await seedIdentity(page);
  await routeApi(page, {
    resultCompleteness: "partial", limitedCompletion: true,
    readableAvailable: true, syntheticScore: "missing_dimensions", reportV2: true,
  });
  const reports: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/report-v2.2")) reports.push(request.url());
  });
  await page.goto("/");
  await expect(page.locator(".recent-strip .status-chip")).toContainText("分析受限");
  await page.goto("/history/");
  await expect(page.locator(".history-row .status-chip")).toContainText("分析受限");
  await page.getByRole("link", { name: "打开研究结果" }).click();
  await expect(page.locator(".status-chip")).toContainText("未形成完整报告");
  await expect(page.locator(".reader-report")).toBeVisible();
  await page.getByText("查看研究执行进度", { exact: true }).click();
  await expect(page.getByText("研究流程结束", { exact: true })).toBeVisible();
  await expect(page.getByText("完整报告就绪", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "恢复研究" })).toHaveCount(0);
  const evidence = page.getByRole("button", { name: /查看来源与反证/ }).first();
  await evidence.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(reports).toEqual([]);
});
