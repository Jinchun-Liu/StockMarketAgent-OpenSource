import type { Metadata } from "next";
import "@fontsource-variable/ibm-plex-sans/wght.css";
import "@fontsource-variable/noto-sans-sc/wght.css";
import "@fontsource/ibm-plex-mono/400.css";
import "./instrument.css";

import { AppShell } from "./_instrument/app-shell";

export const metadata: Metadata = {
  title: {
    default: "StockMarketAgent · 安静的 A 股研究仪器",
    template: "%s · StockMarketAgent",
  },
  description: "搜索一只 A 股，创建独立研究会话，并阅读证据约束的完整深度报告。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body><AppShell>{children}</AppShell></body>
    </html>
  );
}
