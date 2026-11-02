"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  User,
  UserManager,
  WebStorageStateStore,
  type UserManagerSettings,
} from "oidc-client-ts";

import { loadRuntimeConfig, type RuntimeWebConfig } from "./runtime-config";

type IdentityState = "loading" | "anonymous" | "authenticated" | "error";

interface PreviewIdentityContext {
  state: IdentityState;
  accessToken: string | null;
  config: RuntimeWebConfig | null;
  error: string | null;
  signIn: (returnTo?: string) => Promise<void>;
  signOut: () => Promise<void>;
  completeCallback: () => Promise<string>;
}

const IdentityContext = createContext<PreviewIdentityContext | null>(null);

function managerSettings(config: RuntimeWebConfig): UserManagerSettings {
  const origin = window.location.origin;
  return {
    authority: config.issuer,
    client_id: config.client_id,
    redirect_uri: `${origin}/auth/callback/`,
    post_logout_redirect_uri: `${origin}/`,
    response_type: "code",
    scope: config.scope,
    loadUserInfo: false,
    monitorSession: false,
    automaticSilentRenew: false,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    metadata: {
      issuer: config.issuer,
      authorization_endpoint: config.authorization_endpoint,
      token_endpoint: config.token_endpoint,
      end_session_endpoint: config.end_session_endpoint,
    },
  };
}

function usableUser(user: User | null): user is User {
  return user !== null && !user.expired && user.access_token.length > 0;
}

function safeLocalReturn(value: string): string {
  if (!value.startsWith("/") || value.startsWith("//")) return "/";
  try {
    const target = new URL(value, window.location.origin);
    if (target.origin !== window.location.origin) return "/";
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return "/";
  }
}

export function PreviewIdentityProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<IdentityState>("loading");
  const [config, setConfig] = useState<RuntimeWebConfig | null>(null);
  const [manager, setManager] = useState<UserManager | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void loadRuntimeConfig()
      .then((loaded) => {
        if (!active) return;
        const nextManager = new UserManager(managerSettings(loaded));
        const clearUser = () => {
          setUser(null);
          setState("anonymous");
        };
        nextManager.events.addAccessTokenExpired(clearUser);
        nextManager.events.addUserUnloaded(clearUser);
        setConfig(loaded);
        setManager(nextManager);
        return nextManager.getUser();
      })
      .then((storedUser) => {
        if (!active || storedUser === undefined) return;
        if (usableUser(storedUser)) {
          setUser(storedUser);
          setState("authenticated");
        } else {
          setUser(null);
          setState("anonymous");
        }
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setError(caught instanceof Error ? caught.message : "IDENTITY_INITIALIZATION_FAILED");
        setState("error");
      });
    return () => {
      active = false;
    };
  }, []);

  const signIn = useCallback(
    async (returnTo = window.location.pathname + window.location.search) => {
      if (!manager) throw new Error("IDENTITY_NOT_READY");
      const safeReturn = safeLocalReturn(returnTo);
      window.sessionStorage.setItem("sma.r2.return-to", safeReturn);
      await manager.signinRedirect();
    },
    [manager],
  );

  const signOut = useCallback(async () => {
    if (!manager) throw new Error("IDENTITY_NOT_READY");
    await manager.signoutRedirect();
  }, [manager]);

  const completeCallback = useCallback(async () => {
    if (!manager) throw new Error("IDENTITY_NOT_READY");
    const callbackUser = await manager.signinRedirectCallback();
    if (!usableUser(callbackUser)) throw new Error("IDENTITY_CALLBACK_INVALID");
    setUser(callbackUser);
    setState("authenticated");
    const returnTo = window.sessionStorage.getItem("sma.r2.return-to") ?? "/";
    window.sessionStorage.removeItem("sma.r2.return-to");
    return safeLocalReturn(returnTo);
  }, [manager]);

  const value = useMemo<PreviewIdentityContext>(
    () => ({
      state,
      accessToken: usableUser(user) ? user.access_token : null,
      config,
      error,
      signIn,
      signOut,
      completeCallback,
    }),
    [completeCallback, config, error, signIn, signOut, state, user],
  );

  return <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>;
}

export function usePreviewIdentity(): PreviewIdentityContext {
  const context = useContext(IdentityContext);
  if (!context) throw new Error("PreviewIdentityProvider is missing");
  return context;
}
