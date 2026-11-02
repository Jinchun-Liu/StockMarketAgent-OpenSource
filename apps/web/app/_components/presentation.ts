export const BRIEFING_SECTIONS = [
  { key: "statutory_announcements", label: "法定公告", description: "比较窗口内，经契约校验后返回的法定公告变化条数。" },
  { key: "financial_changes", label: "财务变化", description: "比较窗口内，财务字段新增、更正或相互矛盾的返回条数。" },
  { key: "market_snapshot", label: "行情快照", description: "比较窗口内，行情快照中被系统识别为变化的返回条数。" },
  { key: "company_industry_news", label: "公司与行业新闻", description: "比较窗口内，与公司或行业相关且通过关联校验的变化条数。" },
  { key: "macro_flashes", label: "相关宏观快讯", description: "比较窗口内，与固定标的建立有效关联的宏观快讯变化条数。" },
] as const;

export type BriefingSectionKey = (typeof BRIEFING_SECTIONS)[number]["key"];

const GAP_REASON_LABELS: Readonly<Record<string, string>> = {
  CONTEXT_ASSOCIATION_INSUFFICIENT: "与兆易创新的关联还不够明确",
  DEV_LIVE_MACRO_NOT_QUALIFIED: "相关宏观信息暂未达到展示条件",
  R1_MACRO_RELATION_INSUFFICIENT: "宏观信息与公司的关联还不够明确",
  R1_MEDIA_UNCONFIRMED: "媒体线索仍待更多信息核对",
  R1_REPLAY_RIGHTS_BLOCKED: "这部分演示资料暂不可用",
  R1_REPLAY_SOURCE_FAILURE: "这部分资料本次未成功更新",
  R1_SOURCE_FACT_INELIGIBLE: "现有内容还不足以形成可展示事实",
  R1_SOURCE_NO_CHANGE: "本次没有发现新增变化",
  R1_SOURCE_UNAVAILABLE: "这部分资料暂时不可用",
  RESEARCH_DATA_AUTH_FAILED: "研究数据服务暂时无法完成身份确认",
  RESEARCH_DATA_CREDENTIAL_MISSING: "研究数据服务尚未在运行环境中填写访问密钥",
  RESEARCH_DATA_PERMISSION_BLOCKED: "当前套餐暂未开放这类资料",
  RESEARCH_DATA_RATE_LIMITED: "研究数据服务请求较多，稍后会自动再试",
  RESEARCH_DATA_REQUEST_REJECTED: "研究数据服务暂未接受本次请求",
  RESEARCH_DATA_RESPONSE_TOO_LARGE: "这次返回资料过多，系统已停止处理",
  RESEARCH_DATA_SCHEMA_CHANGED: "研究数据格式发生变化，正在重新适配",
  RESEARCH_DATA_TIMEOUT: "研究数据服务本次响应超时",
  RESEARCH_DATA_UPSTREAM_UNAVAILABLE: "研究数据服务本次暂不可用",
  SOURCE_SCHEMA_DRIFT: "资料格式发生变化，正在重新适配",
  SOURCE_STALE: "这部分资料不是最新一次结果",
  SOURCE_UNAVAILABLE: "这部分资料暂时不可用",
  SYNTHETIC_SOURCE_DELAY: "演示资料更新稍有延迟",
};

export const ATTENTION_CATEGORY_LABELS: Readonly<Record<string, string>> = {
  announcements: "法定公告",
  financial: "财务变化",
  market: "行情变化",
  company_industry: "公司与行业",
  macro: "相关宏观信息",
};

export const PRESSURE_FACET_LABELS: Readonly<Record<string, string>> = {
  activity_divergence: "成交活跃偏离",
  extension_crowding: "位置与拥挤",
  liquidity_fragility: "流动性承压",
  volatility_tail: "波动与尾部压力",
  market_industry_divergence: "个股与行业偏离",
};

