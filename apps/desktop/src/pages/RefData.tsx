import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api } from "../api/client";
import { Badge, Button, Card, ErrorBox, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime, num } from "../lib/format";

export default function RefDataPage() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const versions = useQuery({ queryKey: ["refdata"], queryFn: () => api.get<any[]>("/refdata/versions") });
  const fileRef = useRef<HTMLInputElement>(null);
  const imp = useMutation({
    mutationFn: (f: File) => {
      const fd = new FormData();
      fd.append("file", f, f.name);
      return api.upload("/refdata/import", fd);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["refdata"] }),
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Reference data"
        subtitle="CMS NCCI PTP, MUE, PFS global days and HCPCS from signed .vref bundles. Rules look up rows by each line's date of service."
        actions={
          can("ADMIN") && (
            <>
              <Button variant="primary" onClick={() => fileRef.current?.click()} loading={imp.isPending}>
                Import signed bundle
              </Button>
              <input ref={fileRef} type="file" hidden accept=".vref" onChange={(e) => e.target.files?.[0] && imp.mutate(e.target.files[0])} />
            </>
          )
        }
      />
      <ErrorBox error={imp.error} />
      {imp.data && (
        <p className="text-sm text-good-ink">
          Imported bundle {imp.data.bundle_version}: {imp.data.datasets.map((d: any) => `${d.dataset} ${d.version} (${num(d.rows)} rows)`).join(", ")}. Signature verified.
        </p>
      )}
      <Card title="Installed datasets">
        {versions.isLoading ? (
          <Spinner />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Dataset</Th>
                <Th>Version</Th>
                <Th>Effective</Th>
                <Th className="text-right">Rows</Th>
                <Th>Imported</Th>
                <Th>SHA-256</Th>
              </tr>
            </thead>
            <tbody>
              {versions.data?.map((v) => (
                <tr key={`${v.dataset}-${v.version}`}>
                  <Td>{v.dataset}</Td>
                  <Td>
                    {v.version} {v.stale && <Badge tone="warn">past effective period</Badge>}
                  </Td>
                  <Td className="text-xs">
                    {v.effective_from ?? "—"} → {v.effective_to ?? "open"}
                  </Td>
                  <Td className="num text-right">{num(v.row_count)}</Td>
                  <Td className="text-xs">{dateTime(v.imported_at)}</Td>
                  <Td className="font-mono text-xs">{v.sha256.slice(0, 12)}…</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <FrequencyLimits />
        <RecurringSeries />
      </div>
    </div>
  );
}

function FrequencyLimits() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["freq"], queryFn: () => api.get<any[]>("/refdata/frequency-limits") });
  const [f, setF] = useState({ code: "", max_count: "1", period: "YEAR", scope: "PATIENT", note: "" });
  const add = useMutation({
    mutationFn: () => api.post("/refdata/frequency-limits", { ...f, max_count: Number(f.max_count), source: "client" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["freq"] }),
  });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/refdata/frequency-limits/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["freq"] }) });
  return (
    <Card title="Frequency limits (CLN-007)">
      <Table>
        <thead>
          <tr>
            <Th>Code</Th>
            <Th>Max</Th>
            <Th>Per</Th>
            <Th>Scope</Th>
            <Th>Note</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {q.data?.map((r) => (
            <tr key={r.id}>
              <Td className="font-mono">{r.code}</Td>
              <Td className="num">{r.max_count}</Td>
              <Td>{r.period.toLowerCase()}</Td>
              <Td>{r.scope === "PATIENT" ? "patient" : "patient + provider"}</Td>
              <Td className="text-xs text-ink-2">{r.note}</Td>
              <Td>{can("ADMIN") && <Button size="sm" variant="ghost" onClick={() => del.mutate(r.id)}>Remove</Button>}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {can("ADMIN") && (
        <div className="mt-3 grid grid-cols-5 items-end gap-2">
          <Input label="Code" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />
          <Input label="Max" type="number" min={1} value={f.max_count} onChange={(e) => setF({ ...f, max_count: e.target.value })} />
          <Select label="Per" value={f.period} onChange={(e) => setF({ ...f, period: e.target.value })} options={["DAY", "WEEK", "MONTH", "YEAR", "LIFETIME"].map((p) => [p, p.toLowerCase()])} />
          <Select label="Scope" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })} options={[["PATIENT", "Patient"], ["PATIENT_PROVIDER", "Patient + provider"]]} />
          <Button onClick={() => add.mutate()} disabled={!f.code} loading={add.isPending}>
            Add
          </Button>
        </div>
      )}
      <ErrorBox error={add.error} />
    </Card>
  );
}

function RecurringSeries() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["recurring"], queryFn: () => api.get<any[]>("/refdata/recurring-series") });
  const [f, setF] = useState({ code: "", typical_frequency: "3X_WEEK", note: "" });
  const add = useMutation({ mutationFn: () => api.post("/refdata/recurring-series", f), onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring"] }) });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/refdata/recurring-series/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring"] }) });
  return (
    <Card title="Legitimate recurring series (SUP-004)">
      <Table>
        <thead>
          <tr>
            <Th>Code</Th>
            <Th>Typical frequency</Th>
            <Th>Note</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {q.data?.map((r) => (
            <tr key={r.id}>
              <Td className="font-mono">{r.code}</Td>
              <Td>{r.typical_frequency.replace("_", " ").toLowerCase()}</Td>
              <Td className="text-xs text-ink-2">{r.note}</Td>
              <Td>{can("ADMIN") && <Button size="sm" variant="ghost" onClick={() => del.mutate(r.id)}>Remove</Button>}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {can("ADMIN") && (
        <div className="mt-3 grid grid-cols-4 items-end gap-2">
          <Input label="Code" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />
          <Select
            label="Frequency"
            value={f.typical_frequency}
            onChange={(e) => setF({ ...f, typical_frequency: e.target.value })}
            options={[["DAILY", "daily"], ["2X_WEEK", "2x week"], ["3X_WEEK", "3x week"], ["WEEKLY", "weekly"], ["BIWEEKLY", "biweekly"], ["EVERY_21D", "every 21 days"], ["EVERY_28D", "every 28 days"], ["MONTHLY", "monthly"]]}
          />
          <Input label="Note" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
          <Button onClick={() => add.mutate()} disabled={!f.code} loading={add.isPending}>
            Add
          </Button>
        </div>
      )}
      <ErrorBox error={add.error} />
    </Card>
  );
}
