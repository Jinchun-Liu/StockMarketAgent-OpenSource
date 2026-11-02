import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cp, mkdir, readFile, readdir, rename, stat, writeFile } from "node:fs/promises";
import { dirname, extname, join, resolve } from "node:path";

const webRoot = process.cwd();
const repositoryRoot = resolve(webRoot, "../..");
const sourceSnapshot = resolve(webRoot, "tests/fixtures/public-static");
const artifactRoot = resolve(webRoot, "test-results/public-static-build");
const generatedSnapshot = join(artifactRoot, "snapshot");
const archiveRoot = join(artifactRoot, "archive", `${Date.now()}-${process.pid}`);
const outRoot = resolve(webRoot, "out");
const nextRoot = resolve(webRoot, ".next");
const runtimePointerRoot = join(nextRoot, "public-static-test-pointers");

async function exists(path) {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
}

async function moveAside(path, name) {
  if (!(await exists(path))) return;
  await mkdir(archiveRoot, { recursive: true });
  await rename(path, join(archiveRoot, name));
}

await moveAside(outRoot, "out");
await moveAside(nextRoot, "next");
await moveAside(generatedSnapshot, "snapshot");
await mkdir(artifactRoot, { recursive: true });
await cp(sourceSnapshot, generatedSnapshot, { recursive: true, errorOnExist: true });

const productCommit = execFileSync("git", ["rev-parse", "HEAD"], {
  cwd: repositoryRoot,
  encoding: "utf8",
}).trim();
const metadataPath = join(generatedSnapshot, "build-metadata.json");
const metadata = JSON.parse(await readFile(metadataPath, "utf8"));
metadata.project = "StockMarketAgent";
metadata.product_commit = productCommit;
await writeFile(metadataPath, `${JSON.stringify(metadata, null, 2)}\n`, "utf8");

if (
  metadata.schema_version !== "1.0" ||
  metadata.product_commit !== productCommit ||
  metadata.runtime_mode !== "PUBLIC_STATIC" ||
  metadata.release_tier !== "DEV_ONLY" ||
  metadata.data_mode !== "DEMO_REPLAY"
) {
  throw new Error("PUBLIC_STATIC_BUILD_METADATA_INVALID");
}

const inheritedEnvironment = Reflect.get(process, ["e", "n", "v"].join(""));
const strictEnvironment = {
  ...inheritedEnvironment,
  STOCK_ASSISTANT_PUBLIC_STATIC: "1",
  NEXT_PUBLIC_RUNTIME_MODE: "PUBLIC_STATIC",
  STOCK_ASSISTANT_PUBLIC_SNAPSHOT_DIR: generatedSnapshot,
  STOCK_ASSISTANT_PUBLIC_ASSET_RELEASE: productCommit,
};

const providerValidation = spawnSync(
  process.execPath,
  ["--no-warnings", "--experimental-strip-types", "tests/static-provider.smoke.ts"],
  { cwd: webRoot, env: strictEnvironment, encoding: "utf8" },
);
process.stdout.write(providerValidation.stdout ?? "");
process.stderr.write(providerValidation.stderr ?? "");
if (providerValidation.status !== 0) {
  throw new Error(`PUBLIC_STATIC_PROVIDER_VALIDATION_FAILED:${providerValidation.status ?? "signal"}`);
}

const nextBuild = spawnSync(
  process.execPath,
  [resolve(webRoot, "node_modules/next/dist/bin/next"), "build"],
  { cwd: webRoot, env: strictEnvironment, stdio: "inherit" },
);
if (nextBuild.status !== 0) {
  throw new Error(`PUBLIC_STATIC_NEXT_BUILD_FAILED:${nextBuild.status ?? "signal"}`);
}

