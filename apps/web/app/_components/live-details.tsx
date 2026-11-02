"use client";

import {
  BRIEFING_SECTIONS,
  capabilityLabel,
  formatTime,
  normalizeCapability,
  safeGapReason,
  sourceRoleLabel,
  statusLabel,
  statusTone,
} from "./presentation";
import { PageFrame } from "./site-chrome";
import { SnapshotStatus, useDisplayedSnapshot } from "./snapshot-status";

function timeValue(value: string | null | undefined, empty = "未推进") {
  return value ? <time dateTime={value} title={value}>{formatTime(value)}</time> : empty;
}

function runStateLabel(value: string): string {
  switch (value) {
    case "succeeded": return "成功";
    case "no_change": return "无新增";
    case "gap": return "来源缺口";
    case "blocked": return "权利阻断";
    case "failed": return "运行失败";
    case "stale": return "沿用旧数据";
    default: return value;
  }
}

export function LiveDetails() {
  const {
    state,
    briefing: displayedBriefing,
    evidenceByEventId,
    isHistorical,
    usesHistoricalExperienceFallback,
  } = useDisplayedSnapshot();
  const briefing = displayedBriefing && "informationTime" in displayedBriefing
    ? null
    : displayedBriefing;
  const runStatus = state.current?.runStatus;
  const sourceCoverage = state.current?.briefing.source_coverage ?? briefing?.source_coverage ?? [];
  const connectionLabel = state.phase === "live"
    ? "DEV_LIVE · 已校验"
    : state.phase === "loading"
      ? "正在核验最新快照"
      : usesHistoricalExperienceFallback
        ? "DEV_ONLY · 当前失败 / 历史体验"
      : isHistorical
        ? "历史演示"
        : "降级快照 · 可核验";
  return (
    <PageFrame
      active="details"
      connectionLabel={connectionLabel}
      connectionTone={state.phase === "live" ? "online" : state.phase === "error" ? "offline" : "static"}
      disclaimer={briefing?.disclaimer}
    >
      <header className="detail-hero">
        <div>
          <p className="eyebrow">DETAILS · EVIDENCE · METHOD</p>
          <h1>数据、审计与计算依据</h1>
          <p className="hero-lede">
            先确认快照与来源状态，再阅读五类变化、脱敏日志和计算方法。缺失与真实 0 严格分开。
          </p>
        </div>
        <dl className="detail-summary">
          <div><dt>标的</dt><dd>{briefing?.instrument.symbol ?? "603986.SH"}</dd></div>
          <div><dt>来源模式</dt><dd>{state.current?.manifest.origin_mode.toUpperCase() ?? "HISTORICAL"}</dd></div>
          <div><dt>规则版本</dt><dd>{briefing?.ruleset_version ?? "—"}</dd></div>
          <div><dt>运行状态</dt><dd>{runStatus?.status ?? "historical_fallback"}</dd></div>
        </dl>
      </header>

      <SnapshotStatus />

      <nav className="page-index" aria-label="本页目录">
        <span>本页目录</span>
        <a href="#sources">来源状态</a>
        <a href="#changes">五类变化</a>
        <a href="#audit">运行日志</a>
        <a href="#method">计算依据</a>
      </nav>

      {briefing ? (
        <div className="detail-stack">
          <section className="detail-section" id="sources" aria-labelledby="sources-heading">
            <div className="section-intro">
              <div><p className="eyebrow">01 · SOURCE HEALTH</p><h2 id="sources-heading">来源状态</h2></div>
              <p>来源等级、当前运行和最后成功分开呈现；DEV_ONLY 不等于正式数据授权。</p>
            </div>
            <div className="source-status-grid">
              {runStatus ? runStatus.capabilities.map((capability) => {
                const coverage = sourceCoverage.find((item) =>
                  item.source_id === capability.source_id &&
                  normalizeCapability(item.capability) === normalizeCapability(capability.capability));
                return (
                  <article className="source-status-card" key={`${capability.source_id}:${capability.capability}`}>
                    <div className="source-card-head">
                      <div><span>{capabilityLabel(capability.capability)}</span><h3>{capability.source_id}</h3></div>
                      <strong className={`source-state is-${capability.state === "succeeded" || capability.state === "no_change" ? "good" : capability.state === "stale" ? "warn" : "bad"}`}>
                        {runStateLabel(capability.state)}
                      </strong>
                    </div>
                    <dl className="compact-data-list">
                      <div><dt>来源等级</dt><dd>{capability.source_grade}</dd></div>
                      <div><dt>最后成功</dt><dd>{timeValue(coverage?.last_success_at, "暂无成功记录")}</dd></div>
                      <div><dt>业务水位</dt><dd>{timeValue(coverage?.progress.business_watermark)}</dd></div>
                      <div><dt>安全原因码</dt><dd>{capability.safe_reason_code ?? "—"}</dd></div>
                      <div><dt>运行完成</dt><dd>{timeValue(runStatus.completed_at)}</dd></div>
                      <div><dt>运行 ID</dt><dd><code>{runStatus.run_id}</code></dd></div>
                    </dl>
                  </article>
                );
              }) : sourceCoverage.map((coverage) => (
                <article className="source-status-card" key={`${coverage.source_id}:${coverage.capability}`}>
                  <div className="source-card-head">
                    <div><span>{capabilityLabel(coverage.capability)}</span><h3>{coverage.source_id}</h3></div>
                    <strong className={`source-state is-${statusTone(coverage.status)}`}>{statusLabel(coverage.status)}</strong>
                  </div>
                  <dl className="compact-data-list">
                    <div><dt>来源角色</dt><dd>{sourceRoleLabel(coverage.source_role)}</dd></div>
                    <div><dt>最后成功</dt><dd>{timeValue(coverage.last_success_at, "暂无成功记录")}</dd></div>
                    <div><dt>业务水位</dt><dd>{timeValue(coverage.progress.business_watermark)}</dd></div>
                    <div><dt>安全原因码</dt><dd>{safeGapReason(coverage.gap_reason)}</dd></div>
                  </dl>
                </article>
              ))}
              {!runStatus && sourceCoverage.length === 0 ? (
                <p className="zero-result">当前未返回来源状态，不能据此认定数据完整。</p>
              ) : null}
            </div>
          </section>

          <section className="detail-section" id="changes" aria-labelledby="changes-heading">
            <div className="section-intro">
              <div><p className="eyebrow">02 · CHANGE REGISTER</p><h2 id="changes-heading">五类变化明细</h2></div>
              <p>比较窗口：{timeValue(briefing.comparison_since)} — {timeValue(briefing.as_of)}</p>
            </div>
            {usesHistoricalExperienceFallback ? (
              <aside className="experience-fallback-banner" aria-label="五类历史体验说明">
                <span>DEV_ONLY</span>
                <div>
                  <strong>本区为同一份历史演示数据，当前来源失败状态请以上方来源卡片为准</strong>
                  <p>没有把当前与历史按类别拼接；条目、整数和证据链接均来自 {formatTime(briefing.as_of)} 的完整回放。</p>
                </div>
              </aside>
            ) : null}
            <div className="change-register">
              {BRIEFING_SECTIONS.map((section, sectionIndex) => {
                const items = briefing.sections[section.key].items ?? [];
                const matching = runStatus?.capabilities.filter(
                  (item) => normalizeCapability(item.capability) === section.key,
                ) ?? [];
                const missing = !usesHistoricalExperienceFallback && (state.phase === "unavailable" || (
                  items.length === 0 && matching.length > 0 && matching.every(
                    (item) => item.state === "gap" || item.state === "blocked" || item.state === "failed",
                  )
                ));
                return (
                  <article className="change-group" id={section.key} key={section.key}>
                    <header>
                      <div><span>{String(sectionIndex + 1).padStart(2, "0")}</span><h3>{section.label}</h3></div>
                      <strong>{missing ? "—" : `${items.length} 条`}</strong>
                    </header>
                    {missing ? (
                      <p className="zero-result">暂无可用数据；系统没有用 0 代替来源缺失。</p>
                    ) : items.length === 0 ? (
                      <p className="zero-result">
                        {usesHistoricalExperienceFallback ? "历史演示在该窗口返回 0 条变化。" : "当前已校验返回 0 条变化。"}
                      </p>
                    ) : (
                      <div className="change-items">
                        {items.map((item) => {
                          const evidence = evidenceByEventId[item.event_id];
                          const destination = isHistorical
                            ? `/evidence/${item.event_id}/`
                            : `/details/#event-${item.event_id}`;
                          return (
                            <section className="change-item" id={`event-${item.event_id}`} key={`${item.event_id}:${item.event_revision}`} tabIndex={-1}>
                              <div className="item-meta">
                                <span className={`change-badge is-${item.change_type}`}>{item.change_type.toUpperCase()}</span>
                                <span>revision {item.event_revision}</span>
                                <time dateTime={item.first_seen_at}>{formatTime(item.first_seen_at)}</time>
                              </div>
                              <h4>{item.headline}</h4>
                              <p>{item.fact_summary}</p>
                              {item.inference ? <p className="inference-note"><strong>系统推断</strong>{item.inference}</p> : null}
                              <div className="item-footer">
                                <span>{item.evidence_ids.length} 条证据 · {item.source_roles.map(sourceRoleLabel).join(" / ")}</span>
                                <a href={destination}>{isHistorical ? "查看历史回放证据" : "查看同页证据摘要"} <span aria-hidden="true">↗</span></a>
                              </div>
                              {!isHistorical && evidence ? (
                                <details className="inline-evidence">
                                  <summary>证据摘要与修订</summary>
                                  <p>{evidence.title}</p>
                                  <dl className="compact-data-list">
                                    <div><dt>event ID</dt><dd><code>{evidence.event_id}</code></dd></div>
                                    <div><dt>revision</dt><dd>{evidence.revision}</dd></div>
                                    <div><dt>证据记录</dt><dd>{evidence.evidence.length} 条</dd></div>
                                    <div><dt>可见截止</dt><dd>{timeValue(evidence.as_of)}</dd></div>
                                  </dl>
                                </details>
                              ) : null}
                            </section>
                          );
                        })}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </section>

          <section className="detail-section" id="audit" aria-labelledby="audit-heading">
            <div className="section-intro">
              <div><p className="eyebrow">03 · SANITIZED RUN LOG</p><h2 id="audit-heading">脱敏运行日志</h2></div>
              <p>只展示稳定时间、状态、来源等级、原因码和公开运行 ID；不含 cursor、私有地址或堆栈。</p>
            </div>
            <div className="audit-ledger">
              <div className="audit-ledger-head"><span>时间</span><span>能力</span><span>结果与公开标识</span></div>
              {runStatus?.capabilities.map((item) => (
                <article key={`run:${item.source_id}:${item.capability}`}>
                  <time dateTime={runStatus.completed_at}>{formatTime(runStatus.completed_at)}</time>
                  <div><strong>{item.source_id}</strong><span>{item.capability} · {item.source_grade}</span></div>
                  <div><span className={`audit-dot is-${item.state === "succeeded" || item.state === "no_change" ? "good" : item.state === "stale" ? "warn" : "bad"}`} aria-hidden="true" /><code>{item.safe_reason_code ?? runStateLabel(item.state)}</code></div>
                </article>
              )) ?? sourceCoverage.map((coverage) => (
                <article key={`audit:${coverage.source_id}:${coverage.progress.batch_audit_id}`}>
                  <time dateTime={coverage.observed_at}>{formatTime(coverage.observed_at)}</time>
                  <div><strong>{coverage.source_id}</strong><span>{coverage.capability} · {coverage.progress.advance_state}</span></div>
                  <div><span className={`audit-dot is-${statusTone(coverage.status)}`} aria-hidden="true" /><code>{coverage.progress.batch_audit_id}</code></div>
                </article>
              ))}
            </div>
          </section>

          <section className="detail-section method-section" id="method" aria-labelledby="method-heading">
            <div className="section-intro">
              <div><p className="eyebrow">04 · METHOD</p><h2 id="method-heading">数字是怎样得到的</h2></div>
              <p>浏览器只呈现一份已校验 briefing 的五类 items.length；当前五类全失败时可整体切换历史回放，不按类别拼接。</p>
            </div>
            <div className="formula-card">
              <span>显示整数</span><code>briefing.sections[固定类别].items.length</code>
              <p>成功空数组才显示 0；来源失败显示“—”；DEV_ONLY 全失败体验仅整体使用一份明确标时的历史回放。</p>
            </div>
            <div className="method-grid">
              <article><span>POINTER</span><h3>原子切换</h3><p>先校验 current 指向的 manifest，再校验每个不可变资产。</p></article>
              <article><span>SHA-256</span><h3>逐字节校验</h3><p>manifest 与全部资产使用浏览器 crypto.subtle 核对公开哈希。</p></article>
              <article><span>PIT</span><h3>时点安全</h3><p>事实与证据必须和 data_as_of 一致，避免把未来修订带回过去。</p></article>
              <article><span>DEGRADED</span><h3>显式降级</h3><p>部分、标旧与不可用互不替代，也不会伪造成完整成功。</p></article>
            </div>
          </section>
        </div>
      ) : (
        <section className="data-callout is-error"><span aria-hidden="true">×</span><div><h2>详细数据暂不可用</h2><p>没有通过契约校验的当前或历史简报，系统不会补写结果。</p></div></section>
      )}
    </PageFrame>
  );
}
