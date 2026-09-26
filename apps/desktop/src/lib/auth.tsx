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
  lockedReason: string | null;
  login: (u: string, p: string) => Promise<void>;
  logout: () => Promise<void>;
  lock: (reason?: string) => void;
  can: (role: User["role"]) => boolean;
}

const RANK = { VIEWER: 0, REVIEWER: 1, ADMIN: 2 };
const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [lockedReason, setLocked] = useState<string | null>(null);
  const idleMs = useRef(15 * 60 * 1000);
  const lastActive = useRef(Date.now());
  const lastPing = useRef(0);

  const lock = useCallback((reason = "Locked") => {
    setSession(null);
    setUser(null);
    setLocked(reason);
  }, []);

  useEffect(() => setUnauthorizedHandler(() => lock("Your session ended. Sign in again.")), [lock]);

  // Auto-lock after inactivity (the engine enforces the same idle limit server-side).
  useEffect(() => {
    if (!user) return;
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

  const login = useCallback(async (username: string, password: string) => {
    const r = await api.post<{ session_token: string; user: User; idle_seconds: number }>("/auth/login", { username, password });
    setSession(r.session_token);
    idleMs.current = r.idle_seconds * 1000;
    lastActive.current = Date.now();
    setLocked(null);
    setUser(r.user);
  }, []);

  const logout = useCallback(async () => {
    await api.post("/auth/logout").catch(() => undefined);
    lock("Signed out.");
  }, [lock]);

  const can = useCallback((role: User["role"]) => !!user && RANK[user.role] >= RANK[role], [user]);

  return <Ctx.Provider value={{ user, lockedReason, login, logout, lock, can }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