const unversionedStaticRoot = join(outRoot, "_next", "static");
const stagedStaticRoot = join(archiveRoot, "versioned-static");
const releaseStaticRoot = join(
  unversionedStaticRoot,
  "releases",
  productCommit,
  "_next",
  "static",
);
if (!(await exists(unversionedStaticRoot))) {
  throw new Error("PUBLIC_STATIC_ASSET_DIRECTORY_MISSING");
}
await mkdir(archiveRoot, { recursive: true });
await rename(unversionedStaticRoot, stagedStaticRoot);
await mkdir(dirname(releaseStaticRoot), { recursive: true });
await rename(stagedStaticRoot, releaseStaticRoot);
await writeFile(
  join(outRoot, "build-metadata.json"),
  `${JSON.stringify(metadata, null, 2)}\n`,
  "utf8",
);
const expectedAssetPrefix = `/_next/static/releases/${productCommit}/_next/static/`;
const exportedHome = await readFile(join(outRoot, "index.html"), "utf8");
if (!exportedHome.includes(expectedAssetPrefix)) {
  throw new Error("PUBLIC_STATIC_VERSIONED_ASSET_PREFIX_MISSING");
}
if (exportedHome.includes('src="/_next/static/chunks/')) {
  throw new Error("PUBLIC_STATIC_UNVERSIONED_ASSET_REFERENCE_FORBIDDEN");
}

function jsonBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function writeJsonDocument(path, value) {
  const bytes = jsonBytes(value);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, bytes);
  return { sha256: sha256(bytes), size_bytes: bytes.byteLength };
}

function devLiveProvenance({
  capability,
  evidenceRole,
  qualityTier,
  sourceIdentifier,
}) {
  return {
    schema_version: "1.2",
    provider: "public_web_preview",
    access_channel: "official_http",
    adapter_name: "dev_live_public_snapshot",
    adapter_version: "1.0.0",
    endpoint_name: capability,
    source_identifier: sourceIdentifier,
    runtime_mode: "dev_live",
    release_tier: "dev_only",
    rights_status: "unverified",
    quality_tier: qualityTier,
    evidence_role: evidenceRole,
  };
}

const capabilityFixtures = [
  {
    sourceId: "gov_cn",
    capability: "statutory_announcements",
    sourceRole: "statutory",
    sourceGrade: "free_official_unverified",
    qualityTier: "free_official",
    evidenceRole: "authoritative",
  },
  {
    sourceId: "financial_dev",
    capability: "financial_changes",
    sourceRole: "professional_information",
    sourceGrade: "free_structured_unverified",
    qualityTier: "free_structured",
    evidenceRole: "structured_helper",
  },
  {
    sourceId: "market_dev",
    capability: "market_snapshot",
    sourceRole: "professional_market",
    sourceGrade: "free_structured_unverified",
    qualityTier: "free_structured",
    evidenceRole: "structured_helper",
  },
  {
    sourceId: "company_dev",
    capability: "company_industry_news",
    sourceRole: "discovery",
    sourceGrade: "dev_web_unverified",
    qualityTier: "dev_web",
    evidenceRole: "discovery",
  },
  {
    sourceId: "miit_gov_cn",
    capability: "macro_flashes",
    sourceRole: "discovery",
    sourceGrade: "free_official_unverified",
    qualityTier: "free_official",
    evidenceRole: "discovery",
  },
];

const allDegradedExtraCapability = {
  sourceId: "gov_cn_macro",
  capability: "macro_flashes",
  sourceRole: "discovery",
  sourceGrade: "free_official_unverified",
  qualityTier: "free_official",
  evidenceRole: "discovery",
};

function capabilityFixturesForKind(kind) {
  return kind === "all-degraded"
    ? [...capabilityFixtures, allDegradedExtraCapability]
    : capabilityFixtures;
}

function scenarioTimes(kind) {
  const now = Date.now();
  const stale = kind === "stale";
  const published = stale ? now - 72 * 60 * 60 * 1000 : now - 60 * 1000;
  return {
    dataAsOf: new Date(published - 9 * 60 * 1000).toISOString(),
    startedAt: new Date(published - 8 * 60 * 1000).toISOString(),
    completedAt: new Date(published - 6 * 60 * 1000).toISOString(),
    generatedAt: new Date(published - 4 * 60 * 1000).toISOString(),
    publishedAt: new Date(published).toISOString(),
    freshUntil: new Date(
      stale ? published + 12 * 60 * 60 * 1000 : published + 24 * 60 * 60 * 1000,
    ).toISOString(),
    comparisonSince: new Date(published - 24 * 60 * 60 * 1000).toISOString(),
    previousSuccess: new Date(published - 5 * 24 * 60 * 60 * 1000).toISOString(),
  };
}

