import type { Claim, Session, SessionStage } from "@/lib/api/research-v2";

export const stageOrder: readonly SessionStage[] = [
  "queued",
  "resolving_instrument",
  "collecting_history",
  "auditing_data",
  "refetching_data",
  "scoring",
  "generating_report",
  "analyzing_business",
  "analyzing_financials",
  "analyzing_valuation",
  "analyzing_events",
  "debating_cases",
  "validating_report",
  "completed",
];

export const stageLabels: Record<SessionStage, string> = {
  queued: "进入研究队列",
  resolving_instrument: "确认证券身份",
  collecting_history: "回溯历史证据",
  auditing_data: "正在审核资料",
  refetching_data: "正在定向补抓",
  scoring: "正在评分",
  generating_report: "正在生成报告",
  analyzing_business: "拆解业务模式",
  analyzing_financials: "比较财务质量",
  analyzing_valuation: "复算估值口径",
  analyzing_events: "匹配国际事件与暴露",
  debating_cases: "分析看多、看空观点并形成综合判断",
  validating_report: "核对论点与证据",
  completed: "研究流程结束",
};

export const statusLabels: Record<Session["status"], string> = {
  queued: "已排队",
  running: "研究中",
  succeeded: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
  stalled: "等待恢复",
  expired: "已过期",
  deleted: "已删除",
};

export const claimLabels: Record<Claim["claim_type"], string> = {
  fact: "事实",
  system_inference: "系统推断",
  model_hypothesis: "模型假设",
};

export const listingLabels: Record<string, string> = {
  pre_listing: "待上市",
  active: "上市",
  suspended: "停牌",
  delisted: "退市",
  unknown: "状态未知",
};

export function formatTime(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(timestamp);
}

export function formatDate(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "日期未知";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeZone: "Asia/Shanghai",
  }).format(timestamp);
}

export const resultLabels: Record<NonNullable<Session["result_completeness"]>, string> = {
  complete: "完整报告已就绪",
  partial: "部分完成 · 尚未形成完整报告",
  insufficient_evidence: "证据不足 · 尚未形成完整报告",
  execution_failed: "执行失败 · 尚未形成完整报告",
};

export function sessionResultLabel(session: Session): string {
  if (session.status === "succeeded" && session.result_completeness === "partial") {
    return "分析受限 · 流程已结束，未形成完整报告";
  }
  return session.result_completeness ? resultLabels[session.result_completeness] : statusLabels[session.status];
}
