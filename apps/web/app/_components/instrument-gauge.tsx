import type { CSSProperties } from "react";

import { InfoTooltip } from "./info-tooltip";

const TICK_COLORS = [
  "#AEC4BC", "#A5BEB5", "#9BB8AE", "#90B1A6", "#84AA9D",
  "#78A394", "#6B9B8B", "#5E9181", "#508777", "#437D6D",
  "#377263", "#2D675A", "#245C51", "#1D5349", "#174A43",
] as const;

type InstrumentGaugeProps = {
  sectionKey: string;
  label: string;
  description: string;
  value: number | null;
  delay: number;
};

export type GaugeDynamics = {
  integerValue: number;
  scaleValue: number;
  needleAngle: number;
  jitterDuration: number;
  jitterCount: number;
  hasNeedle: boolean;
};

export function getGaugeDynamics(value: number | null): GaugeDynamics {
  const integerValue = value === null ? 0 : Math.max(0, Math.round(value));
  const scaleValue = Math.min(10, integerValue);
  return {
    integerValue,
    scaleValue,
    needleAngle: -120 + scaleValue * 24,
    jitterDuration: Math.max(900, 2800 - scaleValue * 190),
    jitterCount: scaleValue >= 8 ? 3 : scaleValue >= 5 ? 2 : 1,
    hasNeedle: value !== null,
  };
}

function point(angle: number, radius: number): { x: number; y: number } {
  const radians = (angle * Math.PI) / 180;
  return {
    x: 120 + Math.sin(radians) * radius,
    y: 120 - Math.cos(radians) * radius,
  };
}

export function InstrumentGauge({
  sectionKey,
  label,
  description,
  value,
  delay,
}: InstrumentGaugeProps) {
  const dynamics = getGaugeDynamics(value);
  const gaugeStyle = {
    "--needle-angle": `${dynamics.needleAngle}deg`,
    "--jitter-duration": `${dynamics.jitterDuration}ms`,
    "--jitter-count": String(dynamics.jitterCount),
    "--gauge-delay": `${delay}ms`,
  } as CSSProperties;

  return (
    <article
      className="gauge-card"
      data-change-gauge
      data-value={value === null ? "unavailable" : String(dynamics.integerValue)}
      style={gaugeStyle}
      aria-label={`${label}，当前${value === null ? "不可用" : `${dynamics.integerValue}条变化`}`}
    >
      <div className="gauge-visual" aria-hidden="true">
        <svg viewBox="0 0 240 224" focusable="false">
          <circle className="gauge-rim" cx="120" cy="120" r="105" />
          <circle className="gauge-face" cx="120" cy="120" r="94" />
          {Array.from({ length: 21 }, (_, index) => {
            const angle = -120 + index * 12;
            const start = point(angle, index % 5 === 0 ? 76 : 82);
            const end = point(angle, 91);
            const colorIndex = Math.min(
              TICK_COLORS.length - 1,
              Math.floor((index / 20) * TICK_COLORS.length),
            );
            return (
              <line
                className={index % 5 === 0 ? "gauge-tick is-major" : "gauge-tick"}
                key={angle}
                x1={start.x}
                y1={start.y}
                x2={end.x}
                y2={end.y}
                stroke={TICK_COLORS[colorIndex]}
              />
            );
          })}
          <text className="gauge-scale" x="45" y="185">0</text>
          <text className="gauge-scale" x="120" y="42" textAnchor="middle">5</text>
          <text className="gauge-scale" x="195" y="185" textAnchor="end">10+</text>
          {dynamics.hasNeedle ? (
            <g className="gauge-needle-sweep">
              <g className="gauge-needle-jitter">
                <line className="gauge-needle" x1="120" y1="130" x2="120" y2="46" />
                <circle className="gauge-needle-cap" cx="120" cy="120" r="6" />
                <circle className="gauge-needle-pin" cx="120" cy="120" r="2" />
              </g>
            </g>
          ) : null}
        </svg>
        <output className="gauge-reading" aria-label={`${label}当前返回条数`}>
          {value === null ? "—" : dynamics.integerValue}
          <small>条变化</small>
        </output>
      </div>
      <div className="gauge-caption">
        <div>
          <span className="gauge-index">{String(delay / 80 + 1).padStart(2, "0")}</span>
          <h2>{label}</h2>
        </div>
        <InfoTooltip label={label} description={description} />
      </div>
      <a className="text-link" href={`/details/#${sectionKey}`}>查看明细 <span aria-hidden="true">↗</span></a>
    </article>
  );
}
