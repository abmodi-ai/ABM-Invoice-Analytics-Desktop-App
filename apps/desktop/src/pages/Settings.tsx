import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { Badge, Button, Card, ErrorBox, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime, num } from "../lib/format";

async function pickPath(opts: { directory?: boolean; filters?: Array<{ name: string; extensions: string[] }> }): Promise<string | null> {
  if ("__TAURI_INTERNALS__" in window) {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const r = await open({ directory: opts.directory, multiple: false, filters: opts.filters });
    return typeof r === "string" ? r : null;
  }
  return window.prompt(opts.directory ? "Folder path" : "File path");
}

export default function SettingsPage() {
  const { can } = useAuth();
  return (
    <div className="space-y-4">
      <PageHeader title="Settings" />
      <AiSettings />
      {can("ADMIN") && (
        <>
          <SignInCard />
          <WatchedFolders />
          <UsersCard />
          <BackupCard />
          <RetentionCard />
          <StartFreshCard />
        </>
      )}
    </div>
  );
}

function AiSettings() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const st = useQuery({ queryKey: ["ai-status"], queryFn: () => api.get("/ai/status"), refetchInterval: 10000 });
  const [tier, setTier] = useState<string | null>(null);
  const [model, setModel] = useState<string>("");
  const save = useMutation({
    mutationFn: () => api.post("/ai/tier", { tier: tier ?? st.data.tier, model_path: model || null }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ai-status"] }),
  });
  const importPkg = useMutation({
    mutationFn: async () => {
      const p = await pickPath({ filters: [{ name: "Model package", extensions: ["zip"] }] });
      if (!p) return null;
      return api.post("/ai/models/import", { path: p });
    },
  });
  if (st.isLoading) return <Spinner />;
  const hw = st.data.hardware;
  return (
    <Card title="Local AI">
      <p className="mb-3 text-sm text-ink-2">
        Optional. AI explains flags, triages ambiguous ones, reads messy documents and answers questions. It only proposes; decisions stay with reviewers. Everything runs on this computer.
      </p>
      <div className="mb-3 grid gap-2 text-sm md:grid-cols-4">
        <div>
          RAM <b className="num">{hw.ram_gb} GB</b>
        </div>
        <div>
          Cores <b className="num">{hw.physical_cores}</b>
        </div>
        <div>AVX2 {hw.avx2 === null ? <Badge>n/a (ARM)</Badge> : hw.avx2 ? <Badge tone="good">yes</Badge> : <Badge tone="bad">no</Badge>}</div>
        <div>
          Recommended <b>{hw.recommended_tier.toLowerCase()}</b>
        </div>
      </div>
      {can("ADMIN") ? (
        <div className="flex flex-wrap items-end gap-2">
          <Select
            label="Tier"
            value={tier ?? st.data.tier}
            onChange={(e) => setTier(e.target.value)}
            options={[
              ["OFF", "Off"],
              ["LITE", `Lite (~3 GB)${hw.eligible_tiers.includes("LITE") ? "" : " — not supported"}`],
              ["STANDARD", `Standard (~5.5 GB)${hw.eligible_tiers.includes("STANDARD") ? "" : " — not supported"}`],
              ["PLUS", `Plus (~18 GB)${hw.eligible_tiers.includes("PLUS") ? "" : " — not supported"}`],
            ]}
          />
          <Input label="Model file (in models folder)" placeholder="default for tier" value={model} onChange={(e) => setModel(e.target.value)} />
          <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending}>
            Apply
          </Button>
          <Button onClick={() => importPkg.mutate()} loading={importPkg.isPending}>
            Import model package…
          </Button>
        </div>
      ) : (
        <p className="text-sm">Current tier: {st.data.tier.toLowerCase()}</p>
      )}
      {st.data.setup && !st.data.setup.ready && (
        <p className="mt-2 text-sm text-ink-2">
          Before AI can be switched on: {st.data.setup.missing.join("; ")}. See the steps in the AI assistance panel of the review queue.
        </p>
      )}
      <ErrorBox error={save.error ?? importPkg.error} />
      {importPkg.data && <p className="mt-2 text-sm text-good-ink">Installed {importPkg.data.installed.map((m: any) => m.file).join(", ")} (hash verified).</p>}
      <p className="mt-3 text-xs text-ink-3">
        Runtime: {st.data.runtime.running ? `running ${st.data.runtime.model ?? ""}` : "stopped (starts on the first AI job, stops when idle)"} · queue {st.data.queue.queued}
      </p>
    </Card>
  );
}

