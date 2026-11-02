import type { Metadata } from "next";

import { HistoryClient } from "../_instrument/history-client";

export const metadata: Metadata = {
  title: "七日研究历史",
  description: "恢复、重试或提前删除七日内的私有合成研究会话。",
};

export default function HistoryPage() {
  return <HistoryClient />;
}
