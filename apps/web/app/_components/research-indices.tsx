"use client";

import { useState } from "react";
import type { CSSProperties } from "react";

import type { PremarketBriefingViewV1_3 } from "@/lib/api/contracts";
import { normalizeResearchScore, plainInterpretationText } from "./presentation";

type Explanation = Readonly<{ code: string; text: string }>;
type Interpretation = PremarketBriefingViewV1_3["flash_interpretation"];

type ResearchIndexMeterProps = Readonly<{
  kind: "attention" | "pressure";
  title: string;
  description: string;
  score: number | null;
  band: string;
  completeness: number;
  disclaimer: string;
  drivers?: readonly Explanation[] | undefined;
  mitigators?: readonly Explanation[] | undefined;
}>;

export function ResearchIndexMeter({
  kind,
  title,
  description,
  score,
  band,
  completeness,
  disclaimer,
  drivers = [],
  mitigators = [],
}: ResearchIndexMeterProps) {
  const normalized = normalizeResearchScore(score);
  const meterStyle = { "--meter-value": `${normalized ?? 0}%` } as CSSProperties;
  const valueText = normalized === null ? "暂时没有可用分数" : `${normalized} 分，${band}`;
  return (
    <article className={`research-meter is-${kind}`}>
      <header>
        <div>
          <p className="meter-kicker">0—100</p>
          <h2>{title}</h2>
        </div>
        <strong className="meter-band">{normalized === null ? "暂不评分" : band}</strong>
      </header>
      <p className="meter-description">{description}</p>
      <div
        className={`meter-track${normalized === null ? " is-unavailable" : ""}`}
        role="meter"
        aria-label={title}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={normalized ?? undefined}
        aria-valuetext={valueText}
        style={meterStyle}
      >
        <div className="meter-fill" aria-hidden="true" />
        <output>{normalized === null ? "—" : normalized}<small>{normalized === null ? "资料不足" : "/ 100"}</small></output>
        <span className="meter-edge is-start">0</span>
        <span className="meter-edge is-end">100</span>
      </div>
      <div className="meter-coverage">
        <span>资料完整度</span>
        <strong>{Math.round(completeness)}%</strong>
      </div>
      <div className="meter-reasons">
        <section aria-label={`${title}主要原因`}>
          <h3>主要原因</h3>
          {drivers.slice(0, 3).length ? (
            <ul>{drivers.slice(0, 3).map((item) => <li key={item.code}>{item.text}</li>)}</ul>
          ) : <p>暂时没有需要单独强调的原因。</p>}
        </section>
        <section aria-label={`${title}缓和因素`}>
          <h3>缓和因素</h3>
          {mitigators.slice(0, 2).length ? (
            <ul>{mitigators.slice(0, 2).map((item) => <li key={item.code}>{item.text}</li>)}</ul>
          ) : <p>暂时没有明确的缓和因素。</p>}
        </section>
      </div>
      <p className="meter-disclaimer">{disclaimer}</p>
    </article>
  );
}

export function InterpretationSwitch({
  flash,
  pro,
}: Readonly<{ flash: Interpretation; pro: Interpretation | null | undefined }>) {
  const [selected, setSelected] = useState<"quick" | "deep">("quick");
  const active = selected === "deep" && pro ? pro : flash;
  const fallback = active.status === "fallback" || active.origin === "rules";
  return (
    <section className="interpretation-card" aria-labelledby="interpretation-title">
      <div className="interpretation-heading">
        <div>
          <p className="eyebrow">一句话说明</p>
          <h2 id="interpretation-title">{plainInterpretationText(active.title)}</h2>
        </div>
        {pro ? (
          <div className="interpretation-switch" role="group" aria-label="选择解读长度">
            <button type="button" aria-pressed={selected === "quick"} onClick={() => setSelected("quick")}>快速解读</button>
            <button type="button" aria-pressed={selected === "deep"} onClick={() => setSelected("deep")}>深入解读</button>
          </div>
        ) : null}
      </div>
      <p>{plainInterpretationText(active.summary)}</p>
      {fallback ? <small>本次使用固定规则整理，核心分数与原因不受影响。</small> : null}
    </section>
  );
}
