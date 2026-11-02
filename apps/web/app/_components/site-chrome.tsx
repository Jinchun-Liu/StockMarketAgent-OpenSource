/* eslint-disable @next/next/no-html-link-for-pages */

import type { ReactNode } from "react";

type PageFrameProps = {
  active: "overview" | "details" | "research" | "none";
  connectionLabel: string;
  connectionTone: "online" | "static" | "offline";
  children: ReactNode;
  disclaimer?: string | undefined;
};

export function PageFrame({
  active,
  connectionLabel,
  connectionTone,
  children,
  disclaimer = "本系统仅供个人信息整理与研究，不构成投资建议。",
}: PageFrameProps) {
  return (
    <>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <header className="site-header">
        <a className="brand" href="/" aria-label="StockMarketAgent 首页">
          <span className="brand-mark" aria-hidden="true">S</span>
          <span>
            <strong>StockMarketAgent</strong>
            <small>A 股证据研究台</small>
          </span>
        </a>
        <nav className="site-nav" aria-label="主导航">
          <a href="/" aria-current={active === "overview" ? "page" : undefined}>概览</a>
          <a href="/research/" aria-current={active === "research" ? "page" : undefined}>深度研究</a>
          <a href="/details/" aria-current={active === "details" ? "page" : undefined}>为什么得到这个结果</a>
        </nav>
        <span className={`connection-pill is-${connectionTone}`}>
          <i aria-hidden="true" />
          {connectionLabel}
        </span>
      </header>
      <main id="main-content" className="site-main">{children}</main>
      <footer className="site-footer">
        <span>StockMarketAgent · 仅供研究</span>
        <p>{disclaimer}</p>
      </footer>
    </>
  );
}

export function connectionState(
  providerKind: "runtime" | "public_static",
  health: { version: string } | null,
): Pick<PageFrameProps, "connectionLabel" | "connectionTone"> {
  if (providerKind === "public_static") {
    return { connectionLabel: "公开演示快照", connectionTone: "static" };
  }
  if (health) {
    return { connectionLabel: "最新结果可用", connectionTone: "online" };
  }
  return { connectionLabel: "最新结果暂不可用", connectionTone: "offline" };
}
