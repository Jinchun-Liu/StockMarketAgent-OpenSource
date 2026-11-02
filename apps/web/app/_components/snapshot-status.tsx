"use client";

import type {
  EventEvidenceViewV1_2,
  PremarketBriefingViewV1_2,
  PremarketBriefingViewV1_3,
} from "@/lib/api/contracts";

import { BRIEFING_SECTIONS, formatTime, normalizeCapability } from "./presentation";
import {
  type PublicSnapshotState,
  type SnapshotPhase,
  usePublicSnapshot,
} from "./public-snapshot-provider";
import type { HistoricalExperienceBriefing } from "@/lib/data/historical-replay";

const PHASE_COPY: Record<SnapshotPhase, Readonly<{ label: string; detail: string }>> = {
  loading: { label: "正在检查最新结果", detail: "检查完成前，页面保留上一份可读内容。" },
  live: { label: "最新结果可用", detail: "页面已经完成必要检查，可以继续阅读。" },
  partial: { label: "部分数据可用", detail: "已证实内容继续展示；缺失来源不会被补零或猜测。" },
  stale: { label: "当前展示最近一次结果", detail: "新一轮更新尚未完成，请留意页面显示的更新时间。" },
  unavailable: { label: "当前数据不可用", detail: "本轮未形成可用当前结果；页面不会用 0 代替缺失。" },
  historical_fallback: { label: "历史演示", detail: "这份内容只用于展示产品功能，不代表最新市场情况。" },
  error: { label: "最新结果暂时无法读取", detail: "页面已保留上一份可读内容，不会补写或猜测数据。" },
};

export type DisplayedSnapshot = Readonly<{
  state: PublicSnapshotState;
  briefing:
    | PremarketBriefingViewV1_2
    | PremarketBriefingViewV1_3
    | HistoricalExperienceBriefing
    | null;
  evidenceByEventId: Readonly<Record<string, EventEvidenceViewV1_2>>;
  isHistorical: boolean;
  usesHistoricalExperienceFallback: boolean;
  snapshotKey: string;
}>;

const CURRENT_FAILURE_STATES = new Set(["gap", "blocked", "failed"]);

export function shouldUseHistoricalExperienceFallback(
  state: PublicSnapshotState,
): boolean {
  const current = state.current;
  if (!current || !state.fallback || current.manifest.origin_mode === "demo_replay") {
    return false;
  }
  return BRIEFING_SECTIONS.every((section) => {
    const matching = current.runStatus.capabilities.filter(
      (item) => normalizeCapability(item.capability) === section["key"],
    );
    return matching.length > 0 && matching.every((item) => CURRENT_FAILURE_STATES.has(item.state));
  });
}

export function useDisplayedSnapshot(): DisplayedSnapshot {
  const state = usePublicSnapshot();
  const usesHistoricalExperienceFallback = shouldUseHistoricalExperienceFallback(state);
  const briefing = usesHistoricalExperienceFallback
    ? state.fallback?.briefing ?? null
    : state.current?.briefing ?? state.fallback?.briefing ?? null;
  const evidenceByEventId = usesHistoricalExperienceFallback
    ? {}
    : state.current?.evidenceByEventId ?? {};
  return {
    state,
    briefing,
    evidenceByEventId,
    isHistorical: usesHistoricalExperienceFallback || !state.current || state.current.manifest.origin_mode === "demo_replay",
    usesHistoricalExperienceFallback,
    snapshotKey: usesHistoricalExperienceFallback
      ? `${state.current?.manifest.snapshot_id ?? "current"}:historical-experience`
      : state.current?.manifest.snapshot_id ?? "historical-replay",
  };
}

export function SnapshotStatus({ compact = false }: Readonly<{ compact?: boolean }>) {
  const {
    state,
    briefing,
    isHistorical,
    usesHistoricalExperienceFallback,
  } = useDisplayedSnapshot();
  const copy = usesHistoricalExperienceFallback
    ? {
        label: "本次更新暂不可用",
        detail: "页面已切换到明确标时的历史演示，避免把缺失误写成 0。",
      }
    : PHASE_COPY[state.phase];
  const manifest = state.current?.manifest;
  const lastSuccess = manifest?.last_successful_run_at ?? state.fallback?.lastSuccessfulRunAt ?? null;
  const informationTime = briefing && "informationTime" in briefing
    ? briefing.informationTime
    : briefing?.as_of ?? null;
  return (
    <section
      className={`snapshot-status is-${state.phase}${compact ? " is-compact" : ""}`}
      aria-label="内容更新时间"
      aria-live="polite"
    >
      <div className="snapshot-status-copy">
        <span className="snapshot-status-dot" aria-hidden="true" />
        <div>
          <strong>{copy.label}</strong>
          <p>{copy.detail}</p>
        </div>
      </div>
      {!compact ? (
        <dl className="snapshot-metadata">
          <div><dt>信息更新到</dt><dd>{manifest ? <time dateTime={manifest.data_as_of}>{formatTime(manifest.data_as_of)}</time> : informationTime ? <time dateTime={informationTime}>{formatTime(informationTime)}</time> : "暂无"}</dd></div>
          <div><dt>页面更新时间</dt><dd>{manifest ? <time dateTime={manifest.published_at}>{formatTime(manifest.published_at)}</time> : state.fallback ? <time dateTime={state.fallback.publishedAt}>{formatTime(state.fallback.publishedAt)}</time> : "暂无"}</dd></div>
          <div><dt>最近成功更新</dt><dd>{lastSuccess ? <time dateTime={lastSuccess}>{formatTime(lastSuccess)}</time> : "暂无成功记录"}</dd></div>
        </dl>
      ) : null}
      {manifest?.origin_mode === "dev_live" ? (
        <p className="snapshot-disclaimer">
          当前属于开发阶段资料，只用于检查功能与研究展示，不代表正式数据覆盖。
        </p>
      ) : null}
      {isHistorical ? (
        <p className="snapshot-disclaimer is-history">
          历史演示：内容来自{informationTime ? ` ${formatTime(informationTime)} ` : "过去时点"}，不代表最新市场情况。
        </p>
      ) : null}
      {usesHistoricalExperienceFallback ? (
        <p className="snapshot-disclaimer is-experience">
          本页整体使用同一份历史演示，不会把当前资料与历史资料拼在一起。
        </p>
      ) : null}
      {state.phase === "error" ? (
        <div className="snapshot-retry">
          <span>未通过页面检查的内容不会显示。</span>
          <button type="button" onClick={state.refresh}>重新检查最新结果</button>
        </div>
      ) : null}
    </section>
  );
}
