// Engine API client. The desktop shell supplies the loopback URL and the per-launch token;
// in browser development they come from VITE_ENGINE_URL / VITE_ENGINE_TOKEN.
import type { components } from "./schema";

export type Schemas = components["schemas"];

export interface EngineInfo {
  url: string;
  token: string;
}

let engine: EngineInfo | null = null;
let session: string | null = null;
let onUnauthorized: (() => void) | null = null;

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export async function resolveEngine(): Promise<EngineInfo> {
  if (engine) return engine;
  if (isTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    // The shell waits for the sidecar to report its port before answering.
    engine = await invoke<EngineInfo>("engine_info");
  } else {
    const url = import.meta.env.VITE_ENGINE_URL as string | undefined;
    const token = import.meta.env.VITE_ENGINE_TOKEN as string | undefined;
    if (!url || !token) throw new Error("VITE_ENGINE_URL and VITE_ENGINE_TOKEN must be set in browser dev mode");
    engine = { url, token };
  }
  return engine;
}

export function resetEngine(): void {
  engine = null;
}

export function setSession(token: string | null): void {
  session = token;
}

export function getSession(): string | null {
  return session;
}

export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

type Query = Record<string, string | number | boolean | null | undefined>;

interface Opts {
  query?: Query;
  body?: unknown;
  form?: FormData;
  raw?: boolean;
}

function buildUrl(base: string, path: string, query?: Query): string {
  const u = new URL(path, base);
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== "") u.searchParams.set(k, String(v));
  }
  return u.toString();
}

export async function request<T = any>(method: string, path: string, opts: Opts = {}): Promise<T> {
  const eng = await resolveEngine();
  const headers: Record<string, string> = { Authorization: `Bearer ${eng.token}` };
  if (session) headers["X-IA-Session"] = session;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(buildUrl(eng.url, path, opts.query), { method, headers, body });
  if (res.status === 401 && path !== "/auth/login") {
    onUnauthorized?.();
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  if (opts.raw) return (await res.blob()) as T;
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  get: <T = any>(path: string, query?: Query) => request<T>("GET", path, { query }),
  post: <T = any>(path: string, body?: unknown) => request<T>("POST", path, { body }),
  patch: <T = any>(path: string, body?: unknown) => request<T>("PATCH", path, { body }),
  del: <T = any>(path: string) => request<T>("DELETE", path),
  upload: <T = any>(path: string, form: FormData) => request<T>("POST", path, { form }),
  blob: (path: string, query?: Query) => request<Blob>("GET", path, { query, raw: true }),
};

export async function download(path: string, filename: string, query?: Query): Promise<void> {
  const blob = await api.blob(path, query);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
