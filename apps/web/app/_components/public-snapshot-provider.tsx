"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import type { HistoricalReplaySnapshot } from "@/lib/data/historical-replay";
import {
  loadCurrentPublicSnapshot,
  PublicSnapshotLoadError,
  type LoadedPublicSnapshot,
  type SnapshotLoadErrorCode,
} from "@/lib/data/public-snapshot-loader";

export type SnapshotPhase =
  | "loading"
  | "live"
  | "partial"
  | "stale"
  | "unavailable"
  | "historical_fallback"
  | "error";

export type PublicSnapshotState = Readonly<{
  phase: SnapshotPhase;
  current: LoadedPublicSnapshot | null;
  fallback: HistoricalReplaySnapshot | null;
  errorCode: SnapshotLoadErrorCode | null;
  refresh: () => void;
}>;

const PublicSnapshotContext = createContext<PublicSnapshotState | null>(null);

export function classifySnapshot(
  snapshot: LoadedPublicSnapshot,
  now: number,
): Exclude<SnapshotPhase, "loading" | "error"> {
  if (snapshot.manifest.origin_mode === "demo_replay") return "historical_fallback";
  if (snapshot.manifest.completeness === "unavailable") return "unavailable";
  if (
    Date.parse(snapshot.manifest.fresh_until) <= now ||
    snapshot.runStatus.capabilities.some((item) => item.state === "stale")
  ) return "stale";
  if (
    snapshot.manifest.completeness === "partial" ||
    snapshot.runStatus.status === "degraded"
  ) return "partial";
  return "live";
}

type PublicSnapshotProviderProps = Readonly<{
  fallback: HistoricalReplaySnapshot | null;
  enableDynamicLoad: boolean;
  children: ReactNode;
}>;

export function PublicSnapshotProvider({
  fallback,
  enableDynamicLoad,
  children,
}: PublicSnapshotProviderProps) {
  const [current, setCurrent] = useState<LoadedPublicSnapshot | null>(null);
  const [phase, setPhase] = useState<SnapshotPhase>(
    enableDynamicLoad ? "loading" : fallback ? "historical_fallback" : "error",
  );
  const [errorCode, setErrorCode] = useState<SnapshotLoadErrorCode | null>(null);
  const [requestVersion, setRequestVersion] = useState(0);
  const refresh = useCallback(() => {
    setPhase("loading");
    setErrorCode(null);
    setRequestVersion((value) => value + 1);
  }, []);

  useEffect(() => {
    if (!enableDynamicLoad) return;
    let active = true;
    void loadCurrentPublicSnapshot().then((snapshot) => {
      if (!active) return;
      setCurrent(snapshot);
      setPhase(classifySnapshot(snapshot, Date.now()));
    }).catch((error: unknown) => {
      if (!active) return;
      setCurrent(null);
      setErrorCode(error instanceof PublicSnapshotLoadError ? error.code : "INVALID_ASSET");
      setPhase("error");
    });
    return () => {
      active = false;
    };
  }, [enableDynamicLoad, requestVersion]);

  const value = useMemo<PublicSnapshotState>(() => ({
    phase,
    current,
    fallback,
    errorCode,
    refresh,
  }), [current, errorCode, fallback, phase, refresh]);
  return (
    <PublicSnapshotContext.Provider value={value}>
      {children}
    </PublicSnapshotContext.Provider>
  );
}

export function usePublicSnapshot(): PublicSnapshotState {
  const value = useContext(PublicSnapshotContext);
  if (!value) throw new Error("PUBLIC_SNAPSHOT_PROVIDER_REQUIRED");
  return value;
}
