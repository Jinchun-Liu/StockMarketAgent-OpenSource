"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  createResearch,
  getResearchSkills,
  listSessions,
  searchInstruments,
  type SearchMatch,
  type Session,
  type SkillCatalog,
  type SkillId,
} from "@/lib/api/research-v2";
import { usePreviewIdentity } from "@/lib/preview/identity";

import { formatTime, listingLabels, sessionResultLabel } from "./presentation";

export function HomeClient() {
  const { state, config, accessToken, signIn, error: identityError } = usePreviewIdentity();
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<SearchMatch[]>([]);
  const [selected, setSelected] = useState<SearchMatch | null>(null);
  const [recent, setRecent] = useState<Session[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [skillCatalog, setSkillCatalog] = useState<SkillCatalog | null>(null);
  const [skillMode, setSkillMode] = useState<"auto" | "manual">("auto");
  const [selectedSkills, setSelectedSkills] = useState<SkillId[]>([]);
  const [skillMenuOpen, setSkillMenuOpen] = useState(false);

  const refreshRecent = useCallback(async (signal?: AbortSignal) => {
    if (!config || !accessToken) return;
    const page = await listSessions(config, accessToken, undefined, signal);
    setRecent((page.items ?? []).slice(0, 4));
  }, [accessToken, config]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void refreshRecent(controller.signal).catch((caught: unknown) => {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          setError(caught instanceof Error ? caught.message : "无法读取最近研究");
        }
      });
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [refreshRecent]);

  useEffect(() => {
    if (!selected || !config || !accessToken) return;
    const controller = new AbortController();
    void getResearchSkills(
      config,
      accessToken,
      selected.instrument_id,
      controller.signal,
    ).then(setSkillCatalog).catch((caught: unknown) => {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) {
        setError(caught instanceof Error ? caught.message : "无法读取研究方法");
      }
    });
    return () => controller.abort();
  }, [accessToken, config, selected]);

  const visibleSkillCatalog =
    selected && skillCatalog?.instrument_id === selected.instrument_id ? skillCatalog : null;

  async function submitSearch(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!config || !accessToken || !query.trim()) return;
    setBusy(true);
    setError(null);
    setSelected(null);
    try {
      const result = await searchInstruments(config, accessToken, query);
      setMatches(result.matches ?? []);
      setActiveIndex(0);
      setSearched(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "搜索失败");
    } finally {
      setBusy(false);
    }
  }

  function navigateResults(event: React.KeyboardEvent<HTMLInputElement>) {
    if (matches.length === 0) return;
    if (event.code === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((current) => (current + 1) % matches.length);
    }
    if (event.code === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) => (current - 1 + matches.length) % matches.length);
    }
    if (event.code === "Enter" && searched && matches[activeIndex]) {
      event.preventDefault();
      setSelected(matches[activeIndex]);
    }
  }

  async function create() {
    if (!selected || !config || !accessToken) return;
    setBusy(true);
    setError(null);
    try {
      const accepted = await createResearch(config, accessToken, selected.instrument_id, {
        schema_version: "1.0",
        mode: skillMode,
        selected_skill_ids: skillMode === "manual" ? selectedSkills : [],
      });
      window.location.assign(`/research/?session=${encodeURIComponent(accepted.session_id)}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法创建研究会话");
      setBusy(false);
    }
  }

  return (
    <div className="home-stage page-enter">
      <header className="hero-copy">
        <p className="eyebrow">EVIDENCE-GROUNDED A-SHARE RESEARCH</p>
        <h1>从一只股票开始，安静地把证据看完整。</h1>
        <p>搜索沪深北 A 股，确认唯一证券身份。系统回溯五年与二十个季度，并把国际事件、Bull / Bear、未知项和反证收拢成一份可下钻的报告。</p>
      </header>

      {state === "loading" && (
        <section className="login-panel" aria-live="polite">
          <h2>正在建立私有会话</h2>
          <p>读取无密钥运行配置，并确认浏览器中的登录状态。</p>
        </section>
      )}

      {state === "error" && (
        <div className="error-banner" role="alert">认证环境不可用：{identityError ?? "未知配置错误"}</div>
      )}

      {state === "anonymous" && (
        <section className="login-panel">
          <h2>使用用户名和密码登录</h2>
          <p>登录密码由 AWS Cognito 校验，StockMarketAgent 后端不接收或保存密码，也不保存邮箱。</p>
          <button className="button-primary" type="button" onClick={() => void signIn("/")}>用户名和密码登录</button>
        </section>
      )}

      {state === "authenticated" && (
        <>
          <section className="search-instrument" aria-label="A 股搜索与确认">
            <p className="search-coverage-note">覆盖沪深北当前 A 股与沪深历史退市目录。目录为开发环境离线快照；简称可搜索，公司全称仅在已核验时显示。</p>
            <form className="search-form" role="search" onSubmit={(event) => void submitSearch(event)}>
              <label className="search-label">
                <span>证券代码、简称或公司全称</span>
                <input
                  type="search"
                  value={query}
                  placeholder="例如 603986、兆易创新"
                  autoComplete="off"
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setMatches([]);
                    setSelected(null);
                    setSearched(false);
                  }}
                  onKeyDown={navigateResults}
                  role="combobox"
                  aria-autocomplete="list"
                  aria-expanded={searched && matches.length > 0}
                  aria-haspopup="listbox"
                  aria-controls="instrument-results"
                  aria-activedescendant={
                    searched && matches[activeIndex] ? `instrument-${activeIndex}` : undefined
                  }
                />
              </label>
              <button className="button-primary search-submit" type="submit" disabled={busy || !query.trim()}>{busy ? "正在检索" : "搜索 A 股"}</button>
            </form>

            <ul id="instrument-results" className="search-results" role="listbox" aria-label="证券候选">
              {searched && matches.map((match, index) => (
                <li key={match.instrument_id}>
                  <button
                    id={`instrument-${index}`}
                    className={`search-option${activeIndex === index ? " is-active" : ""}`}
                    type="button"
                    role="option"
                    aria-selected={selected?.instrument_id === match.instrument_id}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => setSelected(match)}
                  >
                    <span>
                      <strong>{match.short_name}</strong>
                      <small>{match.legal_name_verified ? match.legal_name : "公司全称将在研究阶段核验"}</small>
                    </span>
                    <span className="search-code">{match.instrument_id}</span>
                    <span className={`listing-label is-${match.listing_status}`}>{listingLabels[match.listing_status] ?? "状态未知"}</span>
                  </button>
                </li>
              ))}
              {searched && matches.length === 0 && <li className="empty-search">未找到可确认的 A 股证券。请检查代码或使用公司全称。</li>}
            </ul>

            {selected && (
              <div className="instrument-confirmation">
                <div>
                  <p className="eyebrow">确认唯一证券</p>
                  <h2>{selected.short_name}</h2>
                  <p>{selected.instrument_id} · {selected.legal_name_verified ? selected.legal_name : "公司全称待研究核验"} · {listingLabels[selected.listing_status] ?? "状态未知"}</p>
                </div>
                <div className="confirmation-actions">
                  <button className="button-secondary" type="button" onClick={() => setSelected(null)}>重新选择</button>
                  <button className="button-primary" type="button" disabled={busy} onClick={() => void create()}>创建深度研究</button>
                </div>
                <div className="method-control">
                  <button
                    className="method-menu-toggle"
                    type="button"
                    aria-expanded={skillMenuOpen}
                    aria-controls="research-method-menu"
                    onClick={() => setSkillMenuOpen((open) => !open)}
                  >
                    <span>研究方法</span>
                    <strong>{skillMode === "auto" ? "系统自动选择" : `核心方法 + ${selectedSkills.length} 项`}</strong>
                  </button>
                  {skillMenuOpen && visibleSkillCatalog && (
                    <div id="research-method-menu" className="method-menu">
                      <div className="method-mode" role="radiogroup" aria-label="方法选择模式">
                        <button type="button" role="radio" aria-checked={skillMode === "auto"} onClick={() => setSkillMode("auto")}>自动选择 <small>推荐</small></button>
                        <button type="button" role="radio" aria-checked={skillMode === "manual"} onClick={() => setSkillMode("manual")}>手动增加</button>
                      </div>
                      <p>九项核心检查始终执行。手动模式最多增加六项；不可用方法保留说明，但不能绕过边界。</p>
                      <ul className="method-list">
                        {visibleSkillCatalog.items.map((item) => {
                          const checked = item.is_core || selectedSkills.includes(item.skill_id);
                          const unavailable = item.availability !== "available";
                          return (
                            <li key={item.skill_id} className={unavailable ? "is-disabled" : undefined}>
                              <label>
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  disabled={item.is_core || unavailable || skillMode !== "manual"}
                                  onChange={() => {
                                    setSelectedSkills((current) => current.includes(item.skill_id)
                                      ? current.filter((value) => value !== item.skill_id)
                                      : current.length < 6 ? [...current, item.skill_id] : current);
                                  }}
                                />
                                <span><strong>{item.display_name}</strong><small>{item.plain_language_purpose}</small></span>
                                <em>{item.is_core ? "核心" : unavailable ? "不可用" : "可选"}</em>
                              </label>
                              {item.disabled_reason && <p>{item.disabled_reason}</p>}
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  )}
                </div>
              </div>
            )}
          </section>

          <section className="recent-strip" aria-labelledby="recent-title">
            <div>
              <h2 id="recent-title">最近研究</h2>
              <p>七日内可恢复、重试或删除。</p>
            </div>
            {recent.length === 0 ? (
              <p className="muted">还没有研究会话。完成上方确认即可开始。</p>
            ) : (
              <ul className="session-list">
                {recent.map((session) => (
                  <li key={session.session_id}>
                    <Link className="session-link" href={`/research/?session=${session.session_id}`}>
                      <span><strong>{session.instrument_name}</strong><small>{session.instrument_id}</small></span>
                      <span className="status-chip">{sessionResultLabel(session)}</span>
                      <small>{formatTime(session.updated_at)}</small>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      {error && <div className="error-banner" role="alert">{error}</div>}
    </div>
  );
}
