"use client";

import { useId, useState } from "react";

type InfoTooltipProps = {
  label: string;
  description: string;
};

export function InfoTooltip({ label, description }: InfoTooltipProps) {
  const id = useId();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const visible = pinned || (!dismissed && (hovered || focused));

  return (
    <div
      className="info-popover"
      onMouseEnter={() => {
        setHovered(true);
        setDismissed(false);
      }}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        type="button"
        aria-label={`了解${label}变化条数`}
        aria-describedby={`${id}-tooltip`}
        aria-expanded={visible}
        onBlur={() => {
          setFocused(false);
          setPinned(false);
          setDismissed(false);
        }}
        onClick={() => setPinned((current) => {
          const next = !current;
          setDismissed(!next);
          return next;
        })}
        onFocus={() => {
          setFocused(true);
          setDismissed(false);
        }}
        onKeyDown={(event) => {
          if (event["key"] === "Escape") {
            setPinned(false);
            setDismissed(true);
          }
        }}
      >
        ?
      </button>
      <div
        className="tooltip-panel"
        id={`${id}-tooltip`}
        role="tooltip"
        aria-label={`${label}统计说明`}
        hidden={!visible}
      >
        <strong>{label}统计说明</strong>
        <p>{description}</p>
        <p>条数不代表重要程度、涨跌方向或风险等级；0 也不代表来源一定完整。</p>
      </div>
    </div>
  );
}
