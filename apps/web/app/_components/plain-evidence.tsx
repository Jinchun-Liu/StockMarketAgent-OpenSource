import type { EventEvidenceView } from "@/lib/api/contracts";
import { isEventEvidenceV1_1, isEventEvidenceV1_2 } from "@/lib/api/contracts";
import { safeExternalSourceUrl } from "@/lib/source-links";

import { changeTypeLabel, formatTime, plainFactText, sourceRoleLabel } from "./presentation";
import { PageFrame } from "./site-chrome";

export function PlainEvidencePage({ event }: Readonly<{ event: EventEvidenceView | null }>) {
  return (
    <PageFrame active="details" connectionLabel={event ? "证据可读" : "证据暂不可用"} connectionTone={event ? "static" : "offline"}>
      <a className="back-link" href="/details/#changes">← 返回五类变化</a>
      {event ? (
        <article className="evidence-page plain-evidence-page">
          <header className="evidence-hero">
            <div className="item-meta"><span className={`change-badge is-${event.change_type}`}>{changeTypeLabel(event.change_type)}</span></div>
            <p className="eyebrow">这条信息为什么可以展示</p>
            <h1>{event.title}</h1>
            <p className="hero-lede">截至 <time dateTime={event.as_of}>{formatTime(event.as_of)}</time>，以下内容在当时已经可见。</p>
          </header>

          {event.correction ? (
            <section className="correction-card" aria-label="更正说明">
              <h2>{event.correction.kind === "replacement" ? "这条信息后来有更正" : "这条信息后来被撤回"}</h2>
              <p>页面保留新旧记录，方便核对变化，不会静默覆盖。</p>
            </section>
          ) : null}

          <div className="evidence-grid">
            <section>
              <div className="evidence-section-head"><span>01</span><h2>整理出的事实</h2></div>
              <div className="fact-table">
                {(event.facts ?? []).map((fact, index) => (
                  <div className="fact-row" key={`${index}:${fact.value.value}`}>
                    <span>{event.event_type === "market.snapshot" ? "行情摘要" : `事实内容 ${index + 1}`}</span>
                    <strong>{plainFactText(fact.value.value, event.event_type)}{fact.unit ? ` ${fact.unit}` : ""}</strong>
                  </div>
                ))}
                {!event.facts?.length ? <p>这条记录没有额外的结构化字段。</p> : null}
              </div>
            </section>

            <section>
              <div className="evidence-section-head"><span>02</span><h2>相关证据</h2></div>
              <div className="evidence-cards">
                {event.evidence.map((evidence, index) => {
                  const provenance = "provenance" in evidence ? evidence.provenance : null;
                  const externalUrl = safeExternalSourceUrl(evidence.source_url, provenance);
                  return (
                    <article className="evidence-card" key={`evidence-${index}`}>
                      <div className="item-meta"><span>证据 {index + 1}</span><span>{sourceRoleLabel(evidence.source_role)}</span></div>
                      <h3>{evidence.title}</h3>
                      <dl className="compact-data-list">
                        {"event_time" in evidence ? <div><dt>信息发生时间</dt><dd><time dateTime={evidence.event_time}>{formatTime(evidence.event_time)}</time></dd></div> : null}
                        <div><dt>首次看到</dt><dd><time dateTime={evidence.first_seen_at}>{formatTime(evidence.first_seen_at)}</time></dd></div>
                        <div><dt>来源发布时间</dt><dd><time dateTime={evidence.published_at}>{formatTime(evidence.published_at)}</time></dd></div>
                        <div><dt>来源页面</dt><dd>{externalUrl ? <a href={externalUrl} rel="noreferrer" target="_blank">打开公开来源</a> : "当前不提供外链"}</dd></div>
                      </dl>
                    </article>
                  );
                })}
              </div>
            </section>

            <section>
              <div className="evidence-section-head"><span>03</span><h2>更新记录</h2></div>
              <p>这条信息共保留 {event.revision_timeline.items?.length ?? 0} 次可见更新。历史内容不会被后来修订覆盖。</p>
              {(isEventEvidenceV1_1(event) || isEventEvidenceV1_2(event)) &&
                (event.runtime_mode === "demo_replay" || event.runtime_mode === "public_static") ? (
                  <aside className="development-banner">这是历史演示，只用于检查证据阅读流程。</aside>
                ) : null}
            </section>
          </div>
        </article>
      ) : (
        <section className="data-callout is-error"><div><h1>证据暂不可用</h1><p>当前没有足够信息展示这条证据，页面不会显示内部报错。</p></div></section>
      )}
    </PageFrame>
  );
}
