"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiProblem,
  getReport,
  getSession,
  mutateSession,
  type Report,
  type Session,
} from "@/lib/api/research-v2";
import { usePreviewIdentity } from "@/lib/preview/identity";

import { formatTime, stageLabels, stageOrder, sessionResultLabel } from "./presentation";
import { AvailableResultView, ReportView } from "./report-view";

function researchReadErrorMessage(caught: unknown): string {
  if (caught instanceof ApiProblem && caught.status === 412) {
    return "这份研究成品未通过完整性校验，暂时无法打开；原始记录未被改写。";
  }
  if (caught instanceof TypeError && /^(Failed to fetch|Load failed|NetworkError when attempting to fetch resource\.)$/.test(caught.message)) {
    return "网络连接暂时中断，请稍后查看研究进度。";
  }
  return caught instanceof Error ? caught.message : "无法读取研究会话";
}

function sessionIdFromLocation(): string | null {
  const value = new URLSearchParams(window.location.search).get("session");
  return value && /^[0-9a-f-]{36}$/i.test(value) ? value : null;
}

export function ResearchClient() {
  const { state, config, accessToken, signIn, error: identityError } = usePreviewIdentity();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const readSequence = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => setSessionId(sessionIdFromLocation()), 0);
    return () => window.clearTimeout(timer);
  }, []);

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!config || !accessToken || !sessionId) return null;
    const sequence = ++readSequence.current;
    const next = await getSession(config, accessToken, sessionId, signal);
    if (signal?.aborted || sequence !== readSequence.current) return null;
    if (next.status === "succeeded" && (!next.result_completeness || next.result_completeness === "complete")) {
      const completed = await getReport(config, accessToken, sessionId, signal);
      if (!signal?.aborted && sequence === readSequence.current) setReport(completed);
    }
    if (!signal?.aborted && sequence === readSequence.current) {
      setSession(next);
      setReadError(null);
    }
    return next;
  }, [accessToken, config, sessionId]);

  useEffect(() => {
    if (state !== "authenticated" || !sessionId) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void load(controller.signal).catch((caught: unknown) => {
        if (!controller.signal.aborted && !(caught instanceof DOMException && caught.name === "AbortError")) {
          setReadError(researchReadErrorMessage(caught));
        }
      });
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [load, sessionId, state]);

  useEffect(() => {
    if (!sessionId || !config || !accessToken || !session || !["queued", "running"].includes(session.status)) return;
    const controller = new AbortController();
    let active = true;
    let timer = 0;
    let delay = 2_000;
    const poll = async () => {
      try {
        const next = await load(controller.signal);
        if (!active || !next || !["queued", "running"].includes(next.status)) return;
        delay = Math.min(8_000, Math.round(delay * 1.45));
        timer = window.setTimeout(() => void poll(), document.hidden ? 12_000 : delay);
      } catch (caught) {
        if (!active) return;
        setReadError(researchReadErrorMessage(caught));
        timer = window.setTimeout(() => void poll(), 8_000);
      }
    };
    timer = window.setTimeout(() => void poll(), delay);
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [accessToken, config, load, session, sessionId]);

  const currentStageIndex = useMemo(
    () => session ? stageOrder.indexOf(session.progress.stage) : 0,
    [session],
  );

  async function mutate(operation: "cancel" | "retry") {
    if (!config || !accessToken || !sessionId) return;
    setBusy(true);
    setError(null);
    try {
      await mutateSession(config, accessToken, sessionId, operation);
      readSequence.current += 1;
      setReport(null);
      await load().catch((caught: unknown) => setReadError(researchReadErrorMessage(caught)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  if (!sessionId) {
    return (
      <div className="content-page page-enter">
        <div className="empty-state">
          <h1>没有指定研究会话</h1>
          <p>从首页搜索并确认一只 A 股后，系统会在这里恢复进度或展示完整报告。</p>
          <Link className="button-primary" href="/">返回搜索</Link>
        </div>
      </div>
    );
  }

  if (state === "anonymous") {
    return (
      <div className="content-page page-enter">
        <section className="login-panel">
          <h1>登录后恢复这份研究</h1>
          <p>会话按匿名化主体隔离；未授权用户无法枚举或读取其他人的研究。</p>
          <button className="button-primary" type="button" onClick={() => void signIn(window.location.pathname + window.location.search)}>用户名和密码登录</button>
        </section>
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className="content-page page-enter">
        <div className="error-banner" role="alert">
          认证环境不可用：{identityError ?? "未知配置错误"}
        </div>
      </div>
    );
  }

  if (report) return <div className="report-page"><ReportView report={report} /></div>;

  return (
    <div className={`content-page research-page page-enter${session?.available_result?.reader_edition ? " has-result-document" : ""}`}>
      <section className="research-status" aria-live="polite">
        <header className="research-status-head">
          <div>
            <p className="eyebrow">RESEARCH SESSION</p>
            {session?.available_result?.reader_edition
              ? <h2>{session.instrument_name}</h2>
              : <h1>{session?.instrument_name ?? "正在恢复研究"}</h1>}
            <p>{session?.instrument_id ?? sessionId}</p>
          </div>
          {session && <span className="status-chip">{sessionResultLabel(session)}</span>}
        </header>

        {(error ?? readError) && <div className="error-banner" role="alert">{error ?? readError}</div>}

        {!session ? (
          <p className="info-banner">正在恢复已保存的研究进度。</p>
        ) : (
          <>
            <details className={session.available_result?.reader_edition ? "document-progress" : "session-progress"} open={session.available_result?.reader_edition ? undefined : true}>
            <summary>查看研究执行进度</summary>
            <ol className="stage-sequence" aria-label="研究阶段">
              {stageOrder.map((stage, index) => {
                const completed = session.status === "succeeded" || index < currentStageIndex;
                const current = index === currentStageIndex && session.status !== "succeeded";
                return (
                  <li className={`stage-item${completed ? " is-complete" : ""}${current ? " is-current" : ""}`} key={stage} aria-current={current ? "step" : undefined}>
                    <span className="stage-marker" aria-hidden="true">{completed ? "✓" : String(index + 1)}</span>
                    <span>
                      <strong>{stageLabels[stage]}</strong>
                      {current && <small>{session.progress.message}</small>}
                    </span>
                    {current && <small>当前</small>}
                  </li>
                );
              })}
            </ol>
            </details>

            <p className="heartbeat">最近进展：{formatTime(session.progress.latest_progress_at)}</p>

            {(session.coverage_gaps ?? []).length > 0 && (
              <div className="info-banner">当前有 {session.coverage_gaps?.length} 项证据缺口。</div>
            )}

            {session.source_mode === "synthetic_development_only" && <p className="report-notice">合成测试数据 · 仅验证研究流程</p>}
            {["failed", "stalled", "cancelled"].includes(session.status) && <div className="info-banner">
              <strong>{sessionResultLabel(session)}</strong>
              <p>{session.progress.message}</p>
              {session.failure_reason_code && <small>原因编号：{session.failure_reason_code}</small>}
              {!session.retryable && <p>本次没有可用的恢复操作。可查看已保存内容，或返回搜索创建新研究。</p>}
            </div>}
            <div className="research-actions">
              {["queued", "running"].includes(session.status) && <button className="button-secondary" type="button" disabled={busy} onClick={() => void mutate("cancel")}>取消研究</button>}
              {session.retryable && <button className="button-primary" type="button" disabled={busy} onClick={() => void mutate("retry")}>恢复研究</button>}
              <Link className="button-quiet" href="/history/">查看七日历史</Link>
            </div>
          </>
        )}
      </section>
      {session?.available_result && <AvailableResultView result={session.available_result} instrumentName={session.instrument_name} instrumentId={session.instrument_id} qualityLabel={sessionResultLabel(session)} />}
    </div>
  );
}
