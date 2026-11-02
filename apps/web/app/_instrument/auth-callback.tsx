"use client";

import { useEffect, useRef, useState } from "react";

import { usePreviewIdentity } from "@/lib/preview/identity";

export function AuthCallback() {
  const { state, config, completeCallback } = usePreviewIdentity();
  const attempted = useRef(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (state === "loading" || !config || attempted.current) return;
    attempted.current = true;
    void completeCallback()
      .then((returnTo) => window.location.replace(returnTo))
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "IDENTITY_CALLBACK_FAILED");
      });
  }, [completeCallback, config, state]);

  return (
    <section className="callback-panel page-enter" aria-live="polite">
      <p className="eyebrow">SECURE CALLBACK</p>
      <h1>{error ? "登录回调未通过" : "正在完成安全登录"}</h1>
      <p>{error ? `失败关闭：${error}` : "正在验证 PKCE state、授权码和浏览器会话。"}</p>
    </section>
  );
}
