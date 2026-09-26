import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, setSession, setUnauthorizedHandler } from "../api/client";

export interface User {
  user_id: number;
  username: string;
  display_name: string;
  role: "ADMIN" | "REVIEWER" | "VIEWER";
}

interface AuthState {
  user: User | null;
  /** false = the app signs in automatically as the primary admin (no login screen, no idle lock) */
  requireLogin: boolean | null;
  lockedReason: string | null;
  login: (u: string, p: string) => Promise<void>;
  logout: () => Promise<void>;
  lock: (reason?: string) => void;
  can: (role: User["role"]) => boolean;
}

const RANK = { VIEWER: 0, REVIEWER: 1, ADMIN: 2 };
const Ctx = createContext<AuthState | null>(null);

interface SessionReply {
  session_token: string;
  user: User;
  idle_seconds: number;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [requireLogin, setRequireLogin] = useState<boolean | null>(null);
  const [lockedReason, setLocked] = useState<string | null>(null);
  const idleMs = useRef(15 * 60 * 1000);
  const lastActive = useRef(Date.now());
  const lastPing = useRef(0);

  const adopt = useCallback((r: SessionReply) => {
    setSession(r.session_token);
    idleMs.current = r.idle_seconds * 1000;
    lastActive.current = Date.now();
    setLocked(null);
    setUser(r.user);
  }, []);

  const autoSignIn = useCallback(async () => {
    adopt(await api.post<SessionReply>("/auth/auto"));
  }, [adopt]);

  // Decide the mode once the engine is up; retry while it is still starting.
  useEffect(() => {
    let cancelled = false;
    const boot = async (attempt = 0): Promise<void> => {
      try {
        const m = await api.get<{ require_login: boolean }>("/auth/mode");
        if (cancelled) return;
        setRequireLogin(m.require_login);
        if (!m.require_login) await autoSignIn();
      } catch {
        if (!cancelled && attempt < 60) setTimeout(() => boot(attempt + 1), 1000);
      }
    };
    boot();
    return () => {
      cancelled = true;
    };
  }, [autoSignIn]);

  const lock = useCallback(
    (reason = "Locked") => {
      setSession(null);
      setUser(null);
      if (requireLogin === false) {
        autoSignIn().catch(() => setLocked(reason));
        return;
      }
      setLocked(reason);
    },
    [requireLogin, autoSignIn],
  );

  useEffect(() => setUnauthorizedHandler(() => lock("Your session ended. Sign in again.")), [lock]);

  // Idle lock only when sign-in is required (the engine enforces the same limit server-side).
  useEffect(() => {
    if (!user || idleMs.current === 0) return;
    const touch = () => {
      lastActive.current = Date.now();
      if (Date.now() - lastPing.current > 60_000) {
        lastPing.current = Date.now();
        api.post("/auth/ping").catch(() => undefined);
      }
    };
    const events = ["keydown", "mousedown", "wheel", "touchstart"];
    events.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    const t = setInterval(() => {
      if (Date.now() - lastActive.current > idleMs.current) lock("Locked after 15 minutes of inactivity.");
    }, 10_000);
    return () => {
      events.forEach((e) => window.removeEventListener(e, touch));
      clearInterval(t);
    };
  }, [user, lock]);

  const login = useCallback(
    async (username: string, password: string) => {
      adopt(await api.post<SessionReply>("/auth/login", { username, password }));
    },
    [adopt],
  );

  const logout = useCallback(async () => {
    await api.post("/auth/logout").catch(() => undefined);
    lock("Signed out.");
  }, [lock]);

  const can = useCallback((role: User["role"]) => !!user && RANK[user.role] >= RANK[role], [user]);

  return <Ctx.Provider value={{ user, requireLogin, lockedReason, login, logout, lock, can }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
