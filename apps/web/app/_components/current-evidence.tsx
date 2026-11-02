"use client";

import { useSyncExternalStore } from "react";

import { PlainEvidencePage } from "./plain-evidence";
import { useDisplayedSnapshot } from "./snapshot-status";

const EVIDENCE_PATH = /^\/evidence\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/;
const subscribeToPathname = () => () => undefined;

export function CurrentEvidencePage() {
  const { state, evidenceByEventId } = useDisplayedSnapshot();
  const pathname = useSyncExternalStore(
    subscribeToPathname,
    () => window.location.pathname,
    () => null,
  );
  const eventId = pathname ? EVIDENCE_PATH.exec(pathname)?.[1] ?? null : null;
  const event = eventId ? evidenceByEventId[eventId] ?? null : null;

  if (state.phase === "loading" || pathname === null) {
    return (
      <main className="page-shell">
        <section className="data-callout" aria-live="polite">
          <div><h1>正在检查这条证据</h1><p>页面会先核对当前快照，再展示可追溯内容。</p></div>
        </section>
      </main>
    );
  }

  return <PlainEvidencePage event={event} />;
}