export const STRUCTURE_DIMENSION_LABELS: Readonly<Record<string, string>> = {
  trend_organization: "趋势组织",
  position_recovery: "位置与修复",
  trading_participation: "成交参与",
  volatility_environment: "波动环境",
  intraday_acceptance: "盘中接受",
  relative_environment: "相对环境",
};

export const HISTORICAL_METRIC_LABELS: Readonly<Record<string, string>> = {
  return_1d: "随后 1 个交易日涨跌",
  return_3d: "随后 3 个交易日涨跌",
  industry_excess: "相对行业表现",
  mae: "期间最大不利变化",
  mfe: "期间最大有利变化",
};

export function plainLabel(labels: Readonly<Record<string, string>>, value: string): string {
  return labels[value] ?? "暂未归类";
}

const INTERPRETATION_TERM_LABELS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\bdata_as_of\b/gi, "信息更新到"],
  [/\bDEV_LIVE\b/gi, "开发阶段更新"],
  [/\bDEV_ONLY\b/gi, "仅用于功能体验"],
  [/\bUNVERIFIED\b/gi, "来源尚未正式核验"],
  [/\bDISCOVERY\b/gi, "只作为信息线索"],
  [/\babstained\b/gi, "资料不足，暂不评分"],
  [/\bdegraded\b/gi, "部分资料可用"],
  [/\bnot_applicable\b/gi, "本次不适用"],
  [/\bready\b/gi, "资料完整"],
  [/\bdeepseek_flash\b/gi, "快速智能解读"],
  [/\bdeepseek_pro\b/gi, "深入智能解读"],
  [/\bfallback\b/gi, "固定规则说明"],
  [/\bgenerated\b/gi, "智能解读已生成"],
  [/\bpost_close\b/gi, "盘后确认"],
  [/\bintraday_10m\b/gi, "盘中十分钟"],
];

export function plainInterpretationText(value: string): string {
  return INTERPRETATION_TERM_LABELS.reduce(
    (text, [pattern, replacement]) => text.replace(pattern, replacement),
    value,
  );
}

export function normalizeResearchScore(value: number | null): number | null {
  return value === null ? null : Math.min(100, Math.max(0, value));
}

type SourceCoverageSummary = {
  capability: string;
  status: string;
};

export function hasSourceCoverageGap(
  sourceCoverage: readonly SourceCoverageSummary[],
  hasBriefing: boolean,
): boolean {
  if (!hasBriefing || sourceCoverage.some((coverage) => coverage.status !== "ok")) {
    return true;
  }
  const freshCapabilities = new Set(
    sourceCoverage.map((coverage) => normalizeCapability(coverage.capability)),
  );
  return !BRIEFING_SECTIONS.every((section) => freshCapabilities.has(section["key"]));
}

export function safeGapReason(value: string | null | undefined): string {
  if (!value) return "暂无额外说明";
  return GAP_REASON_LABELS[value] ?? "原因正在整理，页面不会据此补写结果";
}

export function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

const STRUCTURED_TEXT_FALLBACK = "这条结构化信息已经完成整理，详细字段暂不直接展示。";

interface MarketNumber {
  numeric: number;
  text: string;
}

function marketNumber(value: unknown): MarketNumber | null {
  if (
    typeof value !== "number" &&
    (typeof value !== "string" || !/^-?\d+(?:\.\d+)?$/.test(value))
  ) {
    return null;
  }
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric)
    ? {
        numeric,
        text: numeric.toLocaleString("zh-CN", { maximumFractionDigits: 4 }),
      }
    : null;
}

function isUnknownStructuredText(value: string): boolean {
  try {
    JSON.parse(value);
    return true;
  } catch {
    return /^[A-Za-z_][A-Za-z0-9_.-]{0,127}\s*=/.test(value);
  }
}