function SignInCard() {
  const qc = useQueryClient();
  const s = useQuery({ queryKey: ["settings"], queryFn: () => api.get("/settings") });
  const save = useMutation({
    mutationFn: (v: boolean) => api.patch("/settings", { "security.require_login": v }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const on = !!s.data?.["security.require_login"];
  return (
    <Card title="Sign-in">
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={on} disabled={s.isLoading} onChange={(e) => save.mutate(e.target.checked)} />
        <span>
          Require users to sign in
          <span className="block text-xs text-ink-2">
            Off: the app opens straight to the dashboard as the primary admin (demo / single-user desktop). Turn on before
            using real patient data: sign-in, roles and the 15-minute idle lock then apply. Takes effect the next time the
            app starts.
          </span>
        </span>
      </label>
      <ErrorBox error={save.error} />
    </Card>
  );
}

function WatchedFolders() {
  const qc = useQueryClient();
  const s = useQuery({ queryKey: ["settings"], queryFn: () => api.get("/settings") });
  const save = useMutation({ mutationFn: (folders: string[]) => api.patch("/settings", { "ingest.watched_folders": folders }), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const folders: string[] = s.data?.["ingest.watched_folders"] ?? [];
  return (
    <Card
      title="Watched folders"
      actions={
        <Button
          size="sm"
          onClick={async () => {
            const p = await pickPath({ directory: true });
            if (p) save.mutate([...folders, p]);
          }}
        >
          Add folder
        </Button>
      }
    >
      {folders.length === 0 ? (
        <p className="text-sm text-ink-3">No folders. New files dropped into a watched folder are ingested automatically.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {folders.map((f) => (
            <li key={f} className="flex items-center justify-between">
              <span className="font-mono text-xs">{f}</span>
              <Button size="sm" variant="ghost" onClick={() => save.mutate(folders.filter((x) => x !== f))}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      <ErrorBox error={save.error} />
    </Card>
  );
}

function UsersCard() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<any[]>("/users") });
  const [f, setF] = useState({ username: "", display_name: "", role: "REVIEWER", password: "" });
  const add = useMutation({
    mutationFn: () => api.post("/users", f),
    onSuccess: () => {
      setF({ username: "", display_name: "", role: "REVIEWER", password: "" });
      qc.invalidateQueries({ queryKey: ["users"] });
    },
  });
  const patch = useMutation({ mutationFn: ({ id, body }: { id: number; body: any }) => api.patch(`/users/${id}`, body), onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }) });
  return (
    <Card title="Users and roles">
      <Table>
        <thead>
          <tr>
            <Th>Username</Th>
            <Th>Name</Th>
            <Th>Role</Th>
            <Th>Last sign-in</Th>
            <Th>Status</Th>
          </tr>
        </thead>
        <tbody>
          {users.data?.map((u) => (
            <tr key={u.id}>
              <Td>{u.username}</Td>
              <Td>{u.display_name}</Td>
              <Td>
                <select aria-label={`Role for ${u.username}`} className="h-7 rounded border border-border bg-surface text-xs" value={u.role} onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value } })}>
                  {["ADMIN", "REVIEWER", "VIEWER"].map((r) => (
                    <option key={r}>{r}</option>
                  ))}
                </select>
              </Td>
              <Td className="text-xs">{dateTime(u.last_login_at)}</Td>
              <Td>
                <Button size="sm" variant="ghost" onClick={() => patch.mutate({ id: u.id, body: { active: !u.active } })}>
                  {u.active ? "Deactivate" : "Activate"}
                </Button>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      <div className="mt-3 grid grid-cols-5 items-end gap-2">
        <Input label="Username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} />
        <Input label="Display name" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
        <Select label="Role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} options={[["REVIEWER", "Reviewer"], ["VIEWER", "Viewer"], ["ADMIN", "Admin"]]} />
        <Input label="Initial password (10+)" type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        <Button onClick={() => add.mutate()} disabled={!f.username || f.password.length < 10} loading={add.isPending}>
          Add user
        </Button>
      </div>
      <ErrorBox error={add.error ?? patch.error} />
    </Card>
  );
}

function BackupCard() {
  const [rk, setRk] = useState("");
  const backup = useMutation({
    mutationFn: async () => {
      const dir = await pickPath({ directory: true });
      return api.post("/backup", { dest_dir: dir || null, recovery_key: rk || null });
    },
  });
  const restore = useMutation({
    mutationFn: async () => {
      const p = await pickPath({ filters: [{ name: "ABM Invoice Analytics backup", extensions: ["vbak"] }] });
      if (!p) return null;
      if (!window.confirm("Restore replaces the current database. The current one is kept as a .pre-restore file. Continue?")) return null;
      return api.post("/restore", { path: p, recovery_key: rk || null });
    },
  });
  return (
    <Card title="Backup and restore">
      <p className="mb-3 text-sm text-ink-2">Backups are encrypted. Enter the recovery key to make a backup that can also be restored on a different computer.</p>
      <div className="flex flex-wrap items-end gap-2">
        <Input label="Recovery key (optional)" value={rk} onChange={(e) => setRk(e.target.value)} className="w-80 font-mono" />
        <Button variant="primary" onClick={() => backup.mutate()} loading={backup.isPending}>
          Create backup…
        </Button>
        <Button onClick={() => restore.mutate()} loading={restore.isPending}>
          Restore…
        </Button>
      </div>
      {backup.data && (
        <p className="mt-2 text-sm text-good-ink">
          Saved {backup.data.path} ({num(Math.round(backup.data.bytes / 1024))} KB).
        </p>
      )}
      {restore.data && <p className="mt-2 text-sm text-good-ink">Restored from {restore.data.restored_from}. The engine is restarting.</p>}
      <ErrorBox error={backup.error ?? restore.error} />
    </Card>
  );
}

function RetentionCard() {
  const [years, setYears] = useState("7");
  const [hard, setHard] = useState(false);
  const preview = useMutation({ mutationFn: () => api.post("/retention/preview", { years: Number(years) }) });
  const apply = useMutation({ mutationFn: () => api.post("/retention/apply", { years: Number(years), hard_delete: hard, confirm: true }) });
  return (
    <Card title="Data retention">
      <div className="flex flex-wrap items-end gap-2">
        <Input label="Keep invoices for (years)" type="number" min={1} value={years} onChange={(e) => setYears(e.target.value)} className="w-28" />
        <label className="flex items-center gap-1 text-sm">
          <input type="checkbox" checked={hard} onChange={(e) => setHard(e.target.checked)} /> Also purge patient names no longer referenced
        </label>
        <Button onClick={() => preview.mutate()} loading={preview.isPending}>
          Preview
        </Button>
        <Button
          variant="danger"
          disabled={!preview.data}
          loading={apply.isPending}
          onClick={() => window.confirm(`Remove ${preview.data?.invoices} invoices dated before ${preview.data?.cutoff}? This is audited.`) && apply.mutate()}
        >
          Apply
        </Button>
      </div>
      {preview.data && (
        <p className="mt-2 text-sm">
          {num(preview.data.invoices)} invoices before {preview.data.cutoff} ({num(preview.data.open_flags_on_them)} open flags).
        </p>
      )}
      {apply.data && <p className="mt-2 text-sm text-good-ink">Removed {num(apply.data.invoices_removed)} invoices.</p>}
      <ErrorBox error={preview.error ?? apply.error} />
    </Card>
  );
}

function StartFreshCard() {
  const [typed, setTyped] = useState("");
  const qc = useQueryClient();
  const wipe = useMutation({
    mutationFn: () => api.post("/data/delete-all", { confirm: typed }),
    onSuccess: () => {
      setTyped("");
      qc.invalidateQueries();
    },
  });
  return (
    <Card title="Start fresh">
      <p className="text-sm text-ink-2">
        Permanently delete every invoice, source document, patient/subject and flag, then start with an empty
        database. Users, settings, reference data and import templates are kept. The deletion is recorded in the
        audit log. Make a backup first if you may need the data again.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <Input label='Type DELETE to confirm' value={typed} onChange={(e) => setTyped(e.target.value)} className="w-48" />
        <Button variant="danger" disabled={typed !== "DELETE"} loading={wipe.isPending} onClick={() => wipe.mutate()}>
          Delete all invoices
        </Button>
      </div>
      {wipe.data && (
        <p className="mt-2 text-sm text-good-ink">
          Deleted {num(wipe.data.invoices)} invoices, {num(wipe.data.documents)} documents and {num(wipe.data.flags)} flags.
        </p>
      )}
      <ErrorBox error={wipe.error} />
    </Card>
  );
}
