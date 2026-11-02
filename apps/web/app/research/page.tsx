import type { Metadata } from "next";

import { ResearchClient } from "../_instrument/research-client";

export const metadata: Metadata = {
  title: "指定股票深度研究",
  description: "搜索 A 股并生成证据约束的七日研究报告。",
};

export default function ResearchPage() {
  return <ResearchClient />;
}
