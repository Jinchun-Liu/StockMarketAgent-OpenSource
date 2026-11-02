"use client";

import { isPremarketBriefingV1_3 } from "@/lib/api/contracts";

import { attentionBandLabel, formatTime, pressureBandLabel } from "./presentation";
import { InterpretationSwitch, ResearchIndexMeter } from "./research-indices";
import { PageFrame } from "./site-chrome";
import { SnapshotStatus, useDisplayedSnapshot } from "./snapshot-status";

export function ResearchOverview() {
  const { state, briefing, usesHistoricalExperienceFallback } = useDisplayedSnapshot();
  const research = briefing && !("informationTime" in briefing) && isPremarketBriefingV1_3(briefing)
    ? briefing
    : null;
  const informationTime = briefing && "informationTime" in briefing
    ? briefing.informationTime
    : briefing?.as_of ?? null;
  const connectionLabel = usesHistoricalExperienceFallback || state.phase === "historical_fallback"
    ? "历史演示"
    : state.phase === "live"
      ? "最新结果可用"
      : state.phase === "loading"
        ? "正在检查更新"
        : "部分资料可读";
  return (
    <PageFrame
      active="overview"
      connectionLabel={connectionLabel}
      connectionTone={state.phase === "live" ? "online" : state.phase === "error" ? "offline" : "static"}
      disclaimer="仅供个人信息整理与研究，不构成投资建议、收益承诺或交易指令。"
    >
      <section className="research-hero">
        <p className="eyebrow">兆易创新 <span>603986.SH</span></p>
        <h1>先看哪里需要核对，再看交易结构是否承压</h1>
        <p>两个 0—100 研究指数只回答这两个问题。分高表示更需要留意，不代表上涨、下跌或发生概率。</p>
        <div className="research-hero-meta">
          <span>仅研究兆易创新</span>
          <span>{informationTime ? <>信息更新到 <time dateTime={informationTime}>{formatTime(informationTime)}</time></> : "等待可用资料"}</span>
          <a href="/details/">为什么得到这个结果 <span aria-hidden="true">→</span></a>
        </div>
      </section>

      <SnapshotStatus />

      <section className="dual-index" aria-label="兆易创新双研究指数">
        <ResearchIndexMeter
          kind="attention"
          title="信息风险温度"
          description="综合公告、财务、行情、公司与行业、宏观五类信息的变化、更正与矛盾。"
          score={research?.attention_risk_index.score ?? null}
          band={research ? attentionBandLabel(research.attention_risk_index.band) : "资料不足"}
          completeness={research?.attention_risk_index.completeness_percent ?? 0}
          disclaimer={research?.attention_risk_index.disclaimer ?? "旧版资料不含此指数，页面不会自行补算。"}
          drivers={research?.attention_risk_index.drivers}
          mitigators={research?.attention_risk_index.mitigators}
        />
        <ResearchIndexMeter
          kind="pressure"
          title="交易结构压力（研究）"
          description="根据兆易创新自身历史行情，观察活跃偏离、位置拥挤、流动性、波动及行业偏离。"
          score={research?.behavior_pressure_summary.score ?? null}
          band={research ? pressureBandLabel(research.behavior_pressure_summary.band) : "资料不足"}
          completeness={research?.behavior_pressure_summary.completeness_percent ?? 0}
          disclaimer={research?.behavior_pressure_summary.disclaimer ?? "旧版资料不含此研究指数，页面不会自行补算。"}
          drivers={research?.behavior_pressure_summary.drivers}
          mitigators={research?.behavior_pressure_summary.mitigators}
        />
      </section>

      {research ? <InterpretationSwitch flash={research.flash_interpretation} pro={research.pro_interpretation} /> : (
        <section className="legacy-index-note" aria-label="旧版资料说明">
          <h2>这份资料仍可阅读，但暂时没有双指数</h2>
          <p>当前是旧版简报。五类变化仍保留在“为什么得到这个结果”中，页面不会把变化条数换算成分数。</p>
        </section>
      )}
    </PageFrame>
  );
}
