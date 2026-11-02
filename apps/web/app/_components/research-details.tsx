"use client";

import { isPremarketBriefingV1_3 } from "@/lib/api/contracts";

import {
  ATTENTION_CATEGORY_LABELS,
  availabilityLabel,
  BRIEFING_SECTIONS,
  changeTypeLabel,
  formatTime,
  HISTORICAL_METRIC_LABELS,
  plainFactText,
  plainLabel,
  PRESSURE_FACET_LABELS,
  STRUCTURE_DIMENSION_LABELS,
} from "./presentation";
import { PageFrame } from "./site-chrome";
import { SnapshotStatus, useDisplayedSnapshot } from "./snapshot-status";

function scoreText(value: number | null): string {
  return value === null ? "暂不评分" : `${value} 分`;
}

function analogStateText(value: string): string {
  return plainLabel({
    pending: "正在整理历史情景",
    ready: "历史情景可读",
    insufficient: "可比样本不足",
    maturity_pending: "部分情景仍在等待后续结果",
    incomparable: "当前情况暂不适合比较",
    failed: "历史情景本次未能生成",
  }, value);
}

export function ResearchDetails() {
  const { state, briefing, isHistorical } = useDisplayedSnapshot();
  const research = briefing && !("informationTime" in briefing) && isPremarketBriefingV1_3(briefing)
    ? briefing
    : null;
  const totalChanges = briefing
    ? "totalChanges" in briefing
      ? briefing.totalChanges
      : BRIEFING_SECTIONS.reduce(
          (total, section) => total + (briefing.sections[section["key"]].items?.length ?? 0),
          0,
        )
    : 0;
  return (
    <PageFrame
      active="details"
      connectionLabel={state.phase === "live" ? "最新结果可用" : isHistorical ? "历史演示" : "部分资料可读"}
      connectionTone={state.phase === "live" ? "online" : state.phase === "error" ? "offline" : "static"}
      disclaimer="仅供个人信息整理与研究，不构成投资建议、收益承诺或交易指令。"
    >
      <header className="why-hero">
        <p className="eyebrow">兆易创新 · 结果说明</p>
        <h1>为什么得到这个结果</h1>
        <p>先看五类信息和五个交易结构分面，再看盘面六维与历史情景。这里不展示程序日志、内部编号或原始报错。</p>
      </header>

      <SnapshotStatus />

      <nav className="page-index" aria-label="本页内容">
        <span>快速跳转</span>
        <a href="#scores">分数拆解</a>
        <a href="#structure">盘面六维</a>
        <a href="#history">历史情景</a>
        <a href="#changes">五类变化</a>
        <a href="#update">本次更新情况</a>
      </nav>

      {research ? (
        <div className="detail-stack plain-detail-stack">
          <section className="detail-section" id="scores" aria-labelledby="scores-title">
            <div className="section-intro">
              <div><p className="eyebrow">0—100 分拆解</p><h2 id="scores-title">哪些部分推高了分数</h2></div>
              <p>缺少资料时显示“暂不评分”，不会用 0 代替。</p>
            </div>
            <div className="score-breakdown">
              <article>
                <h3>信息风险温度 · 五类</h3>
                <ul>{research.attention_risk_index.categories.map((item) => (
                  <li key={item.category}><span>{plainLabel(ATTENTION_CATEGORY_LABELS, item.category)}</span><strong>{scoreText(item.score)}</strong></li>
                ))}</ul>
              </article>
              <article>
                <h3>交易结构压力 · 五分面</h3>
                <ul>{research.behavior_pressure_summary.facets.map((item) => (
                  <li key={item.facet}><span>{plainLabel(PRESSURE_FACET_LABELS, item.facet)}</span><strong>{scoreText(item.score)}</strong></li>
                ))}</ul>
              </article>
            </div>
          </section>

          <section className="detail-section" id="structure" aria-labelledby="structure-title">
            <div className="section-intro">
              <div><p className="eyebrow">不计入第二个总分的辅助观察</p><h2 id="structure-title">盘面结构六维</h2></div>
              <p>这些状态帮助理解价格与成交路径，不是另一套买卖评分。</p>
            </div>
            <div className="structure-grid">
              {research.behavior_pressure_summary.structure_dimensions.map((item) => (
                <article key={item.dimension}>
                  <span>{availabilityLabel(item.availability)}</span>
                  <h3>{plainLabel(STRUCTURE_DIMENSION_LABELS, item.dimension)}</h3>
                  <p>{item.explanation}</p>
                </article>
              ))}
            </div>
          </section>

          <section className="detail-section" id="history" aria-labelledby="history-title">
            <div className="section-intro">
              <div><p className="eyebrow">只描述过去，不预测未来</p><h2 id="history-title">历史相似情景</h2></div>
              <p>只展示随后 1 日、3 日及区间变化，不给上涨概率、命中率或目标价。</p>
            </div>
            {research.behavior_pressure_summary.historical_analogs ? (
              <div className="history-card">
                <div><strong>{analogStateText(research.behavior_pressure_summary.historical_analogs.state)}</strong><span>{research.behavior_pressure_summary.historical_analogs.sample_count} 个去重样本</span></div>
                {research.behavior_pressure_summary.historical_analogs.ranges?.length ? (
                  <table>
                    <caption>历史样本的中位数与中间一半范围</caption>
                    <thead><tr><th>观察项</th><th>中位数</th><th>中间一半范围</th></tr></thead>
                    <tbody>{research.behavior_pressure_summary.historical_analogs.ranges.map((item) => (
                      <tr key={item.metric}><th>{plainLabel(HISTORICAL_METRIC_LABELS, item.metric)}</th><td>{item.median}</td><td>{item.q1} 至 {item.q3}</td></tr>
                    ))}</tbody>
                  </table>
                ) : <p>当前没有足够的可比结果区间。</p>}
              </div>
            ) : <p className="zero-result">当前没有历史情景结果。</p>}
          </section>

          <section className="detail-section" id="changes" aria-labelledby="changes-title">
            <div className="section-intro"><div><p className="eyebrow">事实变化</p><h2 id="changes-title">五类变化明细</h2></div><p>本窗口共整理 {totalChanges} 条变化。</p></div>
            <div className="change-register">
              {BRIEFING_SECTIONS.map((section) => {
                const items = research.sections[section["key"]].items ?? [];
                return (
                  <article className="change-group" key={section["key"]}>
                    <header><h3>{section.label}</h3><strong>{items.length} 条</strong></header>
                    {items.length ? <div className="change-items">{items.map((item) => (
                      <section className="change-item" key={item.event_id}>
                        <div className="item-meta"><span className={`change-badge is-${item.change_type}`}>{changeTypeLabel(item.change_type)}</span><time dateTime={item.first_seen_at}>{formatTime(item.first_seen_at)}</time></div>
                        <h4>{item.headline}</h4><p>{plainFactText(item.fact_summary, section["key"] === "market_snapshot" ? "market.snapshot" : undefined)}</p>
                        <div className="item-footer"><span>{item.evidence_ids.length} 条相关证据</span><a href={`/evidence/${item.event_id}/`}>查看相关证据</a></div>
                      </section>
                    ))}</div> : <p className="zero-result">本窗口没有新增变化。</p>}
                  </article>
                );
              })}
            </div>
          </section>

          <section className="detail-section" id="update" aria-labelledby="update-title">
            <div className="section-intro"><div><p className="eyebrow">白话更新摘要</p><h2 id="update-title">本次更新情况</h2></div></div>
            <div className="update-summary">
              <p>{research.plain_language_update.summary}</p>
              <dl>
                <div><dt>检查项目</dt><dd>{research.plain_language_update.checked_count} 项</dd></div>
                <div><dt>成功更新</dt><dd>{research.plain_language_update.succeeded_count} 项</dd></div>
                <div><dt>没有变化</dt><dd>{research.plain_language_update.no_change_count} 项</dd></div>
                <div><dt>暂不可用</dt><dd>{research.plain_language_update.unavailable_count} 项</dd></div>
              </dl>
              {research.plain_language_update.reasons?.length ? <ul>{research.plain_language_update.reasons.map((item) => <li key={item.code}>{item.text}</li>)}</ul> : null}
            </div>
          </section>
        </div>
      ) : briefing ? (
        <section className="legacy-index-note legacy-detail-note">
          <h2>旧版简报仍可阅读</h2>
          <p>本窗口整理了 {totalChanges} 条五类变化，但没有双指数、五分面、六维或历史情景。页面不会自行换算。</p>
        </section>
      ) : (
        <section className="data-callout is-error"><div><h2>结果暂不可用</h2><p>页面没有足够资料，不会补写分数。</p></div></section>
      )}
    </PageFrame>
  );
}