export function plainFactText(value: string, eventType?: string): string {
  const trimmed = value.trim();
  const payload = trimmed.startsWith("market.snapshot=")
    ? trimmed.slice("market.snapshot=".length)
    : trimmed;
  if (eventType === "market.snapshot" || trimmed.startsWith("market.snapshot=")) {
    try {
      const parsed = JSON.parse(payload) as unknown;
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return STRUCTURED_TEXT_FALLBACK;
      }
      const record = parsed as Record<string, unknown>;
      const close = marketNumber(record.close);
      const change = marketNumber(record.change);
      const percentChange = marketNumber(record.pct_chg);
      const high = marketNumber(record.high);
      const low = marketNumber(record.low);
      const volume = marketNumber(record.vol);
      const parts = [
        close ? `收盘价 ${close.text}` : null,
        percentChange
          ? `涨跌幅 ${percentChange.numeric > 0 ? "+" : ""}${percentChange.text}%`
          : null,
        change ? `价格变化 ${change.numeric > 0 ? "+" : ""}${change.text}` : null,
        high ? `最高 ${high.text}` : null,
        low ? `最低 ${low.text}` : null,
        volume ? `成交量 ${volume.text}` : null,
      ].filter((item): item is string => item !== null);
      return parts.length > 0 ? parts.join("，") : STRUCTURED_TEXT_FALLBACK;
    } catch {
      return STRUCTURED_TEXT_FALLBACK;
    }
  }
  if (isUnknownStructuredText(trimmed)) {
    return STRUCTURED_TEXT_FALLBACK;
  }
  return trimmed;
}

export function modeLabel(value: string): string {
  switch (value) {
    case "demo_replay":
      return "历史演示";
    case "dev_live":
      return "开发阶段更新";
    case "public_static":
      return "公开演示快照";
    case "authorized":
      return "正式授权资料";
    default:
      return "资料状态待确认";
  }
}

export function developmentModeBanner(value: string): string {
  return `${modeLabel(value)}：只用于检查产品功能与阅读体验，不代表正式数据覆盖。`;
}

export function statusLabel(value: string): string {
  if (value.endsWith("_blocked")) return "暂时无法使用";
  switch (value) {
    case "ok":
      return "本次可用";
    case "delayed":
    case "timeliness_unverified":
      return "更新稍有延迟";
    case "unavailable":
    case "not_qualified":
      return "本次暂不可用";
    default:
      return "状态暂未识别";
  }
}

export function statusTone(value: string): "good" | "warn" | "bad" | "neutral" {
  if (value === "ok") return "good";
  if (value === "delayed" || value === "timeliness_unverified") return "warn";
  if (value.endsWith("_blocked") || value === "unavailable" || value === "not_qualified") return "bad";
  return "neutral";
}

export function normalizeCapability(value: string): string {
  return value.split(".").join("_");
}

export function capabilityLabel(value: string): string {
  const normalizedValue = normalizeCapability(value);
  const match = BRIEFING_SECTIONS.find((section) => section["key"] === normalizedValue);
  return match ? match.label : "其他资料";
}

export function sourceRoleLabel(value: string): string {
  switch (value) {
    case "statutory":
      return "法定来源";
    case "professional_market":
      return "专业行情";
    case "professional_information":
      return "专业信息";
    case "discovery":
      return "发现线索";
    default:
      return "其他来源";
  }
}

export function attentionBandLabel(value: string | null): string {
  return plainLabel({ calm: "平静", notice: "留意", warming: "升温", tense: "紧张", high_alert: "高警觉" }, value ?? "");
}

export function pressureBandLabel(value: string | null): string {
  return plainLabel({ stable: "平稳", warming: "升温", attention: "关注", crowded: "拥挤", fragile: "脆弱" }, value ?? "");
}

export function availabilityLabel(value: string): string {
  return plainLabel({ ready: "资料完整", degraded: "部分资料可用", abstained: "资料不足，暂不评分", not_applicable: "本次不适用" }, value);
}

export function changeTypeLabel(value: string): string {
  return plainLabel({ new: "新增", update: "更新", correction: "更正", contradiction: "信息有矛盾" }, value);
}
