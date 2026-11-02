"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { PreviewIdentityProvider, usePreviewIdentity } from "@/lib/preview/identity";

function IdentityControl() {
  const { state, signIn, signOut } = usePreviewIdentity();
  const [busy, setBusy] = useState(false);

  async function act() {
    setBusy(true);
    try {
      if (state === "authenticated") await signOut();
      if (state === "anonymous") await signIn();
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      className="identity-control"
      type="button"
      disabled={busy || state === "loading" || state === "error"}
      onClick={() => void act()}
    >
      <span className={`identity-dot is-${state}`} aria-hidden="true" />
      {state === "authenticated"
        ? "退出"
        : state === "anonymous"
          ? "用户名和密码登录"
          : state === "error"
            ? "认证不可用"
            : "正在连接"}
    </button>
  );
}

function Chrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { config } = usePreviewIdentity();
  return (
    <div className="instrument-app">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <header className="topbar">
        <div className="topbar-inner">
          <Link className="brand" href="/" aria-label="StockMarketAgent 首页">
            <span className="brand-mark" aria-hidden="true">S</span>
            <span>StockMarketAgent</span>
          </Link>
          <nav className="primary-nav" aria-label="主要导航">
            <Link aria-current={pathname === "/" ? "page" : undefined} href="/">新研究</Link>
            <Link aria-current={pathname.startsWith("/history") ? "page" : undefined} href="/history/">七日历史</Link>
          </nav>
          <div className="topbar-actions">
            <span className="environment-chip">{config?.environment_label ?? "私人研究预览 · 配置待确认"}</span>
            <IdentityControl />
          </div>
        </div>
      </header>
      <main id="main-content">{children}</main>
      <footer className="site-footer">
        <p>{config?.synthetic_data === true
          ? "合成测试数据 · 仅供研究链路验证 · 不构成投资建议"
          : config?.synthetic_data === false
            ? "真实资料私人研究 · DEV_ONLY / UNVERIFIED · 不构成投资建议"
            : "私人研究预览 · 配置待确认 · 不构成投资建议"}</p>
      </footer>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <PreviewIdentityProvider>
      <Chrome>{children}</Chrome>
    </PreviewIdentityProvider>
  );
}
