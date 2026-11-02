"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { listSessions, mutateSession, type Session } from "@/lib/api/research-v2";
import { usePreviewIdentity } from "@/lib/preview/identity";

import { formatTime, sessionResultLabel } from "./presentation";

export function HistoryClient() {
  const { state, config, accessToken, signIn, error: identityError } = usePreviewIdentity();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [cursor, setCursor] = useState<string | undefined>();
  const [backStack, setBackStack] = useState<Array<string | undefined>>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Session | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const deleteDialogRef = useRef<HTMLElement>(null);
  const deleteCloseRef = useRef<HTMLButtonElement>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!config || !accessToken) return;
    const page = await listSessions(config, accessToken, cursor, signal);
    setSessions(page.items ?? []);
    setNextCursor(page.next_cursor ?? null);
  }, [accessToken, config, cursor]);

  useEffect(() => {
    if (state !== "authenticated") return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void load(controller.signal).catch((caught: unknown) => {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          setError(caught instanceof Error ? caught.message : "无法读取历史研究");
        }
      });
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [load, state]);

  useEffect(() => {
    if (!deleteTarget) return;
    const prior = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code === "Escape") {
        setDeleteTarget(null);
        return;
      }
      if (event.code !== "Tab" || !deleteDialogRef.current) return;
      const focusable = Array.from(
        deleteDialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
        ),
      );
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.body.style.overflow = "hidden";
    deleteCloseRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = "";
      prior?.focus();
    };
  }, [deleteTarget]);

  async function mutate(session: Session, operation: "retry" | "delete") {
    if (!config || !accessToken) return;
    setBusyId(session.session_id);
    setError(null);
    try {
      await mutateSession(config, accessToken, session.session_id, operation);
      setDeleteTarget(null);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  }

  function nextPage() {
    if (!nextCursor) return;
    setBackStack((current) => [...current, cursor]);
    setCursor(nextCursor);
  }

  function previousPage() {
    const prior = backStack.at(-1);
    setBackStack((current) => current.slice(0, -1));
    setCursor(prior);
  }

  if (state === "anonymous") {
    return (
      <div className="content-page page-enter">
        <section className="login-panel">
          <h1>登录后查看七日历史</h1>
          <p>列表、报告和变更操作都会按当前主体过滤，其他用户的会话不可枚举。</p>
          <button className="button-primary" type="button" onClick={() => void signIn("/history/")}>用户名和密码登录</button>
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

  return (
    <div className="content-page page-enter">
      <header className="page-heading">
        <p className="eyebrow">SEVEN-DAY RESEARCH HISTORY</p>
        <h1>七日研究历史</h1>
        <p>恢复未完成的研究、重试可恢复故障，或提前删除完整报告与临时上下文。</p>
      </header>

      {error && <div className="error-banner" role="alert">{error}</div>}

      <section className="history-surface" aria-label="研究会话列表">
        {state === "loading" && <p className="empty-state">正在读取私有会话…</p>}
        {state === "authenticated" && sessions.length === 0 && (
          <div className="empty-state">
            <h2>七日内没有研究</h2>
            <p>从一只 A 股开始创建第一份证据约束报告。</p>
            <Link className="button-primary" href="/">开始新研究</Link>
          </div>
        )}
        {sessions.map((session) => (
          <article className="history-row" key={session.session_id}>
            <div className="history-identity">
              <Link href={`/research/?session=${session.session_id}`}>{session.instrument_name}</Link>
              <small>{session.instrument_id} · {session.progress.message}</small>
            </div>
            <div className="history-status">
              <span className="status-chip">{sessionResultLabel(session)}</span>
            </div>
            <div className="history-time">
              <small>最近进展</small>
              {formatTime(session.progress.latest_progress_at)}
            </div>
            <div className="history-actions">
              <Link
                className={session.status === "succeeded" ? "button-secondary" : "button-quiet"}
                href={`/research/?session=${session.session_id}`}
              >
                {session.status === "succeeded"
                  ? "打开研究结果"
                  : session.status === "queued" || session.status === "running"
                    ? "查看进度"
                    : session.available_result
                      ? "查看可用结果"
                    : session.retryable
                      ? "查看并恢复"
                      : "查看状态"}
              </Link>
              {session.retryable && (
                <button className="button-quiet" type="button" disabled={busyId === session.session_id} onClick={() => void mutate(session, "retry")}>重试</button>
              )}
              <button className="button-danger" type="button" disabled={busyId === session.session_id} onClick={() => setDeleteTarget(session)}>删除</button>
            </div>
          </article>
        ))}
      </section>

      {(backStack.length > 0 || nextCursor) && (
        <nav className="pagination" aria-label="历史分页">
          <button className="button-secondary" type="button" disabled={backStack.length === 0} onClick={previousPage}>上一页</button>
          <button className="button-secondary" type="button" disabled={!nextCursor} onClick={nextPage}>下一页</button>
        </nav>
      )}

      {deleteTarget && (
        <>
          <button className="evidence-lens-backdrop" type="button" aria-label="取消删除" onClick={() => setDeleteTarget(null)} />
          <aside ref={deleteDialogRef} className="evidence-lens" role="dialog" aria-modal="true" aria-labelledby="delete-title">
            <header className="lens-header">
              <strong>提前删除研究</strong>
              <button ref={deleteCloseRef} className="lens-close" type="button" aria-label="关闭" onClick={() => setDeleteTarget(null)}>×</button>
            </header>
            <div className="lens-content">
              <h2 id="delete-title">删除 {deleteTarget.instrument_name}？</h2>
              <p>报告将立即不可访问，正文、临时上下文、embedding 和对象制品进入 24 小时清理队列。最小审计回执仍会保留。</p>
              <button className="button-danger" type="button" disabled={busyId === deleteTarget.session_id} onClick={() => void mutate(deleteTarget, "delete")}>确认删除</button>
            </div>
          </aside>
        </>
      )}
    </div>
  );
}
