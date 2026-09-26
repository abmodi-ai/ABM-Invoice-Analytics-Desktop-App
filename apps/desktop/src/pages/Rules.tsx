import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { Badge, Button, Card, ErrorBox, Modal, PageHeader, Spinner, Table, Td, Th, TierBadge } from "../components/ui";
import { useAuth } from "../lib/auth";
import { num, pct } from "../lib/format";

export default function RulesPage() {
  const q = useQuery({ queryKey: ["rules"], queryFn: () => api.get<any[]>("/rules") });
  const [edit, setEdit] = useState<any>(null);
  const qc = useQueryClient();
  const sweep = useMutation({ mutationFn: () => api.post("/detect/sweep") });
  const { can } = useAuth();
  if (q.isLoading) return <Spinner />;
  const rules = (q.data ?? []).filter((r) => r.kind !== "suppression");
  const sups = (q.data ?? []).filter((r) => r.kind === "suppression");
  return (
    <div className="space-y-4">
      <PageHeader
        title="Rules"
        subtitle="Thresholds are versioned and audited. Preview the effect of a change before applying it."
        actions={
          can("REVIEWER") && (
            <Button onClick={() => sweep.mutate()} loading={sweep.isPending}>
              Run full sweep
            </Button>
          )
        }
      />
      {sweep.isSuccess && <p className="text-sm text-ink-2">Full sweep queued (job {sweep.data.job_id}).</p>}
      <Card title="Detection rules">
        <Table>
          <thead>
            <tr>
              <Th>Rule</Th>
              <Th>Title</Th>
              <Th>Tier</Th>
              <Th>Enabled</Th>
              <Th className="text-right">Active flags</Th>
              <Th className="text-right">Precision (reviewed)</Th>
              <Th>Settings</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.rule_id}>
                <Td className="font-mono">{r.rule_id}</Td>
                <Td>
                  {r.title}
                  {r.uses_refdata.length > 0 && <div className="text-xs text-ink-3">uses {r.uses_refdata.join(", ")}</div>}
                </Td>
                <Td>{r.config.tier === "ESCALATE" ? <Badge>escalates +1</Badge> : <TierBadge tier={r.config.tier} />}</Td>
                <Td>{r.config.enabled ? <Badge tone="good">on</Badge> : <Badge>off</Badge>}</Td>
                <Td className="num text-right">{num(r.active_flags)}</Td>
                <Td className="num text-right">
                  {pct(r.precision)} <span className="text-ink-3">({r.reviewed})</span>
                </Td>
                <Td className="text-xs text-ink-2">
                  {Object.entries(r.config)
                    .filter(([k]) => !["enabled", "tier"].includes(k))
                    .map(([k, v]) => `${k}=${v}`)
                    .join(" · ") || "—"}
                  <div className="text-ink-3">v{r.config_version}</div>
                </Td>
                <Td>
                  {can("ADMIN") && (
                    <Button size="sm" onClick={() => setEdit(r)}>
                      Edit
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
      <Card title="Suppressions">
        <div className="flex flex-wrap gap-2">
          {sups.map((s) => (
            <Button key={s.rule_id} size="sm" variant={s.config.enabled ? "secondary" : "ghost"} disabled={!can("ADMIN")} onClick={() => setEdit({ ...s, title: s.rule_id })}>
              {s.rule_id} {s.config.enabled ? "on" : "off"}
            </Button>
          ))}
        </div>
      </Card>
      {edit && (
        <EditRule
          rule={edit}
          onClose={() => {
            setEdit(null);
            qc.invalidateQueries({ queryKey: ["rules"] });
          }}
        />
      )}
    </div>
  );
}

function EditRule({ rule, onClose }: { rule: any; onClose: () => void }) {
  const [cfg, setCfg] = useState<Record<string, any>>({ ...rule.config });
  const key = rule.rule_id.startsWith("SUP-") ? `suppress.${rule.rule_id}` : `rules.${rule.rule_id}`;
  const changes = Object.fromEntries(Object.entries(cfg).filter(([k, v]) => rule.config[k] !== v));
  const preview = useMutation({ mutationFn: () => api.post("/rules/preview-impact", { changes: { [key]: changes } }) });
  const save = useMutation({ mutationFn: () => api.patch(`/rules/${rule.rule_id}`, changes), onSuccess: onClose });
  return (
    <Modal open onClose={onClose} title={`${rule.rule_id} — ${rule.title}`}>
      <div className="space-y-3">
        {Object.entries(rule.config).map(([k, v]) => (
          <label key={k} className="flex items-center justify-between gap-3 text-sm">
            <span className="text-ink-2">{k.replaceAll("_", " ")}</span>
            {typeof v === "boolean" ? (
              <input type="checkbox" checked={cfg[k]} onChange={(e) => setCfg({ ...cfg, [k]: e.target.checked })} />
            ) : k === "tier" && v !== "ESCALATE" ? (
              <select className="h-8 rounded border border-border bg-surface px-2" value={cfg[k]} onChange={(e) => setCfg({ ...cfg, [k]: e.target.value })}>
                {["HARD", "PROBABLE", "WEAK", "INFO"].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            ) : (
              <input
                className="h-8 w-32 rounded border border-border bg-surface px-2 text-right"
                value={cfg[k]}
                onChange={(e) => setCfg({ ...cfg, [k]: typeof v === "number" ? Number(e.target.value) : e.target.value })}
              />
            )}
          </label>
        ))}
        {preview.data && (
          <div className="rounded border border-border bg-surface-2 p-3 text-sm">
            Currently {num(preview.data.current)} flags → {num(preview.data.proposed)}:{" "}
            <b>+{num(preview.data.added)}</b> added, <b>−{num(preview.data.removed)}</b> removed, {num(preview.data.tier_changed)} change tier.
          </div>
        )}
        <ErrorBox error={preview.error ?? save.error} />
        <div className="flex justify-end gap-2">
          <Button onClick={() => preview.mutate()} loading={preview.isPending} disabled={!Object.keys(changes).length}>
            Preview impact
          </Button>
          <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending} disabled={!Object.keys(changes).length}>
            Save
          </Button>
        </div>
        <p className="text-xs text-ink-3">Saved changes apply to new ingests immediately; run a full sweep to re-evaluate existing records.</p>
      </div>
    </Modal>
  );
}
