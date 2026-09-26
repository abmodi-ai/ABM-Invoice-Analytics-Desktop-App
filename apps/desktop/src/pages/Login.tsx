import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { Button, Card, ErrorBox, Input, Spinner } from "../components/ui";
import { useAuth } from "../lib/auth";

function Setup({ onDone }: { onDone: (key: string | null) => void }) {
  const [f, setF] = useState({ username: "admin", display_name: "", password: "", confirm: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (f.password !== f.confirm) return setErr(new Error("Passwords do not match"));
    setBusy(true);
    try {
      const r = await api.post("/setup/admin", { username: f.username, display_name: f.display_name || f.username, password: f.password, create_recovery_key: true });
      onDone(r.recovery_key);
    } catch (x) {
      setErr(x);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-3">
      <p className="text-sm text-ink-2">First run: create the administrator account. Passwords need at least 10 characters.</p>
      <Input label="Username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required />
      <Input label="Display name" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
      <Input label="Password" type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required minLength={10} />
      <Input label="Confirm password" type="password" value={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.value })} required />
      <ErrorBox error={err} />
      <Button variant="primary" type="submit" loading={busy} className="w-full">
        Create administrator
      </Button>
    </form>
  );
}

export default function LoginPage() {
  const { login, lockedReason } = useAuth();
  const status = useQuery({ queryKey: ["setup-status"], queryFn: () => api.get("/setup/status"), retry: 30, retryDelay: 1000 });
  const [recovery, setRecovery] = useState<string | null>(null);
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await login(u, p);
    } catch (x) {
      setErr(x);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full items-center justify-center bg-surface-2 p-6">
      <div className="w-[min(420px,100%)] space-y-4">
        <div className="text-center">
          <h1 className="text-2xl font-semibold">Verismo Invoice Tracker</h1>
          <p className="text-sm text-ink-2">Offline duplicate-billing detection</p>
        </div>
        <Card>
          {status.isLoading && <Spinner label="Starting engine" />}
          {status.error && <ErrorBox error={status.error} />}
          {recovery && (
            <div className="mb-4 space-y-2 rounded-md border border-warning/60 bg-warning/10 p-3 text-sm">
              <p className="font-medium">Recovery key (shown once)</p>
              <p className="text-ink-2">Print it and store it somewhere safe. It is the only way to restore a backup on a new machine.</p>
              <code className="block rounded bg-surface p-2 text-center font-mono text-base tracking-wide">{recovery}</code>
              <Button size="sm" onClick={() => window.print()}>
                Print
              </Button>
            </div>
          )}
          {status.data?.needs_admin && !recovery ? (
            <Setup
              onDone={(k) => {
                setRecovery(k);
                status.refetch();
              }}
            />
          ) : (
            status.data && (
              <form onSubmit={submit} className="space-y-3">
                {lockedReason && <p className="text-sm text-ink-2">{lockedReason}</p>}
                <Input label="Username" value={u} onChange={(e) => setU(e.target.value)} autoFocus required autoComplete="username" />
                <Input label="Password" type="password" value={p} onChange={(e) => setP(e.target.value)} required autoComplete="current-password" />
                <ErrorBox error={err} />
                <Button variant="primary" type="submit" loading={busy} className="w-full">
                  Sign in
                </Button>
              </form>
            )
          )}
        </Card>
        <p className="text-center text-xs text-ink-3">All data stays on this computer. No network connections are made.</p>
      </div>
    </div>
  );
}