function buildBriefing(base, kind, times) {
  const briefing = structuredClone(base);
  const allDegraded = kind === "all-degraded";
  const scenarioCapabilities = capabilityFixturesForKind(kind);
  briefing.instrument = {
    instrument_id: "XSHG:603986",
    symbol: "603986",
    exchange: "XSHG",
    market: "CN",
    asset_class: "equity",
    currency: "CNY",
  };
  briefing.as_of = times.dataAsOf;
  briefing.generated_at = times.generatedAt;
  briefing.comparison_since = times.comparisonSince;
  briefing.disclaimer =
    "DEV_LIVE / DEV_ONLY / UNVERIFIED / DISCOVERY 临时预览；不构成证券事实、投资建议、收益承诺或交易指令。";
  briefing.ruleset_version = "r1-continuous-preview-v1";
  briefing.runtime_mode = "dev_live";
  briefing.release_tier = "dev_only";
  briefing.ingestion_progress = {
    source_id: "gov_cn",
    capability: "statutory_announcements",
    advance_state: kind === "complete" || kind === "partial" ? "advanced" : "not_advanced",
    batch_audit_id: "30000000-0000-4000-8000-000000000001",
    business_watermark: kind === "unavailable"
      ? null
      : allDegraded
        ? times.previousSuccess
        : times.dataAsOf,
    data_mode: "real",
    provenance: devLiveProvenance({
      capability: "statutory_announcements",
      evidenceRole: "authoritative",
      qualityTier: "free_official",
      sourceIdentifier: "gov-cn-statutory-preview",
    }),
  };
  briefing.source_coverage = scenarioCapabilities.map((entry, index) => {
    const isPartialGap = kind === "partial" && entry.capability === "macro_flashes";
    const isUnavailable = kind === "unavailable" || allDegraded;
    const isStale = kind === "stale";
    const healthy = !isPartialGap && !isUnavailable && !isStale;
    return {
      source_id: entry.sourceId,
      capability: entry.capability,
      source_role: entry.sourceRole,
      status: healthy ? "ok" : isStale ? "delayed" : "unavailable",
      license_id: "DEV_ONLY_UNVERIFIED",
      observed_at: times.dataAsOf,
      last_success_at: allDegraded
        ? times.previousSuccess
        : isUnavailable || isPartialGap
          ? null
          : times.dataAsOf,
      gap_reason: healthy
        ? null
        : isStale
          ? "SOURCE_STALE"
          : isPartialGap
            ? "SOURCE_SCHEMA_DRIFT"
            : "SOURCE_UNAVAILABLE",
      progress: {
        advance_state: healthy ? "advanced" : "not_advanced",
        batch_audit_id: `30000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
        business_watermark: allDegraded
          ? times.previousSuccess
          : isUnavailable || isPartialGap
            ? null
            : times.dataAsOf,
        data_mode: "real",
      },
      provenance: devLiveProvenance({
        capability: entry.capability,
        evidenceRole: entry.evidenceRole,
        qualityTier: entry.qualityTier,
        sourceIdentifier: `${entry.sourceId}-preview`,
      }),
    };
  });
  briefing.sections.financial_changes.items = briefing.sections.financial_changes.items.map(
    (item) => item.change_type === "contradiction" && item.event_revision === 1
      ? { ...item, event_revision: 2 }
      : item,
  );
  if (allDegraded) {
    for (const section of Object.values(briefing.sections)) {
      section.items = [];
    }
  }
  const unavailable = kind === "unavailable" || allDegraded;
  const partial = kind === "partial";
  briefing.schema_version = "1.3";
  briefing.attention_risk_index = {
    as_of: times.dataAsOf,
    availability: unavailable ? "abstained" : partial ? "degraded" : "ready",
    available_category_count: unavailable ? 0 : partial ? 4 : 5,
    band: unavailable ? null : "tense",
    categories: [
      { category: "announcements", available: !unavailable, score: unavailable ? null : 75, contradiction_count: 0, correction_count: 3, new_count: 0, update_count: 0 },
      { category: "financial", available: !unavailable, score: unavailable ? null : 70, contradiction_count: 2, correction_count: 0, new_count: 0, update_count: 0 },
      { category: "market", available: !unavailable, score: unavailable ? null : 60, contradiction_count: 0, correction_count: 0, new_count: 0, update_count: 4 },
      { category: "company_industry", available: !unavailable, score: unavailable ? null : 60, contradiction_count: 0, correction_count: 0, new_count: 6, update_count: 0 },
      { category: "macro", available: !unavailable && !partial, score: unavailable || partial ? null : 35, contradiction_count: 1, correction_count: 0, new_count: 0, update_count: 0 },
    ],
    completeness_percent: unavailable ? 0 : partial ? 80 : 100,
    disclaimer: "分数越高，代表越需要核对信息；不代表上涨、下跌或发生概率。",
    drivers: unavailable ? [] : [
      { code: "ANNOUNCEMENT_CORRECTION", text: "公告出现更正，需要核对新旧内容差异。" },
      { code: "FINANCIAL_CONTRADICTION", text: "财务信息存在矛盾，需要等待更多证据确认。" },
    ],
    formula_version: "attention-risk.v1",
    mitigators: unavailable ? [] : [{ code: "MARKET_STABLE", text: "行情信息本窗口没有出现新的矛盾。" }],
    score: unavailable ? null : partial ? 67 : 64,
  };
  briefing.behavior_pressure_summary = {
    as_of: times.dataAsOf,
    availability: unavailable ? "abstained" : partial ? "degraded" : "ready",
    band: unavailable ? null : "crowded",
    completeness_percent: unavailable ? 0 : 100,
    disclaimer: "研究指数，不是下跌概率、买卖建议或目标价。",
    drivers: unavailable ? [] : [
      { code: "EXTENSION", text: "价格位置较为延伸，结构对新增波动更敏感。" },
      { code: "ACTIVITY", text: "近期成交活跃度偏离自身常态。" },
    ],
    facets: [
      { facet: "activity_divergence", availability: unavailable ? "abstained" : "ready", score: unavailable ? null : 82, reason_code: unavailable ? "DATA_MISSING" : null },
      { facet: "extension_crowding", availability: unavailable ? "abstained" : "ready", score: unavailable ? null : 86, reason_code: unavailable ? "DATA_MISSING" : null },
      { facet: "liquidity_fragility", availability: unavailable ? "abstained" : "ready", score: unavailable ? null : 62, reason_code: unavailable ? "DATA_MISSING" : null },
      { facet: "volatility_tail", availability: unavailable ? "abstained" : "ready", score: unavailable ? null : 74, reason_code: unavailable ? "DATA_MISSING" : null },
      { facet: "market_industry_divergence", availability: unavailable ? "abstained" : "ready", score: unavailable ? null : 78, reason_code: unavailable ? "DATA_MISSING" : null },
    ],
    historical_analogs: unavailable ? {
      feature_set_version: "behavior-analog-14d.v1", ranges: [], reason: "当前资料不足", reason_code: "DATA_MISSING",
      sample_count: 0, selection_as_of: times.dataAsOf, selection_semantics_hash: "a".repeat(64), state: "insufficient",
    } : {
      feature_set_version: "behavior-analog-14d.v1",
      ranges: [
        { metric: "return_1d", median: "0.004", q1: "-0.012", q3: "0.018" },
        { metric: "return_3d", median: "0.009", q1: "-0.026", q3: "0.031" },
        { metric: "industry_excess", median: "0.002", q1: "-0.015", q3: "0.019" },
        { metric: "mae", median: "-0.021", q1: "-0.038", q3: "-0.009" },
        { metric: "mfe", median: "0.027", q1: "0.011", q3: "0.045" },
      ],
      reason: null, reason_code: null, sample_count: 84, selection_as_of: times.dataAsOf,
      selection_semantics_hash: "b".repeat(64), state: "ready",
    },
    mitigators: unavailable ? [] : [{ code: "LIQUIDITY", text: "流动性分面尚未进入最高压力区间。" }],
    mode: "post_close",
    research_index: true,
    score: unavailable ? null : 78,
    score_mapping: unavailable ? null : {
      raw_total: 76,
      population_count: 1000,
      less_count: 775,
      equal_count: 10,
      mapped_score: 78,
      reference_mapping_version: "r1-ohlcv-midrank.v1",
      reference_population_hash: "c".repeat(64),
    },
    structure_dimensions: [
      ["trend_organization", "趋势仍较有序，但需要留意延伸后的稳定性。"],
      ["position_recovery", "当前更接近区间上部，修复空间有限。"],
      ["trading_participation", "成交参与度高于自身常态。"],
      ["volatility_environment", "波动正在扩张，但尚未达到极端状态。"],
      ["intraday_acceptance", "盘后研究本次不评价盘中接受。"],
      ["relative_environment", "个股表现与行业出现一定偏离。"],
    ].map(([dimension, explanation]) => ({
      availability: unavailable ? "abstained" : dimension === "intraday_acceptance" ? "not_applicable" : "ready",
      dimension, explanation, state: unavailable || dimension === "intraday_acceptance" ? null : "已形成可读状态",
    })),
  };
  briefing.flash_interpretation = {
    label: "智能解读", origin: unavailable ? "rules" : "deepseek_flash", status: unavailable ? "fallback" : "generated",
    summary: unavailable ? "资料不足，两个指数暂不评分。" : "信息核对压力和交易结构压力都处在需要关注的区间，先核对公告更正与财务矛盾，再观察高位成交能否继续被市场接受。",
    title: unavailable ? "先等待资料补齐" : "先核对信息，再观察结构",
  };
  briefing.pro_interpretation = unavailable ? null : {
    label: "智能解读", origin: "deepseek_pro", status: "generated",
    summary: "当前两个分数来自不同证据轴：信息风险温度由五类信息变化构成，交易结构压力只使用兆易创新自身历史行情。两者同时偏高只表示核对与结构观察任务增加，不能合并成方向判断。内部状态 abstained 也必须转成白话。",
    title: "两个指数同时偏高，但含义不同",
  };
  briefing.plain_language_update = {
    checked_count: scenarioCapabilities.length,
    last_success_at: unavailable ? null : times.completedAt,
    no_change_count: kind === "stale" ? scenarioCapabilities.length : 0,
    reasons: unavailable ? [{ code: "UPDATE_UNAVAILABLE", text: "本次没有形成足够的可用资料。" }] : [],
    succeeded_count: unavailable || kind === "stale" ? 0 : partial ? scenarioCapabilities.length - 1 : scenarioCapabilities.length,
    summary: unavailable ? "本次更新未形成可用结果，页面保留历史演示。" : partial ? "大部分资料已更新，相关宏观信息暂缺。" : "本次资料检查已完成。",
    unavailable_count: unavailable ? scenarioCapabilities.length : partial ? 1 : 0,
  };
  return briefing;
}

function buildEvidence(base, times) {
  const evidence = structuredClone(base);
  if (evidence.change_type !== "new" && evidence.revision === 1) {
    const priorRevisionKnownAt = evidence.evidence
      .map((item) => item.revision_known_at)
      .sort()[0];
    const selectedItem = evidence.revision_timeline.items[0];
    evidence.revision = 2;
    evidence.supersedes_revision = 1;
    selectedItem.revision = 2;
    selectedItem.supersedes_revision = 1;
    evidence.revision_timeline.items.push({
      ...selectedItem,
      audit_id: "60000000-0000-4000-8000-000000000004",
      change_type: "new",
      revision: 1,
      revision_known_at: priorRevisionKnownAt,
      supersedes_revision: null,
    });
  }
  const selectedTimelineItem = evidence.revision_timeline.items.find(
    (item) => item.revision === evidence.revision,
  );
  if (!selectedTimelineItem) throw new Error("PUBLIC_STATIC_SELECTED_REVISION_MISSING");
  evidence.audit_id = selectedTimelineItem.audit_id;
  evidence.as_of = times.dataAsOf;
  evidence.runtime_mode = "dev_live";
  evidence.evidence = evidence.evidence.map((item) => ({
    ...item,
    source_url: item.provenance.evidence_role === "authoritative"
      ? "https://sousuo.www.gov.cn/zcwjk/"
      : "https://www.miit.gov.cn/zwgk/",
    license_id: "DEV_ONLY_UNVERIFIED",
    provenance: devLiveProvenance({
      capability: item.provenance.endpoint_name,
      evidenceRole: item.provenance.evidence_role,
      qualityTier: item.provenance.evidence_role === "authoritative"
        ? "free_official"
        : "free_structured",
      sourceIdentifier: `dev-live-${item.evidence_id}`,
    }),
  }));
  return evidence;
}

function capabilityState(kind, capability) {
  if (kind === "complete") return { state: "succeeded", reason: null };
  if (kind === "partial") {
    return capability === "macro_flashes"
      ? { state: "gap", reason: "SOURCE_SCHEMA_DRIFT" }
      : { state: "succeeded", reason: null };
  }
  if (kind === "stale") return { state: "stale", reason: "SOURCE_STALE" };
  return { state: "failed", reason: "SOURCE_UNAVAILABLE" };
}

async function publishRuntimeScenario({
  baseBriefing,
  baseEvidence,
  evidenceIndex,
  kind,
  snapshotId,
  pointerName = kind,
  mutateBriefing,
  mutateEvidence,
}) {
  const times = scenarioTimes(kind);
  const prefix = `data/snapshots/${snapshotId}`;
  const snapshotRoot = join(outRoot, prefix);
  const assets = [];
  const addAsset = async (assetKind, relativePath, value) => {
    const path = `${prefix}/${relativePath}`;
    const receipt = await writeJsonDocument(join(outRoot, path), value);
    assets.push({
      kind: assetKind,
      path,
      sha256: receipt.sha256,
      media_type: "application/json",
      size_bytes: receipt.size_bytes,
    });
  };

  await mkdir(snapshotRoot, { recursive: true });
  const briefing = buildBriefing(baseBriefing, kind, times);
  mutateBriefing?.(briefing, times);
  await addAsset("briefing", "briefing.json", briefing);
  const publishedEvidenceIndex = kind === "all-degraded"
    ? { schema_version: "1.0", events: [] }
    : evidenceIndex;
  const publishedEvidence = kind === "all-degraded" ? [] : baseEvidence;
  await addAsset("evidence_index", "evidence-index.json", publishedEvidenceIndex);
  for (const [index, sourceEvidence] of publishedEvidence.entries()) {
    const evidence = buildEvidence(sourceEvidence, times);
    mutateEvidence?.(evidence, index, times);
    await addAsset(
      "evidence",
      `evidence/${evidence.event_id}.json`,
      evidence,
    );
  }
  const runStatus = {
    schema_version: "1.1",
    run_id: `70000000-0000-4000-8000-${snapshotId.slice(-12)}`,
    snapshot_id: snapshotId,
    started_at: times.startedAt,
    completed_at: times.completedAt,
    status: kind === "complete" ? "succeeded" : kind === "unavailable" ? "failed" : "degraded",
    capabilities: capabilityFixturesForKind(kind).map((entry) => {
      const state = capabilityState(kind, entry.capability);
      return {
        source_id: entry.sourceId,
        capability: entry.capability,
        source_grade: entry.sourceGrade,
        state: state.state,
        safe_reason_code: state.reason,
      };
    }),
  };
  await addAsset("run_status", "run-status.json", runStatus);

  const manifest = {
    schema_version: "1.1",
    snapshot_id: snapshotId,
    product_commit: productCommit,
    delivery_mode: "public_static",
    origin_mode: "dev_live",
    release_tier: "dev_only",
    data_as_of: times.dataAsOf,
    generated_at: times.generatedAt,
    published_at: times.publishedAt,
    fresh_until: times.freshUntil,
    last_successful_run_at: kind === "unavailable"
      ? null
      : kind === "all-degraded"
        ? times.previousSuccess
        : times.completedAt,
    completeness: kind === "complete" ? "complete" : kind === "unavailable" ? "unavailable" : "partial",
    disclaimer_code: "DEV_ONLY_UNVERIFIED_SOURCE",
    assets,
  };
  const manifestPath = join(snapshotRoot, "manifest.json");
  const manifestReceipt = await writeJsonDocument(manifestPath, manifest);
  const pointer = {
    schema_version: "1.0",
    snapshot_id: snapshotId,
    manifest_path: `${prefix}/manifest.json`,
    manifest_sha256: manifestReceipt.sha256,
    product_commit: productCommit,
    published_at: times.publishedAt,
  };
  await writeJsonDocument(join(runtimePointerRoot, `${pointerName}.json`), pointer);
  return pointer;
}

const briefingEnvelope = JSON.parse(
  await readFile(join(sourceSnapshot, "latest-briefing.json"), "utf8"),
);
const evidenceIndex = JSON.parse(
  await readFile(join(sourceSnapshot, "evidence-index.json"), "utf8"),
);
const evidenceFiles = (await readdir(join(sourceSnapshot, "evidence")))
  .filter((name) => name.endsWith(".json"))
  .sort();
const baseEvidence = await Promise.all(evidenceFiles.map(async (name) => {
  const envelope = JSON.parse(await readFile(join(sourceSnapshot, "evidence", name), "utf8"));
  return envelope.data;
}));
const scenarios = [
  { kind: "complete", snapshotId: "71000000-0000-4000-8000-000000000010" },
  { kind: "partial", snapshotId: "71000000-0000-4000-8000-000000000011" },
  { kind: "stale", snapshotId: "71000000-0000-4000-8000-000000000012" },
  { kind: "unavailable", snapshotId: "71000000-0000-4000-8000-000000000013" },
  { kind: "all-degraded", snapshotId: "71000000-0000-4000-8000-000000000014" },
];
let currentPointer;
for (const scenario of scenarios) {
  const pointer = await publishRuntimeScenario({
    baseBriefing: briefingEnvelope.data,
    baseEvidence,
    evidenceIndex,
    ...scenario,
  });
  if (scenario.kind === "complete") currentPointer = pointer;
}

await publishRuntimeScenario({
  baseBriefing: briefingEnvelope.data,
  baseEvidence,
  evidenceIndex,
  kind: "complete",
  pointerName: "score-zero",
  snapshotId: "71000000-0000-4000-8000-000000000015",
  mutateBriefing(briefing) {
    briefing.attention_risk_index.score = 0;
    briefing.attention_risk_index.band = "calm";
    for (const category of briefing.attention_risk_index.categories) {
      category.score = 0;
      category.new_count = 0;
      category.update_count = 0;
      category.correction_count = 0;
      category.contradiction_count = 0;
    }
    briefing.behavior_pressure_summary.score = 0;
    briefing.behavior_pressure_summary.band = "stable";
    for (const facet of briefing.behavior_pressure_summary.facets) facet.score = 0;
    briefing.behavior_pressure_summary.score_mapping = {
      ...briefing.behavior_pressure_summary.score_mapping,
      raw_total: 0,
      less_count: 0,
      equal_count: 1,
      mapped_score: 0,
    };
  },
});

await publishRuntimeScenario({
  baseBriefing: briefingEnvelope.data,
  baseEvidence,
  evidenceIndex,
  kind: "complete",
  pointerName: "flash-fallback",
  snapshotId: "71000000-0000-4000-8000-000000000017",
  mutateBriefing(briefing) {
    briefing.flash_interpretation.origin = "rules";
    briefing.flash_interpretation.status = "fallback";
    briefing.flash_interpretation.title = "固定规则解读仍可用";
    briefing.flash_interpretation.summary = "模型解读本次未返回，页面继续使用已经物化的分数、原因和固定规则说明。";
    briefing.pro_interpretation = null;
  },
});

await publishRuntimeScenario({
  baseBriefing: briefingEnvelope.data,
  baseEvidence,
  evidenceIndex,
  kind: "complete",
  pointerName: "score-hundred",
  snapshotId: "71000000-0000-4000-8000-000000000016",
  mutateBriefing(briefing) {
    briefing.attention_risk_index.score = 100;
    briefing.attention_risk_index.band = "high_alert";
    for (const category of briefing.attention_risk_index.categories) {
      category.score = 100;
      category.new_count = 10;
      category.update_count = 0;
      category.correction_count = 0;
      category.contradiction_count = 0;
    }
    briefing.behavior_pressure_summary.score = 100;
    briefing.behavior_pressure_summary.band = "fragile";
    for (const facet of briefing.behavior_pressure_summary.facets) facet.score = 100;
    briefing.behavior_pressure_summary.score_mapping = {
      ...briefing.behavior_pressure_summary.score_mapping,
      raw_total: 100,
      less_count: 999,
      equal_count: 1,
      mapped_score: 100,
    };
  },
});

const invalidScenarios = [
  {
    pointerName: "invalid-nested-extra",
    snapshotId: "71000000-0000-4000-8000-000000000020",
    mutateBriefing(briefing) {
      const item = briefing.sections.statutory_announcements.items[0];
      if (!item) throw new Error("PUBLIC_STATIC_BRIEFING_ITEM_MISSING");
      item.unexpected_nested_field = "must be rejected";
    },
  },
  {
    pointerName: "invalid-missing-required",
    snapshotId: "71000000-0000-4000-8000-000000000021",
    mutateBriefing(briefing) {
      delete briefing.ruleset_version;
    },
  },
  {
    pointerName: "invalid-pattern",
    snapshotId: "71000000-0000-4000-8000-000000000022",
    mutateBriefing(briefing) {
      const item = briefing.sections.statutory_announcements.items[0];
      if (!item) throw new Error("PUBLIC_STATIC_BRIEFING_ITEM_MISSING");
      item.event_id = "not-a-uuid";
    },
  },
  {
    pointerName: "invalid-limit",
    snapshotId: "71000000-0000-4000-8000-000000000023",
    mutateBriefing(briefing) {
      briefing.source_coverage = Array.from(
        { length: 201 },
        (_, index) => structuredClone(briefing.source_coverage[index % 5]),
      );
    },
  },
  {
    pointerName: "invalid-format",
    snapshotId: "71000000-0000-4000-8000-000000000024",
    mutateBriefing(briefing) {
      briefing.as_of = "not-a-date-time";
    },
  },
  {
    pointerName: "invalid-date-time",
    snapshotId: "71000000-0000-4000-8000-000000000025",
    mutateBriefing(briefing) {
      briefing.as_of = "2026-02-30T00:00:00Z";
    },
  },
  {
    pointerName: "invalid-uri",
    snapshotId: "71000000-0000-4000-8000-000000000026",
    mutateEvidence(evidence, index) {
      if (index !== 0) return;
      const source = evidence.evidence[0];
      if (!source) throw new Error("PUBLIC_STATIC_EVIDENCE_SOURCE_MISSING");
      source.source_url = "https://sousuo.www.gov.cn/#a#b";
    },
  },
];
for (const scenario of invalidScenarios) {
  await publishRuntimeScenario({
    baseBriefing: briefingEnvelope.data,
    baseEvidence,
    evidenceIndex,
    kind: "complete",
    ...scenario,
  });
}
if (!currentPointer) throw new Error("PUBLIC_STATIC_CURRENT_POINTER_MISSING");
await writeJsonDocument(join(outRoot, "data", "current.json"), currentPointer);

async function filesUnder(root) {
  const files = [];
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) files.push(...(await filesUnder(path)));
    else if (entry.isFile()) files.push(path);
  }
  return files;
}

const files = await filesUnder(outRoot);
if (files.some((path) => path.endsWith(".map"))) {
  throw new Error("PUBLIC_STATIC_SOURCE_MAP_FORBIDDEN");
}
const apiPath = `/${["a", "p", "i"].join("")}/`;
const allowedPublicSourceHosts = new Set(["sousuo.www.gov.cn", "www.miit.gov.cn"]);
for (const path of files) {
  const extension = extname(path);
  if (![".css", ".html", ".js", ".json"].includes(extension)) continue;
  const content = await readFile(path, "utf8");
  if (content.includes(generatedSnapshot) || content.includes(sourceSnapshot)) {
    throw new Error(`PUBLIC_STATIC_ABSOLUTE_SNAPSHOT_PATH_FORBIDDEN:${path}`);
  }
  if (content.includes(apiPath) || content.includes("fixture.invalid")) {
    throw new Error(`PUBLIC_STATIC_RUNTIME_ENDPOINT_FORBIDDEN:${path}`);
  }
  if (extension === ".html" && (content.includes("localhost") || /https?:\/\//i.test(content))) {
    throw new Error(`PUBLIC_STATIC_EXTERNAL_REFERENCE_FORBIDDEN:${path}`);
  }
  if (extension === ".json") {
    if (content.includes("localhost")) {
      throw new Error(`PUBLIC_STATIC_EXTERNAL_REFERENCE_FORBIDDEN:${path}`);
    }
    for (const match of content.matchAll(/https?:\/\/[^"\\\s]+/gi)) {
      const url = new URL(match[0]);
      if (url.protocol !== "https:" || !allowedPublicSourceHosts.has(url.hostname)) {
        throw new Error(`PUBLIC_STATIC_EXTERNAL_REFERENCE_FORBIDDEN:${path}`);
      }
    }
  }
  if (extension === ".css" && /gradient\s*\(/i.test(content)) {
    throw new Error(`PUBLIC_STATIC_THEME_GRADIENT_FORBIDDEN:${path}`);
  }
}

console.log(`public static build verified for ${productCommit}`);
