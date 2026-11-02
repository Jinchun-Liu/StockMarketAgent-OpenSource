"use client";

import { ResearchOverview } from "./research-overview";

/** Compatibility export for callers that still use the former component name. */
export function LiveOverview() {
  return <ResearchOverview />;
}
